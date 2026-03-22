import os
import sqlite3
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Optional

import requests
from flask import Flask, request, jsonify
from logging.handlers import RotatingFileHandler

# ==================== KONFIGURIMI ====================
app = Flask(__name__)

# Konfigurimi i logging
if not os.path.exists('logs'):
    os.makedirs('logs')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

file_handler = RotatingFileHandler(
    'logs/bot.log', 
    maxBytes=10485760,
    backupCount=5
)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
))
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s'
))
logger.addHandler(console_handler)

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN is not configured!")

# ==================== DATABASE ====================
class Database:
    def __init__(self, db_path: str = 'bot_data.db'):
        self.db_path = db_path
        self._init_db()
    
    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS filters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT,
                    keyword TEXT,
                    response TEXT,
                    is_photo BOOLEAN DEFAULT 0,
                    is_gif BOOLEAN DEFAULT 0,
                    media_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(chat_id, keyword)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS groups (
                    chat_id TEXT PRIMARY KEY,
                    chat_title TEXT,
                    language TEXT DEFAULT 'sq'
                )
            ''')
            
            conn.commit()
            logger.info("Database initialized")

db = Database()

# ==================== FUNKSIONET ====================
def send_message(chat_id: int, text: str, reply_to_message_id: Optional[int] = None):
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.ok
    except Exception as e:
        logger.error(f"Error: {e}")
        return False

def send_photo(chat_id: int, photo_url: str, caption: str = None, reply_to_message_id: Optional[int] = None):
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    payload = {'chat_id': chat_id, 'photo': photo_url}
    if caption:
        payload['caption'] = caption
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.ok
    except Exception as e:
        logger.error(f"Error: {e}")
        return False

def send_gif(chat_id: int, gif_url: str, caption: str = None, reply_to_message_id: Optional[int] = None):
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendAnimation"
    payload = {'chat_id': chat_id, 'animation': gif_url}
    if caption:
        payload['caption'] = caption
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.ok
    except Exception as e:
        logger.error(f"Error: {e}")
        return False

def is_admin(chat_id: int, user_id: int):
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/getChatAdministrators"
    try:
        response = requests.post(url, json={'chat_id': chat_id}, timeout=10)
        if response.ok:
            admins = [a['user']['id'] for a in response.json().get('result', [])]
            return user_id in admins
    except Exception as e:
        logger.error(f"Error: {e}")
    return False

# ==================== LANGUAGES ====================
TEXTS = {
    'sq': {
        'admin_only': "👑 Vetëm administratorët!",
        'group_only': "⚠️ Funksionon vetëm në grupe!",
        'filter_set': "✅ Filtri për '{word}' u vendos!",
        'filter_set_photo': "✅ Filtri me foto për '{word}' u vendos!",
        'filter_set_gif': "✅ Filtri me GIF për '{word}' u vendos!",
        'filter_deleted': "✅ Filtri për '{word}' u fshi!",
        'no_filters': "ℹ️ Nuk ka filtra.",
        'filters_list': "🔍 **Filtrat:**\n\n",
        'need_reply': "⚠️ Përgjigjuni mesazhit!",
        'error': "❌ Gabim!",
    }
}

# ==================== ENDPOINTI ====================
@app.route('/', methods=['GET', 'POST'])
def index():
    # GET request - status check
    if request.method == 'GET':
        return jsonify({
            'status': 'running',
            'token_configured': bool(TOKEN),
            'version': '1.0'
        })
    
    # POST request - webhook
    try:
        # Check if we have JSON
        if not request.is_json:
            return jsonify({'ok': True})
        
        update = request.get_json()
        if not update or 'message' not in update:
            return jsonify({'ok': True})
        
        msg = update['message']
        chat_id = msg['chat']['id']
        msg_id = msg.get('message_id')
        text = msg.get('text', '')
        user_id = msg.get('from', {}).get('id')
        chat_type = msg.get('chat', {}).get('type')
        chat_id_str = str(chat_id)
        
        # Register group
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO groups (chat_id, chat_title)
                VALUES (?, ?)
            ''', (chat_id_str, msg['chat'].get('title', 'Unknown')))
        
        # New members
        if 'new_chat_members' in msg:
            for member in msg['new_chat_members']:
                if member.get('is_bot'):
                    continue
                name = member.get('first_name', 'User')
                send_message(chat_id, f"👋 Welcome {name}!", reply_to_message_id=msg_id)
            return jsonify({'ok': True})
        
        # Commands
        if text and text.startswith('/'):
            parts = text.split()
            cmd = parts[0].lower()
            args = parts[1:]
            
            # Help
            if cmd == '/start' or cmd == '/help':
                help_text = (
                    "🤖 **Rose Bot**\n\n"
                    "**Commands:**\n"
                    "/filter <word> <reply> - Set text filter\n"
                    "/filter <word> photo:<url> - Set photo filter\n"
                    "/filter <word> gif:<url> - Set GIF filter\n"
                    "/stop <word> - Delete filter\n"
                    "/filters - List all filters\n"
                    "/help - Show this help"
                )
                send_message(chat_id, help_text, reply_to_message_id=msg_id)
            
            # Filter
            elif cmd == '/filter':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, TEXTS['sq']['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, TEXTS['sq']['admin_only'], reply_to_message_id=msg_id)
                elif len(args) < 2:
                    send_message(chat_id, "📝 Usage: /filter <word> <response>", reply_to_message_id=msg_id)
                else:
                    keyword = args[0].lower()
                    response = ' '.join(args[1:])
                    
                    is_photo = response.startswith('photo:')
                    is_gif = response.startswith('gif:')
                    media_url = None
                    text_response = response
                    
                    if is_photo:
                        media_url = response[6:]
                        text_response = None
                    elif is_gif:
                        media_url = response[4:]
                        text_response = None
                    
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT OR REPLACE INTO filters (chat_id, keyword, response, is_photo, is_gif, media_url)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (chat_id_str, keyword, text_response, is_photo, is_gif, media_url))
                    
                    if is_photo:
                        send_message(chat_id, TEXTS['sq']['filter_set_photo'].format(word=keyword), reply_to_message_id=msg_id)
                    elif is_gif:
                        send_message(chat_id, TEXTS['sq']['filter_set_gif'].format(word=keyword), reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, TEXTS['sq']['filter_set'].format(word=keyword), reply_to_message_id=msg_id)
            
            # Stop
            elif cmd == '/stop':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, TEXTS['sq']['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, TEXTS['sq']['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, "📝 Usage: /stop <word>", reply_to_message_id=msg_id)
                else:
                    keyword = args[0].lower()
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('DELETE FROM filters WHERE chat_id = ? AND keyword = ?', (chat_id_str, keyword))
                    send_message(chat_id, TEXTS['sq']['filter_deleted'].format(word=keyword), reply_to_message_id=msg_id)
            
            # Filters list
            elif cmd == '/filters':
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT keyword, response, is_photo, is_gif FROM filters WHERE chat_id = ?', (chat_id_str,))
                    filters_list = cursor.fetchall()
                    
                    if filters_list:
                        text_filter = TEXTS['sq']['filters_list']
                        for f in filters_list:
                            if f['is_gif']:
                                text_filter += f"🎬 • {f['keyword']}\n"
                            elif f['is_photo']:
                                text_filter += f"📸 • {f['keyword']}\n"
                            else:
                                text_filter += f"📝 • {f['keyword']} → {f['response'][:30]}\n"
                        send_message(chat_id, text_filter, reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, TEXTS['sq']['no_filters'], reply_to_message_id=msg_id)
        
        # Filters (like Rose)
        elif text:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT keyword, response, is_photo, is_gif, media_url FROM filters WHERE chat_id = ?', (chat_id_str,))
                filters_list = cursor.fetchall()
                
                text_lower = text.lower()
                for f in filters_list:
                    if f['keyword'] in text_lower:
                        if f['is_gif'] and f['media_url']:
                            send_gif(chat_id, f['media_url'], caption=f['response'], reply_to_message_id=msg_id)
                        elif f['is_photo'] and f['media_url']:
                            send_photo(chat_id, f['media_url'], caption=f['response'], reply_to_message_id=msg_id)
                        elif f['response']:
                            send_message(chat_id, f['response'], reply_to_message_id=msg_id)
                        break
        
        return jsonify({'ok': True})
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

# ==================== MAIN ====================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    
    logger.info("Starting Rose Bot...")
    logger.info(f"Port: {port}")
    logger.info(f"Token: {'Yes' if TOKEN else 'No'}")
    
    app.run(host='0.0.0.0', port=port, debug=False)

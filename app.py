import os
import sqlite3
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager
from functools import wraps
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
    # Mos e ndal aplikacionin nëse nuk ka token, vetëm log
    logger.warning("Bot will not work without TELEGRAM_BOT_TOKEN")

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
                CREATE TABLE IF NOT EXISTS groups (
                    chat_id TEXT PRIMARY KEY,
                    chat_title TEXT,
                    language TEXT DEFAULT 'sq',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS welcome_messages (
                    chat_id TEXT PRIMARY KEY,
                    message TEXT,
                    is_enabled BOOLEAN DEFAULT 1
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rules (
                    chat_id TEXT PRIMARY KEY,
                    rules_text TEXT
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS filters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT,
                    keyword TEXT,
                    response TEXT,
                    UNIQUE(chat_id, keyword)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS warnings (
                    chat_id TEXT,
                    user_id INTEGER,
                    count INTEGER DEFAULT 1,
                    PRIMARY KEY (chat_id, user_id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS muted_users (
                    chat_id TEXT,
                    user_id INTEGER,
                    until TIMESTAMP,
                    PRIMARY KEY (chat_id, user_id)
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
        logger.error(f"Error sending message: {e}")
        return False

def delete_message(chat_id: int, message_id: int):
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/deleteMessage"
    try:
        response = requests.post(url, json={'chat_id': chat_id, 'message_id': message_id}, timeout=10)
        return response.ok
    except Exception as e:
        logger.error(f"Error deleting message: {e}")
        return False

def ban_user(chat_id: int, user_id: int):
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/banChatMember"
    try:
        response = requests.post(url, json={'chat_id': chat_id, 'user_id': user_id}, timeout=10)
        return response.ok and response.json().get('ok', False)
    except Exception as e:
        logger.error(f"Error banning user: {e}")
        return False

def kick_user(chat_id: int, user_id: int):
    if not TOKEN:
        return False
    
    if ban_user(chat_id, user_id):
        url = f"https://api.telegram.org/bot{TOKEN}/unbanChatMember"
        try:
            requests.post(url, json={'chat_id': chat_id, 'user_id': user_id}, timeout=10)
            return True
        except:
            return False
    return False

def is_admin(chat_id: int, user_id: int):
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/getChatAdministrators"
    try:
        response = requests.post(url, json={'chat_id': chat_id}, timeout=10)
        if response.ok and response.json().get('ok'):
            admins = [a['user']['id'] for a in response.json()['result']]
            return user_id in admins
    except Exception as e:
        logger.error(f"Error checking admin: {e}")
    return False

def get_lang(chat_id: int):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT language FROM groups WHERE chat_id = ?', (str(chat_id),))
        result = cursor.fetchone()
        return result['language'] if result else 'sq'

LANGUAGES = {
    'sq': {
        'welcome': "👋 Mirë se vini në grup!",
        'rules': "📜 Rregullat e grupit:",
        'warning': "⚠️ Paralajmërim",
        'banned': "🚫 Përdoruesi u ndalua",
        'kicked': "👢 Përdoruesi u përjashtua",
        'muted': "🔇 Përdoruesi u hesht",
        'unmuted': "🔊 Heshtja u hoq",
        'no_rules': "⚠️ Nuk ka rregulla të vendosura.",
        'admin_only': "👑 Vetëm administratorët!",
        'group_only': "⚠️ Funksionon vetëm në grupe!",
        'need_reply': "⚠️ Përgjigjuni mesazhit!",
    }
}

# ==================== ENDPOINTI ====================
@app.route('/', methods=['POST', 'GET'])
def index():
    if request.method == 'GET':
        return jsonify({
            'status': 'running',
            'token_configured': bool(TOKEN),
            'version': '2.0'
        })
    
    if not TOKEN:
        return jsonify({'ok': False, 'error': 'Token not configured'}), 500
    
    try:
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
        
        # Regjistro grupin
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO groups (chat_id, chat_title)
                VALUES (?, ?)
            ''', (chat_id_str, msg['chat'].get('title', 'Unknown')))
        
        lang = get_lang(chat_id)
        texts = LANGUAGES[lang]
        
        # Anëtarë të rinj
        if 'new_chat_members' in msg:
            for member in msg['new_chat_members']:
                if member.get('is_bot'):
                    continue
                
                name = member.get('first_name', 'Përdorues')
                username = member.get('username', '')
                display = f"@{username}" if username else name
                
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT message FROM welcome_messages WHERE chat_id = ?', (chat_id_str,))
                    result = cursor.fetchone()
                    welcome = result['message'] if result else texts['welcome']
                
                welcome = welcome.replace('{user}', display).replace('{first_name}', name).replace('{username}', username)
                send_message(chat_id, welcome)
                
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT rules_text FROM rules WHERE chat_id = ?', (chat_id_str,))
                    result = cursor.fetchone()
                    if result:
                        send_message(chat_id, f"{texts['rules']}\n{result['rules_text']}")
            
            return jsonify({'ok': True})
        
        # Komandat
        if text and text.startswith('/'):
            parts = text.split()
            cmd = parts[0].lower()
            args = parts[1:]
            
            if cmd == '/start' or cmd == '/help':
                help_text = (
                    "🤖 **Bot Menaxhimi i Grupeve**\n\n"
                    "📋 **Komandat:**\n\n"
                    "**Mirëseardhja:**\n"
                    "/setwelcome [mesazh] - Vendos mirëseardhjen\n"
                    "/delwelcome - Fshin mirëseardhjen\n\n"
                    "**Rregullat:**\n"
                    "/setrules [rregullat] - Vendos rregullat\n"
                    "/rules - Shfaq rregullat\n\n"
                    "**Filtrat:**\n"
                    "/setfilter [fjalë] [përgjigje] - Vendos filtër\n"
                    "/delfilter [fjalë] - Fshin filtër\n"
                    "/filters - Shfaq filtrat\n\n"
                    "**Menaxhimi:**\n"
                    "/ban - Ndalon përdoruesin\n"
                    "/kick - Përjashton përdoruesin\n"
                    "/mute [minuta] - Hesht përdoruesin\n"
                    "/unmute - Heq heshtjen\n"
                    "/warn - Paralajmëron përdoruesin\n"
                    "/warns - Shfaq paralajmërimet\n\n"
                    "**Të tjera:**\n"
                    "/language [sq/mk] - Ndrysho gjuhën\n"
                    "/stats - Statistikat e grupit"
                )
                send_message(chat_id, help_text, reply_to_message_id=msg_id)
            
            elif cmd == '/setwelcome':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, "📝 Përdorimi: /setwelcome <mesazhi>\n\nVariablat: {user}, {first_name}, {username}", reply_to_message_id=msg_id)
                else:
                    welcome_text = ' '.join(args)
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('INSERT OR REPLACE INTO welcome_messages (chat_id, message) VALUES (?, ?)', (chat_id_str, welcome_text))
                    send_message(chat_id, "✅ Mirëseardhja u vendos!", reply_to_message_id=msg_id)
            
            elif cmd == '/delwelcome':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('DELETE FROM welcome_messages WHERE chat_id = ?', (chat_id_str,))
                    send_message(chat_id, "✅ Mirëseardhja u fshi!", reply_to_message_id=msg_id)
            
            elif cmd == '/setrules':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, "📝 Përdorimi: /setrules <rregullat>", reply_to_message_id=msg_id)
                else:
                    rules_text = ' '.join(args)
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('INSERT OR REPLACE INTO rules (chat_id, rules_text) VALUES (?, ?)', (chat_id_str, rules_text))
                    send_message(chat_id, "✅ Rregullat u vendosën!", reply_to_message_id=msg_id)
            
            elif cmd == '/rules':
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT rules_text FROM rules WHERE chat_id = ?', (chat_id_str,))
                    result = cursor.fetchone()
                    if result:
                        send_message(chat_id, f"{texts['rules']}\n{result['rules_text']}", reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['no_rules'], reply_to_message_id=msg_id)
            
            elif cmd == '/setfilter':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif len(args) < 2:
                    send_message(chat_id, "📝 Përdorimi: /setfilter <fjalë> <përgjigje>", reply_to_message_id=msg_id)
                else:
                    keyword = args[0].lower()
                    response = ' '.join(args[1:])
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('INSERT OR REPLACE INTO filters (chat_id, keyword, response) VALUES (?, ?, ?)', (chat_id_str, keyword, response))
                    send_message(chat_id, f"✅ Filtri për '{keyword}' u vendos!", reply_to_message_id=msg_id)
            
            elif cmd == '/delfilter':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, "📝 Përdorimi: /delfilter <fjalë>", reply_to_message_id=msg_id)
                else:
                    keyword = args[0].lower()
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('DELETE FROM filters WHERE chat_id = ? AND keyword = ?', (chat_id_str, keyword))
                    send_message(chat_id, f"✅ Filtri për '{keyword}' u fshi!", reply_to_message_id=msg_id)
            
            elif cmd == '/filters':
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT keyword, response FROM filters WHERE chat_id = ?', (chat_id_str,))
                    filters_list = cursor.fetchall()
                    if filters_list:
                        text_filter = "🔍 **Filtrat:**\n\n"
                        for f in filters_list:
                            text_filter += f"• {f['keyword']} → {f['response']}\n"
                        send_message(chat_id, text_filter, reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, "ℹ️ Nuk ka filtra të vendosur.", reply_to_message_id=msg_id)
            
            elif cmd == '/ban':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    if ban_user(chat_id, target):
                        send_message(chat_id, texts['banned'], reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, "❌ Gabim!", reply_to_message_id=msg_id)
            
            elif cmd == '/kick':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    if kick_user(chat_id, target):
                        send_message(chat_id, texts['kicked'], reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, "❌ Gabim!", reply_to_message_id=msg_id)
            
            elif cmd == '/mute':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    minutes = int(args[0]) if args and args[0].isdigit() else 5
                    until = datetime.now() + timedelta(minutes=minutes)
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('INSERT OR REPLACE INTO muted_users (chat_id, user_id, until) VALUES (?, ?, ?)', (chat_id_str, target, until.isoformat()))
                    send_message(chat_id, f"{texts['muted']} për {minutes} minuta!", reply_to_message_id=msg_id)
            
            elif cmd == '/unmute':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('DELETE FROM muted_users WHERE chat_id = ? AND user_id = ?', (chat_id_str, target))
                    send_message(chat_id, texts['unmuted'], reply_to_message_id=msg_id)
            
            elif cmd == '/warn':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT INTO warnings (chat_id, user_id, count) 
                            VALUES (?, ?, 1) 
                            ON CONFLICT(chat_id, user_id) DO UPDATE SET count = count + 1
                        ''', (chat_id_str, target))
                        cursor.execute('SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?', (chat_id_str, target))
                        result = cursor.fetchone()
                        count = result['count']
                    
                    send_message(chat_id, f"{texts['warning']} {count}/3", reply_to_message_id=msg_id)
                    
                    if count >= 3:
                        if ban_user(chat_id, target):
                            send_message(chat_id, texts['banned'], reply_to_message_id=msg_id)
                            cursor.execute('DELETE FROM warnings WHERE chat_id = ? AND user_id = ?', (chat_id_str, target))
            
            elif cmd == '/warns':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?', (chat_id_str, target))
                        result = cursor.fetchone()
                        count = result['count'] if result else 0
                    send_message(chat_id, f"⚠️ Paralajmërime: {count}/3", reply_to_message_id=msg_id)
            
            elif cmd == '/language':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args or args[0] not in ['sq', 'mk']:
                    send_message(chat_id, "📝 Përdorimi: /language [sq/mk]", reply_to_message_id=msg_id)
                else:
                    new_lang = args[0]
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('UPDATE groups SET language = ? WHERE chat_id = ?', (new_lang, chat_id_str))
                    send_message(chat_id, f"✅ Gjuha u ndryshua në {new_lang.upper()}!", reply_to_message_id=msg_id)
            
            elif cmd == '/stats':
                if not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('SELECT COUNT(*) as c FROM filters WHERE chat_id = ?', (chat_id_str,))
                        filters_c = cursor.fetchone()['c']
                        cursor.execute('SELECT COUNT(*) as c FROM warnings WHERE chat_id = ?', (chat_id_str,))
                        warns_c = cursor.fetchone()['c']
                        cursor.execute('SELECT COUNT(*) as c FROM muted_users WHERE chat_id = ? AND until > datetime("now")', (chat_id_str,))
                        muted_c = cursor.fetchone()['c']
                    
                    stats = f"📊 **Statistikat:**\n\n🔍 Filtrat: {filters_c}\n⚠️ Paralajmërime: {warns_c}\n🔇 Të heshtur: {muted_c}"
                    send_message(chat_id, stats, reply_to_message_id=msg_id)
        
        # Filtra dhe mute
        elif text:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT until FROM muted_users WHERE chat_id = ? AND user_id = ? AND until > datetime("now")', (chat_id_str, user_id))
                if cursor.fetchone():
                    delete_message(chat_id, msg_id)
                    return jsonify({'ok': True})
                
                cursor.execute('SELECT keyword, response FROM filters WHERE chat_id = ?', (chat_id_str,))
                for f in cursor.fetchall():
                    if f['keyword'] in text.lower():
                        send_message(chat_id, f"⚠️ {f['response']}", reply_to_message_id=msg_id)
                        delete_message(chat_id, msg_id)
                        break
        
        return jsonify({'ok': True})
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

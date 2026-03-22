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
            
            # Tabela për grupet
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS groups (
                    chat_id TEXT PRIMARY KEY,
                    chat_title TEXT,
                    language TEXT DEFAULT 'sq',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Tabela për mirëseardhjet
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS welcome_messages (
                    chat_id TEXT PRIMARY KEY,
                    message TEXT,
                    is_enabled BOOLEAN DEFAULT 1
                )
            ''')
            
            # Tabela për rregullat
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rules (
                    chat_id TEXT PRIMARY KEY,
                    rules_text TEXT
                )
            ''')
            
            # Tabela për filtrat (tani me mbështetje për foto)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS filters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT,
                    keyword TEXT,
                    response TEXT,
                    is_photo BOOLEAN DEFAULT 0,
                    photo_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(chat_id, keyword)
                )
            ''')
            
            # Tabela për paralajmërimet
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS warnings (
                    chat_id TEXT,
                    user_id INTEGER,
                    count INTEGER DEFAULT 1,
                    PRIMARY KEY (chat_id, user_id)
                )
            ''')
            
            # Tabela për përdoruesit e heshtur
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

# ==================== FUNKSIONET TELEGRAM ====================
def send_message(chat_id: int, text: str, reply_to_message_id: Optional[int] = None):
    """Dërgon mesazh tekst"""
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

def send_photo(chat_id: int, photo_url: str, caption: str = None, reply_to_message_id: Optional[int] = None):
    """Dërgon foto"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    payload = {
        'chat_id': chat_id,
        'photo': photo_url
    }
    if caption:
        payload['caption'] = caption
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if not response.ok:
            logger.error(f"Error sending photo: {response.text}")
        return response.ok
    except Exception as e:
        logger.error(f"Error sending photo: {e}")
        return False

def delete_message(chat_id: int, message_id: int):
    """Fshin mesazhin"""
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
    """Ndalon përdoruesin"""
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
    """Përjashton përdoruesin"""
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
    """Kontrollon nëse përdoruesi është admin"""
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
    """Merr gjuhën e grupit"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT language FROM groups WHERE chat_id = ?', (str(chat_id),))
        result = cursor.fetchone()
        return result['language'] if result else 'sq'

# ==================== LANGUAGES ====================
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
        'filter_usage': "📝 Përdorimi për tekst: /setfilter <fjalë> <përgjigje>\n📸 Përdorimi për foto: /setfilter <fjalë> photo:<URL_fotos>\n\nShembull: /setfilter mirmengjes photo:https://i.imgur.com/morning.jpg Mirmëngjesi!",
        'filter_set_text': "✅ Filtri për '{word}' u vendos!",
        'filter_set_photo': "✅ Filtri për '{word}' u vendos me foto!",
        'filter_deleted': "✅ Filtri për '{word}' u fshi!",
        'no_filters': "ℹ️ Nuk ka filtra të vendosur.",
        'filters_list': "🔍 **Filtrat e aktivizuar:**\n\n",
        'muted_warning': "🔇 Ju jeni të heshtur! Nuk mund të dërgoni mesazhe.",
        'error_general': "❌ Ndodhi një gabim!",
    },
    'mk': {
        'welcome': "👋 Добредојде во групата!",
        'rules': "📜 Правила на групата:",
        'warning': "⚠️ Предупредување",
        'banned': "🚫 Корисникот е блокиран",
        'kicked': "👢 Корисникот е исфрлен",
        'muted': "🔇 Корисникот е занемен",
        'unmuted': "🔊 Занеменоста е отстранета",
        'no_rules': "⚠️ Нема поставено правила.",
        'admin_only': "👑 Само администратори!",
        'group_only': "⚠️ Функционира само во групи!",
        'need_reply': "⚠️ Одговорете на пораката!",
        'filter_usage': "📝 Употреба за текст: /setfilter <збор> <одговор>\n📸 Употреба за слика: /setfilter <збор> photo:<URL_на_слика>\n\nПример: /setfilter доброутро photo:https://i.imgur.com/morning.jpg Добро утро!",
        'filter_set_text': "✅ Филтерот за '{word}' е поставен!",
        'filter_set_photo': "✅ Филтерот за '{word}' е поставен со слика!",
        'filter_deleted': "✅ Филтерот за '{word}' е избришан!",
        'no_filters': "ℹ️ Нема поставено филтри.",
        'filters_list': "🔍 **Активни филтри:**\n\n",
        'muted_warning': "🔇 Вие сте занемени! Не можете да испраќате пораки.",
        'error_general': "❌ Се случи грешка!",
    }
}

# ==================== ENDPOINTI KRYESOR ====================
@app.route('/', methods=['POST', 'GET'])
def index():
    if request.method == 'GET':
        return jsonify({
            'status': 'running',
            'token_configured': bool(TOKEN),
            'version': '2.1.0',
            'features': ['text_filters', 'photo_filters', 'welcome', 'rules', 'moderation']
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
        
        # ========== ANËTARË TË RINJ ==========
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
        
        # ========== KOMANDAT ==========
        if text and text.startswith('/'):
            parts = text.split()
            cmd = parts[0].lower()
            args = parts[1:]
            
            # KOMANDA /start DHE /help
            if cmd == '/start' or cmd == '/help':
                help_text = (
                    "🤖 **Bot Menaxhimi i Grupeve v2.1**\n\n"
                    "📋 **Komandat:**\n\n"
                    "**👋 Mirëseardhja:**\n"
                    "/setwelcome [mesazh] - Vendos mirëseardhjen\n"
                    "/delwelcome - Fshin mirëseardhjen\n\n"
                    "**📜 Rregullat:**\n"
                    "/setrules [rregullat] - Vendos rregullat\n"
                    "/rules - Shfaq rregullat\n\n"
                    "**🔍 Filtrat (Tekst & Foto):**\n"
                    "/setfilter [fjalë] [përgjigje] - Vendos filtër tekst\n"
                    "/setfilter [fjalë] photo:[URL] - Vendos filtër me foto\n"
                    "/delfilter [fjalë] - Fshin filtër\n"
                    "/filters - Shfaq filtrat\n\n"
                    "**⚡ Menaxhimi:**\n"
                    "/ban - Ndalon përdoruesin (përgjigju mesazhit)\n"
                    "/kick - Përjashton përdoruesin (përgjigju mesazhit)\n"
                    "/mute [minuta] - Hesht përdoruesin (default 5 min)\n"
                    "/unmute - Heq heshtjen (përgjigju mesazhit)\n"
                    "/warn - Paralajmëron përdoruesin (3 herë = ban)\n"
                    "/warns - Shfaq paralajmërimet (përgjigju mesazhit)\n\n"
                    "**🌐 Të tjera:**\n"
                    "/language [sq/mk] - Ndrysho gjuhën\n"
                    "/stats - Statistikat e grupit"
                )
                send_message(chat_id, help_text, reply_to_message_id=msg_id)
            
            # KOMANDA /setwelcome
            elif cmd == '/setwelcome':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, "📝 Përdorimi: /setwelcome <mesazhi>\n\nVariablat: {user}, {first_name}, {username}", 
                               reply_to_message_id=msg_id)
                else:
                    welcome_text = ' '.join(args)
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('INSERT OR REPLACE INTO welcome_messages (chat_id, message) VALUES (?, ?)', 
                                     (chat_id_str, welcome_text))
                    send_message(chat_id, "✅ Mirëseardhja u vendos!", reply_to_message_id=msg_id)
            
            # KOMANDA /delwelcome
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
            
            # KOMANDA /setrules
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
                        cursor.execute('INSERT OR REPLACE INTO rules (chat_id, rules_text) VALUES (?, ?)', 
                                     (chat_id_str, rules_text))
                    send_message(chat_id, "✅ Rregullat u vendosën!", reply_to_message_id=msg_id)
            
            # KOMANDA /rules
            elif cmd == '/rules':
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT rules_text FROM rules WHERE chat_id = ?', (chat_id_str,))
                    result = cursor.fetchone()
                    if result:
                        send_message(chat_id, f"{texts['rules']}\n{result['rules_text']}", 
                                   reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['no_rules'], reply_to_message_id=msg_id)
            
            # KOMANDA /setfilter (ME MBËSHTETJE PËR FOTO)
            elif cmd == '/setfilter':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif len(args) < 2:
                    send_message(chat_id, texts['filter_usage'], reply_to_message_id=msg_id)
                else:
                    keyword = args[0].lower()
                    response = ' '.join(args[1:])
                    
                    # Kontrollo nëse është foto
                    is_photo = response.startswith('photo:')
                    photo_url = None
                    text_response = response
                    
                    if is_photo:
                        photo_url = response[6:]  # Heq 'photo:' nga fillimi
                        text_response = None
                        
                        # Validim i thjeshtë i URL-së
                        if not photo_url.startswith(('http://', 'https://')):
                            send_message(chat_id, "❌ URL e fotos duhet të fillojë me http:// ose https://", 
                                       reply_to_message_id=msg_id)
                            return jsonify({'ok': True})
                    
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT OR REPLACE INTO filters (chat_id, keyword, response, is_photo, photo_url)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (chat_id_str, keyword, text_response, is_photo, photo_url))
                    
                    if is_photo:
                        send_message(chat_id, texts['filter_set_photo'].format(word=keyword), 
                                   reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['filter_set_text'].format(word=keyword), 
                                   reply_to_message_id=msg_id)
            
            # KOMANDA /delfilter
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
                        cursor.execute('DELETE FROM filters WHERE chat_id = ? AND keyword = ?', 
                                     (chat_id_str, keyword))
                    send_message(chat_id, texts['filter_deleted'].format(word=keyword), 
                               reply_to_message_id=msg_id)
            
            # KOMANDA /filters
            elif cmd == '/filters':
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT keyword, response, is_photo, photo_url FROM filters WHERE chat_id = ?', 
                                 (chat_id_str,))
                    filters_list = cursor.fetchall()
                    
                    if filters_list:
                        filter_text = texts['filters_list']
                        for f in filters_list:
                            if f['is_photo']:
                                filter_text += f"📸 • `{f['keyword']}` → [Foto]"
                                if f['response']:
                                    filter_text += f" - {f['response']}"
                                filter_text += "\n"
                            else:
                                filter_text += f"📝 • `{f['keyword']}` → {f['response']}\n"
                        send_message(chat_id, filter_text, reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['no_filters'], reply_to_message_id=msg_id)
            
            # KOMANDA /ban
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
                        send_message(chat_id, texts['error_general'], reply_to_message_id=msg_id)
            
            # KOMANDA /kick
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
                        send_message(chat_id, texts['error_general'], reply_to_message_id=msg_id)
            
            # KOMANDA /mute
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
                        cursor.execute('INSERT OR REPLACE INTO muted_users (chat_id, user_id, until) VALUES (?, ?, ?)', 
                                     (chat_id_str, target, until.isoformat()))
                    send_message(chat_id, f"{texts['muted']} për {minutes} minuta!", 
                               reply_to_message_id=msg_id)
            
            # KOMANDA /unmute
            elif cmd == '/unmute':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('DELETE FROM muted_users WHERE chat_id = ? AND user_id = ?', 
                                     (chat_id_str, target))
                    send_message(chat_id, texts['unmuted'], reply_to_message_id=msg_id)
            
            # KOMANDA /warn
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
                        cursor.execute('SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?', 
                                     (chat_id_str, target))
                        result = cursor.fetchone()
                        count = result['count']
                    
                    send_message(chat_id, f"{texts['warning']} {count}/3", reply_to_message_id=msg_id)
                    
                    if count >= 3:
                        if ban_user(chat_id, target):
                            send_message(chat_id, texts['banned'], reply_to_message_id=msg_id)
                            with db.get_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute('DELETE FROM warnings WHERE chat_id = ? AND user_id = ?', 
                                             (chat_id_str, target))
            
            # KOMANDA /warns
            elif cmd == '/warns':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?', 
                                     (chat_id_str, target))
                        result = cursor.fetchone()
                        count = result['count'] if result else 0
                    send_message(chat_id, f"⚠️ Paralajmërime: {count}/3", reply_to_message_id=msg_id)
            
            # KOMANDA /language
            elif cmd == '/language':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args or args[0] not in ['sq', 'mk']:
                    send_message(chat_id, "📝 Përdorimi: /language [sq/mk]\n\nsq - Shqip\nmk - Maqedonisht", 
                               reply_to_message_id=msg_id)
                else:
                    new_lang = args[0]
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('UPDATE groups SET language = ? WHERE chat_id = ?', 
                                     (new_lang, chat_id_str))
                    send_message(chat_id, f"✅ Gjuha u ndryshua në {new_lang.upper()}!", 
                               reply_to_message_id=msg_id)
            
            # KOMANDA /stats
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
                        cursor.execute('SELECT COUNT(*) as c FROM muted_users WHERE chat_id = ? AND until > datetime("now")', 
                                     (chat_id_str,))
                        muted_c = cursor.fetchone()['c']
                        
                        # Numri i filtrave me foto
                        cursor.execute('SELECT COUNT(*) as c FROM filters WHERE chat_id = ? AND is_photo = 1', 
                                     (chat_id_str,))
                        photo_filters_c = cursor.fetchone()['c']
                    
                    stats_text = (
                        f"📊 **Statistikat e Grupit**\n\n"
                        f"🔍 Filtrat total: {filters_c}\n"
                        f"📸 Filtrat me foto: {photo_filters_c}\n"
                        f"⚠️ Paralajmërime aktive: {warns_c}\n"
                        f"🔇 Të heshtur: {muted_c}"
                    )
                    send_message(chat_id, stats_text, reply_to_message_id=msg_id)
        
        # ========== FILTRAT DHE MUTE ==========
        elif text:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Kontrollo nëse përdoruesi është i heshtur
                cursor.execute('''
                    SELECT until FROM muted_users 
                    WHERE chat_id = ? AND user_id = ? AND until > datetime("now")
                ''', (chat_id_str, user_id))
                if cursor.fetchone():
                    delete_message(chat_id, msg_id)
                    send_message(chat_id, texts['muted_warning'], reply_to_message_id=msg_id)
                    return jsonify({'ok': True})
                
                # Kontrollo filtrat (përfshirë fotot)
                cursor.execute('''
                    SELECT keyword, response, is_photo, photo_url 
                    FROM filters 
                    WHERE chat_id = ?
                    ORDER BY keyword
                ''', (chat_id_str,))
                filters_list = cursor.fetchall()
                
                text_lower = text.lower()
                for filter_item in filters_list:
                    if filter_item['keyword'] in text_lower:
                        # Fshi mesazhin origjinal
                        delete_message(chat_id, msg_id)
                        
                        # Dërgo përgjigjen (tekst ose foto)
                        if filter_item['is_photo'] and filter_item['photo_url']:
                            # Dërgo foto
                            caption = filter_item['response'] if filter_item['response'] else None
                            send_photo(chat_id, filter_item['photo_url'], 
                                      caption=caption, 
                                      reply_to_message_id=msg_id)
                        else:
                            # Dërgo tekst
                            if filter_item['response']:
                                send_message(chat_id, f"⚠️ {filter_item['response']}", 
                                           reply_to_message_id=msg_id)
                        break
        
        return jsonify({'ok': True})
        
    except Exception as e:
        logger.error(f"Error processing update: {e}", exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

# ==================== MAIN ====================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    
    # Krijo direktori për databazë nëse nuk ekziston
    if not os.path.exists('data'):
        os.makedirs('data')
    
    logger.info(f"Starting bot on port {port}")
    logger.info(f"Token configured: {bool(TOKEN)}")
    logger.info("Bot supports text and photo filters!")
    
    app.run(host='0.0.0.0', port=port, debug=False)

import os
import json
import re
import asyncio
import sqlite3
from functools import wraps
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timedelta
from contextlib import contextmanager

import requests
from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ==================== KONFIGURIMI ====================
app = Flask(__name__)

# Rate limiting për siguri
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Konfigurimi i logging-ut profesional
import logging
from logging.handlers import RotatingFileHandler

# Krijo direktori për logs nëse nuk ekziston
if not os.path.exists('logs'):
    os.makedirs('logs')

# Konfiguro logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# File handler me rotacion
file_handler = RotatingFileHandler(
    'logs/bot.log', 
    maxBytes=10485760,  # 10MB
    backupCount=5
)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
))
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s'
))
logger.addHandler(console_handler)

# Token nga environment variables
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN nuk është konfiguruar!")
    raise ValueError("TELEGRAM_BOT_TOKEN is required")

# ==================== DATABASE ====================
class Database:
    """Menaxhon lidhjen me databazën SQLite"""
    
    def __init__(self, db_path: str = 'bot_data.db'):
        self.db_path = db_path
        self._init_db()
    
    @contextmanager
    def get_connection(self):
        """Context manager për lidhjet me databazën"""
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
        """Inicializon tabelat e databazës"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Tabela për grupet
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS groups (
                    chat_id TEXT PRIMARY KEY,
                    chat_title TEXT,
                    language TEXT DEFAULT 'sq',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Tabela për mirëseardhjet
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS welcome_messages (
                    chat_id TEXT PRIMARY KEY,
                    message TEXT,
                    is_enabled BOOLEAN DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Tabela për rregullat
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rules (
                    chat_id TEXT PRIMARY KEY,
                    rules_text TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Tabela për filtrat
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS filters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT,
                    keyword TEXT,
                    response TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(chat_id, keyword)
                )
            ''')
            
            # Tabela për paralajmërimet
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT,
                    user_id INTEGER,
                    count INTEGER DEFAULT 1,
                    last_warning TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(chat_id, user_id)
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
            
            # Tabela për audit log
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT,
                    admin_id INTEGER,
                    action TEXT,
                    target_user_id INTEGER,
                    reason TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            logger.info("Database initialized successfully")

db = Database()

# ==================== DEKORATORËT ====================
def admin_required(f):
    """Dekorator për të kontrolluar nëse përdoruesi është admin"""
    @wraps(f)
    def decorated_function(chat_id: int, user_id: int, *args, **kwargs):
        if not is_admin(chat_id, user_id):
            send_message(chat_id, "👑 Vetëm administratorët mund ta përdorin këtë komandë!")
            return None
        return f(chat_id, user_id, *args, **kwargs)
    return decorated_function

def group_only(f):
    """Dekorator për të kontrolluar nëse komanda përdoret në grup"""
    @wraps(f)
    def decorated_function(chat_type: str, *args, **kwargs):
        if chat_type not in ['group', 'supergroup']:
            send_message(args[0] if args else 0, "⚠️ Ky funksion punon vetëm në grupe!")
            return None
        return f(*args, **kwargs)
    return decorated_function

# ==================== FUNKSIONET KRYESORE ====================
def send_message(chat_id: int, text: str, reply_to_message_id: Optional[int] = None, 
                 parse_mode: str = 'HTML') -> bool:
    """Dërgon mesazh në Telegram me konfirmim"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode
    }
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.ok:
            return True
        else:
            logger.error(f"Error sending message: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return False

def delete_message(chat_id: int, message_id: int) -> bool:
    """Fshin mesazhin me konfirmim"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/deleteMessage"
    try:
        response = requests.post(url, json={
            'chat_id': chat_id, 
            'message_id': message_id
        }, timeout=10)
        return response.ok
    except Exception as e:
        logger.error(f"Error deleting message: {e}")
        return False

def ban_user(chat_id: int, user_id: int, revoke_messages: bool = True) -> bool:
    """Ndalon përdoruesin me opsion për të fshirë mesazhet"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/banChatMember"
    try:
        response = requests.post(url, json={
            'chat_id': chat_id,
            'user_id': user_id,
            'revoke_messages': revoke_messages
        }, timeout=10)
        return response.ok and response.json().get('ok', False)
    except Exception as e:
        logger.error(f"Error banning user: {e}")
        return False

def kick_user(chat_id: int, user_id: int) -> bool:
    """Përjashton përdoruesin"""
    if not TOKEN:
        return False
    
    # Ban dhe pastaj unban
    if ban_user(chat_id, user_id, False):
        return unban_user(chat_id, user_id)
    return False

def unban_user(chat_id: int, user_id: int) -> bool:
    """Heq ndalimin e përdoruesit"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/unbanChatMember"
    try:
        response = requests.post(url, json={
            'chat_id': chat_id,
            'user_id': user_id,
            'only_if_banned': True
        }, timeout=10)
        return response.ok
    except Exception as e:
        logger.error(f"Error unbanning user: {e}")
        return False

def is_admin(chat_id: int, user_id: int) -> bool:
    """Kontrollon nëse përdoruesi është admin me cache"""
    if not TOKEN:
        return False
    
    # Cache për 5 minuta
    cache_key = f"admin_{chat_id}_{user_id}"
    # Në një implementim real, do përdornim Redis ose cache tjetër
    
    url = f"https://api.telegram.org/bot{TOKEN}/getChatAdministrators"
    try:
        response = requests.post(url, json={'chat_id': chat_id}, timeout=10)
        if response.ok and response.json().get('ok'):
            admins = [a['user']['id'] for a in response.json()['result']]
            return user_id in admins
    except Exception as e:
        logger.error(f"Error checking admin: {e}")
    return False

def get_chat_language(chat_id: int) -> str:
    """Merr gjuhën e grupit nga databaza"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT language FROM groups WHERE chat_id = ?',
            (str(chat_id),)
        )
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
        'unmuted': "🔊 Përdoruesi nuk është më i heshtur",
        'no_rules': "⚠️ Nuk ka rregulla të vendosura.",
        'admin_only': "👑 Vetëm administratorët mund ta përdorin këtë komandë!",
        'group_only': "⚠️ Ky funksion punon vetëm në grupe!",
        'usage_setwelcome': "📝 Përdorimi: /setwelcome <mesazhi>\n\nVariablat:\n{user} - Emri i përdoruesit\n{first_name} - Emri\n{username} - Username",
        'success_welcome': "✅ Mirëseardhja u vendos me sukses!",
        'usage_setrules': "📝 Përdorimi: /setrules <rregullat>",
        'success_rules': "✅ Rregullat u vendosën me sukses!",
        'usage_setfilter': "📝 Përdorimi: /setfilter <fjalë> <përgjigje>",
        'success_filter': "✅ Filtri për '{word}' u vendos me sukses!",
        'need_reply': "⚠️ Ju lutemi përgjigjuni mesazhit të përdoruesit!",
        'error_general': "❌ Ndodhi një gabim. Ju lutem provoni përsëri.",
    },
    'mk': {
        'welcome': "👋 Добредојде во групата!",
        'rules': "📜 Правила на групата:",
        'warning': "⚠️ Предупредување",
        'banned': "🚫 Корисникот е блокиран",
        'kicked': "👢 Корисникот е исфрлен",
        'muted': "🔇 Корисникот е занемен",
        'unmuted': "🔊 Корисникот повеќе не е занемен",
        'no_rules': "⚠️ Нема поставено правила.",
        'admin_only': "👑 Само администраторите можат да ја користат оваа команда!",
        'group_only': "⚠️ Оваа функција работи само во групи!",
        'usage_setwelcome': "📝 Употреба: /setwelcome <порака>\n\nПроменливи:\n{user} - Име на корисникот\n{first_name} - Име\n{username} - Корисничко име",
        'success_welcome': "✅ Добредојде пораката е поставена успешно!",
        'usage_setrules': "📝 Употреба: /setrules <правила>",
        'success_rules': "✅ Правилата се поставени успешно!",
        'usage_setfilter': "📝 Употреба: /setfilter <збор> <одговор>",
        'success_filter': "✅ Филтерот за '{word}' е поставен успешно!",
        'need_reply': "⚠️ Ве молиме одговорете на пораката на корисникот!",
        'error_general': "❌ Се случи грешка. Ве молиме обидете се повторно.",
    }
}

# ==================== ENDPOINTI KRYESOR ====================
@app.route('/', methods=['POST', 'GET'])
@limiter.limit("100 per minute")
def index():
    """Endpoint kryesor për webhook"""
    if request.method == 'GET':
        return jsonify({
            'status': 'running',
            'token_configured': bool(TOKEN),
            'version': '2.0.0',
            'python_version': '3.12.8',
            'database': 'sqlite'
        })
    
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
        
        # Regjistro grup të ri
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO groups (chat_id, chat_title)
                VALUES (?, ?)
            ''', (chat_id_str, msg['chat'].get('title', 'Unknown')))
        
        lang = get_chat_language(chat_id)
        texts = LANGUAGES[lang]
        
        # Trajto anëtarët e rinj
        if 'new_chat_members' in msg:
            for member in msg['new_chat_members']:
                # Mos dërgo mirëseardhje për bot-in
                if member.get('is_bot'):
                    continue
                
                name = member.get('first_name', 'Përdorues')
                username = member.get('username', '')
                display_name = f"@{username}" if username else name
                
                # Merr mirëseardhjen nga databaza
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        'SELECT message FROM welcome_messages WHERE chat_id = ? AND is_enabled = 1',
                        (chat_id_str,)
                    )
                    result = cursor.fetchone()
                    welcome = result['message'] if result else texts['welcome']
                
                welcome = welcome.replace('{user}', display_name)
                welcome = welcome.replace('{first_name}', name)
                welcome = welcome.replace('{username}', username)
                send_message(chat_id, welcome)
                
                # Dërgo rregullat
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        'SELECT rules_text FROM rules WHERE chat_id = ?',
                        (chat_id_str,)
                    )
                    result = cursor.fetchone()
                    if result:
                        send_message(chat_id, f"{texts['rules']}\n{result['rules_text']}")
            
            return jsonify({'ok': True})
        
        # Trajto komandat
        if text and text.startswith('/'):
            parts = text.split()
            cmd = parts[0].lower()
            args = parts[1:]
            
            # Komanda /start
            if cmd == '/start':
                help_text = (
                    "🤖 **Bot për Menaxhimin e Grupeve v2.0**\n\n"
                    "📋 **Komandat e disponueshme:**\n\n"
                    "👋 **Mirëseardhja & Rregullat:**\n"
                    "/setwelcome - Vendos mirëseardhjen\n"
                    "/setrules - Vendos rregullat\n"
                    "/rules - Shfaq rregullat\n"
                    "/delwelcome - Fshin mirëseardhjen\n\n"
                    "🔍 **Filtrat:**\n"
                    "/setfilter - Vendos filtër\n"
                    "/delfilter - Fshin filtër\n"
                    "/filters - Shfaq filtrat\n\n"
                    "⚡ **Menaxhimi:**\n"
                    "/ban - Ndalon përdoruesin\n"
                    "/kick - Përjashton përdoruesin\n"
                    "/mute - Hesht përdoruesin\n"
                    "/unmute - Heq heshtjen\n"
                    "/warn - Paralajmëron përdoruesin\n"
                    "/warns - Shfaq paralajmërimet\n"
                    "/delwarns - Fshin paralajmërimet\n\n"
                    "🌐 **Gjuha:**\n"
                    "/language - Ndrysho gjuhën e grupit\n\n"
                    "ℹ️ **Info:**\n"
                    "/help - Shfaq këtë ndihmë\n"
                    "/stats - Statistikat e grupit"
                )
                send_message(chat_id, help_text, reply_to_message_id=msg_id)
            
            # Komanda /help
            elif cmd == '/help':
                help_text = (
                    "📚 **Ndihmë e Detajuar**\n\n"
                    "**Mirëseardhja:**\n"
                    "• /setwelcome [mesazh] - Vendos mesazhin e mirëseardhjes\n"
                    "• Variablat: {user}, {first_name}, {username}\n\n"
                    "**Rregullat:**\n"
                    "• /setrules [rregullat] - Vendos rregullat e grupit\n"
                    "• /rules - Shfaq rregullat\n\n"
                    "**Filtrat:**\n"
                    "• /setfilter [fjalë] [përgjigje] - Shton një filtër\n"
                    "• /delfilter [fjalë] - Fshin një filtër\n\n"
                    "**Menaxhimi:**\n"
                    "• Përdorni komandat duke iu përgjigjur mesazhit të përdoruesit\n"
                    "• /ban - Ndalon përdoruesin\n"
                    "• /kick - Përjashton përdoruesin\n"
                    "• /mute [minuta] - Hesht përdoruesin\n"
                    "• /warn - Paralajmëron përdoruesin\n\n"
                    "**Gjuha:**\n"
                    "• /language [sq/mk] - Ndrysho gjuhën e grupit"
                )
                send_message(chat_id, help_text, reply_to_message_id=msg_id)
            
            # Komanda /setwelcome
            elif cmd == '/setwelcome':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, texts['usage_setwelcome'], reply_to_message_id=msg_id)
                else:
                    welcome_text = ' '.join(args)
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT OR REPLACE INTO welcome_messages (chat_id, message, updated_at)
                            VALUES (?, ?, CURRENT_TIMESTAMP)
                        ''', (chat_id_str, welcome_text))
                    send_message(chat_id, texts['success_welcome'], reply_to_message_id=msg_id)
                    
                    # Audit log
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT INTO audit_log (chat_id, admin_id, action)
                            VALUES (?, ?, ?)
                        ''', (chat_id_str, user_id, 'set_welcome'))
            
            # Komanda /delwelcome
            elif cmd == '/delwelcome':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            'DELETE FROM welcome_messages WHERE chat_id = ?',
                            (chat_id_str,)
                        )
                    send_message(chat_id, "✅ Mirëseardhja u fshi!", reply_to_message_id=msg_id)
            
            # Komanda /setrules
            elif cmd == '/setrules':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, texts['usage_setrules'], reply_to_message_id=msg_id)
                else:
                    rules_text = ' '.join(args)
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT OR REPLACE INTO rules (chat_id, rules_text, updated_at)
                            VALUES (?, ?, CURRENT_TIMESTAMP)
                        ''', (chat_id_str, rules_text))
                    send_message(chat_id, texts['success_rules'], reply_to_message_id=msg_id)
            
            # Komanda /rules
            elif cmd == '/rules':
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        'SELECT rules_text FROM rules WHERE chat_id = ?',
                        (chat_id_str,)
                    )
                    result = cursor.fetchone()
                    if result:
                        send_message(chat_id, f"{texts['rules']}\n{result['rules_text']}", 
                                   reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['no_rules'], reply_to_message_id=msg_id)
            
            # Komanda /setfilter
            elif cmd == '/setfilter':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif len(args) < 2:
                    send_message(chat_id, texts['usage_setfilter'], reply_to_message_id=msg_id)
                else:
                    keyword = args[0].lower()
                    response = ' '.join(args[1:])
                    try:
                        with db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute('''
                                INSERT OR REPLACE INTO filters (chat_id, keyword, response)
                                VALUES (?, ?, ?)
                            ''', (chat_id_str, keyword, response))
                        send_message(chat_id, texts['success_filter'].format(word=keyword), 
                                   reply_to_message_id=msg_id)
                    except Exception as e:
                        logger.error(f"Error setting filter: {e}")
                        send_message(chat_id, texts['error_general'], reply_to_message_id=msg_id)
            
            # Komanda /delfilter
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
                        cursor.execute(
                            'DELETE FROM filters WHERE chat_id = ? AND keyword = ?',
                            (chat_id_str, keyword)
                        )
                    send_message(chat_id, f"✅ Filtri për '{keyword}' u fshi!", reply_to_message_id=msg_id)
            
            # Komanda /filters
            elif cmd == '/filters':
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        'SELECT keyword, response FROM filters WHERE chat_id = ?',
                        (chat_id_str,)
                    )
                    filters_list = cursor.fetchall()
                    
                    if filters_list:
                        filter_text = "🔍 **Filtrat e aktivizuar:**\n\n"
                        for f in filters_list:
                            filter_text += f"• `{f['keyword']}` → {f['response']}\n"
                        send_message(chat_id, filter_text, reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, "ℹ️ Nuk ka filtra të vendosur.", reply_to_message_id=msg_id)
            
            # Komanda /ban
            elif cmd == '/ban':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    reason = ' '.join(args) if args else 'Pa arsye'
                    
                    if ban_user(chat_id, target):
                        send_message(chat_id, f"{texts['banned']}!\nArsyeja: {reason}", 
                                   reply_to_message_id=msg_id)
                        
                        # Audit log
                        with db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute('''
                                INSERT INTO audit_log (chat_id, admin_id, action, target_user_id, reason)
                                VALUES (?, ?, ?, ?, ?)
                            ''', (chat_id_str, user_id, 'ban', target, reason))
                    else:
                        send_message(chat_id, "❌ Nuk mund të ndalohet përdoruesi!", reply_to_message_id=msg_id)
            
            # Komanda /kick
            elif cmd == '/kick':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    if kick_user(chat_id, target):
                        send_message(chat_id, f"{texts['kicked']}!", reply_to_message_id=msg_id)
                        
                        # Audit log
                        with db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute('''
                                INSERT INTO audit_log (chat_id, admin_id, action, target_user_id)
                                VALUES (?, ?, ?, ?)
                            ''', (chat_id_str, user_id, 'kick', target))
                    else:
                        send_message(chat_id, "❌ Nuk mund të përjashtohet përdoruesi!", reply_to_message_id=msg_id)
            
            # Komanda /mute
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
                        cursor.execute('''
                            INSERT OR REPLACE INTO muted_users (chat_id, user_id, until)
                            VALUES (?, ?, ?)
                        ''', (chat_id_str, target, until.isoformat()))
                    
                    send_message(chat_id, f"{texts['muted']} për {minutes} minuta!", 
                               reply_to_message_id=msg_id)
                    
                    # Audit log
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT INTO audit_log (chat_id, admin_id, action, target_user_id, reason)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (chat_id_str, user_id, 'mute', target, f"{minutes} minutes"))
            
            # Komanda /unmute
            elif cmd == '/unmute':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            'DELETE FROM muted_users WHERE chat_id = ? AND user_id = ?',
                            (chat_id_str, target)
                        )
                    send_message(chat_id, texts['unmuted'], reply_to_message_id=msg_id)
            
            # Komanda /warn
            elif cmd == '/warn':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    reason = ' '.join(args) if args else 'Pa arsye'
                    
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT INTO warnings (chat_id, user_id, count, last_warning)
                            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                                count = count + 1,
                                last_warning = CURRENT_TIMESTAMP
                        ''', (chat_id_str, target))
                        
                        cursor.execute(
                            'SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?',
                            (chat_id_str, target)
                        )
                        result = cursor.fetchone()
                        count = result['count']
                    
                    send_message(chat_id, f"{texts['warning']} {count}/3\nArsyeja: {reason}", 
                               reply_to_message_id=msg_id)
                    
                    # Nëse ka 3 paralajmërime, ndalo përdoruesin
                    if count >= 3:
                        if ban_user(chat_id, target):
                            send_message(chat_id, f"{texts['banned']} për 3 paralajmërime!", 
                                       reply_to_message_id=msg_id)
                            # Fshi paralajmërimet
                            with db.get_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute(
                                    'DELETE FROM warnings WHERE chat_id = ? AND user_id = ?',
                                    (chat_id_str, target)
                                )
                    
                    # Audit log
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT INTO audit_log (chat_id, admin_id, action, target_user_id, reason)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (chat_id_str, user_id, 'warn', target, reason))
            
            # Komanda /warns
            elif cmd == '/warns':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            'SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?',
                            (chat_id_str, target)
                        )
                        result = cursor.fetchone()
                        count = result['count'] if result else 0
                    
                    send_message(chat_id, f"⚠️ Përdoruesi ka {count}/3 paralajmërime.", 
                               reply_to_message_id=msg_id)
            
            # Komanda /delwarns
            elif cmd == '/delwarns':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            'DELETE FROM warnings WHERE chat_id = ? AND user_id = ?',
                            (chat_id_str, target)
                        )
                    send_message(chat_id, "✅ Paralajmërimet u fshinë!", reply_to_message_id=msg_id)
            
            # Komanda /language
            elif cmd == '/language':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args or args[0] not in ['sq', 'mk']:
                    send_message(chat_id, "📝 Përdorimi: /language [sq/mk]\n\nGjuhët e disponueshme:\n• sq - Shqip\n• mk - Maqedonisht", 
                               reply_to_message_id=msg_id)
                else:
                    new_lang = args[0]
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            UPDATE groups SET language = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE chat_id = ?
                        ''', (new_lang, chat_id_str))
                    send_message(chat_id, f"✅ Gjuha u ndryshua në {new_lang.upper()}!", 
                               reply_to_message_id=msg_id)
            
            # Komanda /stats
            elif cmd == '/stats':
                if not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        
                        # Numri i filtrave
                        cursor.execute(
                            'SELECT COUNT(*) as count FROM filters WHERE chat_id = ?',
                            (chat_id_str,)
                        )
                        filters_count = cursor.fetchone()['count']
                        
                        # Numri i paralajmërimeve aktive
                        cursor.execute(
                            'SELECT COUNT(*) as count FROM warnings WHERE chat_id = ?',
                            (chat_id_str,)
                        )
                        warnings_count = cursor.fetchone()['count']
                        
                        # Numri i përdoruesve të heshtur
                        cursor.execute(
                            'SELECT COUNT(*) as count FROM muted_users WHERE chat_id = ? AND until > datetime("now")',
                            (chat_id_str,)
                        )
                        muted_count = cursor.fetchone()['count']
                        
                        # Veprimet e fundit
                        cursor.execute('''
                            SELECT action, admin_id, target_user_id, timestamp 
                            FROM audit_log 
                            WHERE chat_id = ? 
                            ORDER BY timestamp DESC 
                            LIMIT 5
                        ''', (chat_id_str,))
                        recent_actions = cursor.fetchall()
                    
                    stats_text = (
                        f"📊 **Statistikat e Grupit**\n\n"
                        f"🔍 Filtrat aktiv: {filters_count}\n"
                        f"⚠️ Paralajmërime aktive: {warnings_count}\n"
                        f"🔇 Përdorues të heshtur: {muted_count}\n\n"
                        f"📝 **Veprimet e fundit:**\n"
                    )
                    
                    for action in recent_actions:
                        stats_text += f"• {action['action']} - {action['timestamp']}\n"
                    
                    send_message(chat_id, stats_text, reply_to_message_id=msg_id)
        
        # Filtra për fjalë të ndaluara
        elif text:
            # Kontrollo nëse përdoruesi është i heshtur
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT until FROM muted_users 
                    WHERE chat_id = ? AND user_id = ? AND until > datetime("now")
                ''', (chat_id_str, user_id))
                muted = cursor.fetchone()
                
                if muted:
                    delete_message(chat_id, msg_id)
                    send_message(chat_id, "🔇 Ju jeni të heshtur! Nuk mund të dërgoni mesazhe.", 
                               reply_to_message_id=msg_id)
                    return jsonify({'ok': True})
            
            # Kontrollo filtrat
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT keyword, response FROM filters WHERE chat_id = ?',
                    (chat_id_str,)
                )
                filters_list = cursor.fetchall()
                
                text_lower = text.lower()
                for filter_item in filters_list:
                    if filter_item['keyword'] in text_lower:
                        send_message(chat_id, f"⚠️ {filter_item['response']}", 
                                   reply_to_message_id=msg_id)
                        delete_message(chat_id, msg_id)
                        break
        
        return jsonify({'ok': True})
        
    except Exception as e:
        logger.error(f"Error processing update: {e}", exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

# ==================== BACKGROUND TASKS ====================
def cleanup_expired_mutes():
    """Pastron përdoruesit e heshtur që kanë skaduar"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM muted_users WHERE until <= datetime("now")')
        affected = cursor.rowcount
        if affected > 0:
            logger.info(f"Cleaned up {affected} expired mutes")

# ==================== MAIN ====================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    
    # Krijo direktori për databazë nëse nuk ekziston
    if not os.path.exists('data'):
        os.makedirs('data')
    
    logger.info(f"Starting bot on port {port}")
    logger.info(f"Token configured: {bool(TOKEN)}")
    
    app.run(host='0.0.0.0', port=port, debug=False)

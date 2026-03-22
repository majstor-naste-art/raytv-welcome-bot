import os
import json
import sqlite3
import logging
import threading
from datetime import datetime, timedelta
from contextlib import contextmanager
from functools import wraps
from typing import Optional, Dict, List, Tuple

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
            
            # Groups table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS groups (
                    chat_id TEXT PRIMARY KEY,
                    chat_title TEXT,
                    language TEXT DEFAULT 'en',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Welcome settings table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS welcome_settings (
                    chat_id TEXT PRIMARY KEY,
                    message TEXT,
                    is_enabled BOOLEAN DEFAULT 1,
                    pin_enabled BOOLEAN DEFAULT 0,
                    delete_after_minutes INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Rules table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rules (
                    chat_id TEXT PRIMARY KEY,
                    rules_text TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Filters table (like Rose)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS filters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT,
                    keyword TEXT,
                    response TEXT,
                    is_photo BOOLEAN DEFAULT 0,
                    is_gif BOOLEAN DEFAULT 0,
                    is_sticker BOOLEAN DEFAULT 0,
                    is_video BOOLEAN DEFAULT 0,
                    media_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(chat_id, keyword)
                )
            ''')
            
            # Warnings table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS warnings (
                    chat_id TEXT,
                    user_id INTEGER,
                    count INTEGER DEFAULT 1,
                    reason TEXT,
                    last_warning TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, user_id)
                )
            ''')
            
            # Muted users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS muted_users (
                    chat_id TEXT,
                    user_id INTEGER,
                    until TIMESTAMP,
                    reason TEXT,
                    PRIMARY KEY (chat_id, user_id)
                )
            ''')
            
            # Banned users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS banned_users (
                    chat_id TEXT,
                    user_id INTEGER,
                    reason TEXT,
                    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, user_id)
                )
            ''')
            
            # Notes table (like Rose notes)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT,
                    name TEXT,
                    content TEXT,
                    is_photo BOOLEAN DEFAULT 0,
                    is_gif BOOLEAN DEFAULT 0,
                    media_url TEXT,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(chat_id, name)
                )
            ''')
            
            # Disabled commands table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS disabled_commands (
                    chat_id TEXT,
                    command TEXT,
                    PRIMARY KEY (chat_id, command)
                )
            ''')
            
            # Admins cache table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS admin_cache (
                    chat_id TEXT,
                    admin_id INTEGER,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, admin_id)
                )
            ''')
            
            conn.commit()
            logger.info("Database initialized successfully")

db = Database()

# ==================== FUNKSIONET TELEGRAM ====================
def send_message(chat_id: int, text: str, reply_to_message_id: Optional[int] = None, 
                 parse_mode: str = 'HTML', disable_web_page_preview: bool = False) -> Optional[Dict]:
    """Send text message"""
    if not TOKEN:
        return None
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id, 
        'text': text, 
        'parse_mode': parse_mode,
        'disable_web_page_preview': disable_web_page_preview
    }
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.ok:
            return response.json().get('result')
        else:
            logger.error(f"Error sending message: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

def send_photo(chat_id: int, photo_url: str, caption: str = None, 
               reply_to_message_id: Optional[int] = None) -> Optional[Dict]:
    """Send photo"""
    if not TOKEN:
        return None
    
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
        if response.ok:
            return response.json().get('result')
        else:
            logger.error(f"Error sending photo: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error sending photo: {e}")
        return None

def send_gif(chat_id: int, gif_url: str, caption: str = None, 
             reply_to_message_id: Optional[int] = None) -> Optional[Dict]:
    """Send GIF/animation"""
    if not TOKEN:
        return None
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendAnimation"
    payload = {
        'chat_id': chat_id,
        'animation': gif_url
    }
    if caption:
        payload['caption'] = caption
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.ok:
            return response.json().get('result')
        else:
            logger.error(f"Error sending GIF: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error sending GIF: {e}")
        return None

def send_sticker(chat_id: int, sticker_id: str, reply_to_message_id: Optional[int] = None) -> Optional[Dict]:
    """Send sticker"""
    if not TOKEN:
        return None
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendSticker"
    payload = {
        'chat_id': chat_id,
        'sticker': sticker_id
    }
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.ok:
            return response.json().get('result')
        else:
            logger.error(f"Error sending sticker: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error sending sticker: {e}")
        return None

def send_video(chat_id: int, video_url: str, caption: str = None, 
               reply_to_message_id: Optional[int] = None) -> Optional[Dict]:
    """Send video"""
    if not TOKEN:
        return None
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendVideo"
    payload = {
        'chat_id': chat_id,
        'video': video_url
    }
    if caption:
        payload['caption'] = caption
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.ok:
            return response.json().get('result')
        else:
            logger.error(f"Error sending video: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error sending video: {e}")
        return None

def delete_message(chat_id: int, message_id: int) -> bool:
    """Delete message"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/deleteMessage"
    try:
        response = requests.post(url, json={'chat_id': chat_id, 'message_id': message_id}, timeout=10)
        return response.ok
    except Exception as e:
        logger.error(f"Error deleting message: {e}")
        return False

def pin_message(chat_id: int, message_id: int, disable_notification: bool = True) -> bool:
    """Pin message"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/pinChatMessage"
    try:
        response = requests.post(url, json={
            'chat_id': chat_id,
            'message_id': message_id,
            'disable_notification': disable_notification
        }, timeout=10)
        return response.ok
    except Exception as e:
        logger.error(f"Error pinning message: {e}")
        return False

def unpin_message(chat_id: int, message_id: Optional[int] = None) -> bool:
    """Unpin message"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/unpinChatMessage"
    payload = {'chat_id': chat_id}
    if message_id:
        payload['message_id'] = message_id
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.ok
    except Exception as e:
        logger.error(f"Error unpinning message: {e}")
        return False

def ban_user(chat_id: int, user_id: int, revoke_messages: bool = True, reason: str = None) -> bool:
    """Ban user"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/banChatMember"
    payload = {
        'chat_id': chat_id,
        'user_id': user_id,
        'revoke_messages': revoke_messages
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.ok and response.json().get('ok'):
            if reason:
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT OR REPLACE INTO banned_users (chat_id, user_id, reason)
                        VALUES (?, ?, ?)
                    ''', (str(chat_id), user_id, reason))
            return True
        return False
    except Exception as e:
        logger.error(f"Error banning user: {e}")
        return False

def unban_user(chat_id: int, user_id: int) -> bool:
    """Unban user"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/unbanChatMember"
    try:
        response = requests.post(url, json={
            'chat_id': chat_id,
            'user_id': user_id,
            'only_if_banned': True
        }, timeout=10)
        if response.ok:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM banned_users WHERE chat_id = ? AND user_id = ?', 
                             (str(chat_id), user_id))
            return True
        return False
    except Exception as e:
        logger.error(f"Error unbanning user: {e}")
        return False

def kick_user(chat_id: int, user_id: int, reason: str = None) -> bool:
    """Kick user"""
    if ban_user(chat_id, user_id, False, reason):
        return unban_user(chat_id, user_id)
    return False

def mute_user(chat_id: int, user_id: int, minutes: int = 5, reason: str = None) -> bool:
    """Mute user"""
    until = datetime.now() + timedelta(minutes=minutes)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO muted_users (chat_id, user_id, until, reason)
            VALUES (?, ?, ?, ?)
        ''', (str(chat_id), user_id, until.isoformat(), reason))
    return True

def unmute_user(chat_id: int, user_id: int) -> bool:
    """Unmute user"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM muted_users WHERE chat_id = ? AND user_id = ?', 
                     (str(chat_id), user_id))
    return True

def is_admin(chat_id: int, user_id: int, use_cache: bool = True) -> bool:
    """Check if user is admin with cache"""
    if not TOKEN:
        return False
    
    # Check cache first
    if use_cache:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT admin_id FROM admin_cache 
                WHERE chat_id = ? AND admin_id = ? 
                AND cached_at > datetime("now", "-5 minutes")
            ''', (str(chat_id), user_id))
            if cursor.fetchone():
                return True
    
    # Fetch from API
    url = f"https://api.telegram.org/bot{TOKEN}/getChatAdministrators"
    try:
        response = requests.post(url, json={'chat_id': chat_id}, timeout=10)
        if response.ok and response.json().get('ok'):
            admins = [a['user']['id'] for a in response.json()['result']]
            is_admin_user = user_id in admins
            
            # Update cache
            if is_admin_user:
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT OR REPLACE INTO admin_cache (chat_id, admin_id, cached_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP)
                    ''', (str(chat_id), user_id))
            
            return is_admin_user
    except Exception as e:
        logger.error(f"Error checking admin: {e}")
    return False

def get_chat_language(chat_id: int) -> str:
    """Get group language"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT language FROM groups WHERE chat_id = ?', (str(chat_id),))
        result = cursor.fetchone()
        return result['language'] if result else 'en'

def set_chat_language(chat_id: int, language: str) -> bool:
    """Set group language"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE groups SET language = ?, updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = ?
        ''', (language, str(chat_id)))
        return True

def is_command_disabled(chat_id: int, command: str) -> bool:
    """Check if command is disabled in group"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM disabled_commands WHERE chat_id = ? AND command = ?', 
                     (str(chat_id), command))
        return cursor.fetchone() is not None

def disable_command(chat_id: int, command: str) -> bool:
    """Disable command in group"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO disabled_commands (chat_id, command) VALUES (?, ?)', 
                     (str(chat_id), command))
        return True

def enable_command(chat_id: int, command: str) -> bool:
    """Enable command in group"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM disabled_commands WHERE chat_id = ? AND command = ?', 
                     (str(chat_id), command))
        return True

# ==================== LANGUAGES ====================
LANGUAGES = {
    'en': {
        'welcome': "👋 Welcome to the group!",
        'rules': "📜 Group Rules:",
        'warning': "⚠️ Warning",
        'banned': "🚫 User has been banned",
        'kicked': "👢 User has been kicked",
        'muted': "🔇 User has been muted",
        'unmuted': "🔊 User has been unmuted",
        'no_rules': "⚠️ No rules have been set.",
        'admin_only': "👑 Only administrators can use this command!",
        'group_only': "⚠️ This command only works in groups!",
        'need_reply': "⚠️ Please reply to the user's message!",
        'filter_usage': "📝 **Usage:**\n\n**Text filter:**\n/filter <keyword> <response>\n\n**Photo filter:**\n/filter <keyword> photo:<URL>\n\n**GIF filter:**\n/filter <keyword> gif:<URL>\n\n**Sticker filter:**\n/filter <keyword> sticker:<sticker_id>\n\n**Video filter:**\n/filter <keyword> video:<URL>\n\n**Examples:**\n/filter hello Hello there!\n/filter morning photo:https://example.com/morning.jpg",
        'filter_set_text': "✅ Filter for '{word}' has been set!",
        'filter_set_photo': "✅ Photo filter for '{word}' has been set!",
        'filter_set_gif': "✅ GIF filter for '{word}' has been set!",
        'filter_set_sticker': "✅ Sticker filter for '{word}' has been set!",
        'filter_set_video': "✅ Video filter for '{word}' has been set!",
        'filter_deleted': "✅ Filter for '{word}' has been deleted!",
        'no_filters': "ℹ️ No filters have been set.",
        'filters_list': "🔍 **Active filters:**\n\n",
        'muted_warning': "🔇 You are muted! You cannot send messages.",
        'error_general': "❌ An error occurred. Please try again.",
        'stats': "📊 **Group Statistics**\n\n",
        'no_filter_word': "⚠️ Please provide a keyword and response!\nUsage: /filter <keyword> <response>",
        'welcome_usage': "📝 **Welcome Settings:**\n\n/setwelcome <message> - Set welcome message\n/setwelcome enable/disable - Enable/disable welcome\n/setwelcome pin on/off - Pin welcome message\n/setwelcome delete <minutes> - Auto-delete after minutes\n/setwelcome preview - Preview welcome message\n/setwelcome reset - Reset to default\n\n**Variables:**\n{user} - Full name\n{first_name} - First name\n{username} - Username\n{id} - User ID\n{group} - Group name\n{members} - Member count",
        'welcome_set': "✅ Welcome message has been set!",
        'welcome_enabled': "✅ Welcome message has been enabled!",
        'welcome_disabled': "⏸️ Welcome message has been disabled!",
        'welcome_pin_enabled': "📍 Welcome message will be pinned!",
        'welcome_pin_disabled': "📍 Welcome pin has been disabled!",
        'welcome_auto_delete': "⏰ Welcome message will be deleted after {minutes} minutes!",
        'welcome_preview': "👀 **Preview:**\n\n",
        'welcome_reset': "🔄 Welcome message has been reset to default!",
        'rules_usage': "📝 Usage: /setrules <rules>",
        'rules_set': "✅ Rules have been set!",
        'rules_updated': "✅ Rules have been updated!",
        'note_usage': "📝 Usage:\n/note <name> <content> - Save a note\n/get <name> - Get a note\n/notes - List all notes\n/delnote <name> - Delete a note",
        'note_saved': "✅ Note '{name}' has been saved!",
        'note_deleted': "✅ Note '{name}' has been deleted!",
        'no_notes': "ℹ️ No notes have been saved.",
        'notes_list': "📝 **Saved notes:**\n\n",
        'warn_usage': "⚠️ Usage: /warn <reason> (reply to message)",
        'warn_count': "⚠️ Warning {count}/3",
        'warn_banned': "🚫 User has been banned for reaching 3 warnings!",
        'warns_usage': "⚠️ Usage: /warns (reply to message)",
        'warns_count': "⚠️ Warnings: {count}/3",
        'resetwarns': "✅ Warnings have been reset!",
        'mute_usage': "🔇 Usage: /mute <minutes> (reply to message)",
        'unmute_usage': "🔊 Usage: /unmute (reply to message)",
        'ban_usage': "🚫 Usage: /ban <reason> (reply to message)",
        'kick_usage': "👢 Usage: /kick <reason> (reply to message)",
        'pin_usage': "📍 Usage: /pin (reply to message)",
        'unpin_usage': "📍 Usage: /unpin",
        'pinned': "📍 Message has been pinned!",
        'unpinned': "📍 Message has been unpinned!",
        'language_usage': "🌐 Usage: /language <en/sq/mk>",
        'language_changed': "✅ Language has been changed to {lang}!",
        'admin_list': "👑 **Administrators:**\n\n",
        'bot_info': "🤖 **Bot Info:**\n\nVersion: 2.3.0\nLanguage: Python 3.12\nDatabase: SQLite\nFeatures: Filters, Welcome, Rules, Notes, Moderation",
        'help_text': (
            "🤖 **Rose Bot v2.3**\n\n"
            "📋 **Commands:**\n\n"
            "**🔍 Filters:**\n"
            "/filter <word> <response> - Set a filter\n"
            "/stop <word> - Delete a filter\n"
            "/filters - List all filters\n\n"
            "**👋 Welcome:**\n"
            "/setwelcome <message> - Set welcome message\n"
            "/setwelcome enable/disable - Enable/disable\n"
            "/setwelcome pin on/off - Pin welcome\n"
            "/delwelcome - Delete welcome\n\n"
            "**📜 Rules:**\n"
            "/setrules <rules> - Set group rules\n"
            "/rules - Show rules\n\n"
            "**📝 Notes:**\n"
            "/note <name> <content> - Save a note\n"
            "/get <name> - Get a note\n"
            "/notes - List notes\n"
            "/delnote <name> - Delete note\n\n"
            "**⚡ Moderation:**\n"
            "/ban <reason> - Ban user\n"
            "/kick <reason> - Kick user\n"
            "/mute <minutes> - Mute user\n"
            "/unmute - Unmute user\n"
            "/warn <reason> - Warn user\n"
            "/warns - Check warnings\n"
            "/resetwarns - Reset warnings\n\n"
            "**📍 Admin:**\n"
            "/pin - Pin message\n"
            "/unpin - Unpin message\n"
            "/admins - List admins\n\n"
            "**🌐 Other:**\n"
            "/language <en/sq/mk> - Change language\n"
            "/stats - Group statistics\n"
            "/info - Bot information\n"
            "/help - Show this help"
        )
    },
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
        'need_reply': "⚠️ Ju lutemi përgjigjuni mesazhit të përdoruesit!",
        'filter_usage': "📝 **Përdorimi:**\n\n**Filtër tekst:**\n/filter <fjalë> <përgjigje>\n\n**Filtër foto:**\n/filter <fjalë> photo:<URL>\n\n**Filtër GIF:**\n/filter <fjalë> gif:<URL>\n\n**Filtër sticker:**\n/filter <fjalë> sticker:<sticker_id>\n\n**Filtër video:**\n/filter <fjalë> video:<URL>\n\n**Shembuj:**\n/filter përshëndetje Përshëndetje!\n/filter mirmengjes photo:https://example.com/morning.jpg",
        'filter_set_text': "✅ Filtri për '{word}' u vendos!",
        'filter_set_photo': "✅ Filtri me foto për '{word}' u vendos!",
        'filter_set_gif': "✅ Filtri me GIF për '{word}' u vendos!",
        'filter_set_sticker': "✅ Filtri me sticker për '{word}' u vendos!",
        'filter_set_video': "✅ Filtri me video për '{word}' u vendos!",
        'filter_deleted': "✅ Filtri për '{word}' u fshi!",
        'no_filters': "ℹ️ Nuk ka filtra të vendosur.",
        'filters_list': "🔍 **Filtrat e aktivizuar:**\n\n",
        'muted_warning': "🔇 Ju jeni të heshtur! Nuk mund të dërgoni mesazhe.",
        'error_general': "❌ Ndodhi një gabim. Ju lutem provoni përsëri.",
        'stats': "📊 **Statistikat e Grupit**\n\n",
        'no_filter_word': "⚠️ Ju lutem vendosni një fjalë kyçe dhe përgjigje!\nPërdorimi: /filter <fjalë> <përgjigje>",
        'welcome_usage': "📝 **Konfigurimi i Mirëseardhjes:**\n\n/setwelcome <mesazh> - Vendos mirëseardhjen\n/setwelcome enable/disable - Aktivizon/Çaktivizon\n/setwelcome pin on/off - Bën pin të mirëseardhjes\n/setwelcome delete <minuta> - Fshi automatikisht\n/setwelcome preview - Preview i mirëseardhjes\n/setwelcome reset - Rivendos në default\n\n**Variablat:**\n{user} - Emri i plotë\n{first_name} - Emri\n{username} - Username\n{id} - ID e përdoruesit\n{group} - Emri i grupit\n{members} - Numri i anëtarëve",
        'welcome_set': "✅ Mirëseardhja u vendos!",
        'welcome_enabled': "✅ Mirëseardhja u aktivizua!",
        'welcome_disabled': "⏸️ Mirëseardhja u çaktivizua!",
        'welcome_pin_enabled': "📍 Mirëseardhja do të bëhet pin!",
        'welcome_pin_disabled': "📍 Pin-i i mirëseardhjes u çaktivizua!",
        'welcome_auto_delete': "⏰ Mirëseardhja do të fshihet pas {minutes} minutash!",
        'welcome_preview': "👀 **Preview:**\n\n",
        'welcome_reset': "🔄 Mirëseardhja u rivendos në default!",
        'rules_usage': "📝 Përdorimi: /setrules <rregullat>",
        'rules_set': "✅ Rregullat u vendosën!",
        'rules_updated': "✅ Rregullat u përditësuan!",
        'note_usage': "📝 Përdorimi:\n/note <emri> <përmbajtja> - Ruaj një shënim\n/get <emri> - Merre një shënim\n/notes - Listo të gjithë shënimet\n/delnote <emri> - Fshi një shënim",
        'note_saved': "✅ Shënimi '{name}' u ruajt!",
        'note_deleted': "✅ Shënimi '{name}' u fshi!",
        'no_notes': "ℹ️ Nuk ka shënime të ruajtura.",
        'notes_list': "📝 **Shënimet e ruajtura:**\n\n",
        'warn_usage': "⚠️ Përdorimi: /warn <arsyeja> (përgjigju mesazhit)",
        'warn_count': "⚠️ Paralajmërim {count}/3",
        'warn_banned': "🚫 Përdoruesi u ndalua për 3 paralajmërime!",
        'warns_usage': "⚠️ Përdorimi: /warns (përgjigju mesazhit)",
        'warns_count': "⚠️ Paralajmërime: {count}/3",
        'resetwarns': "✅ Paralajmërimet u rivendosën!",
        'mute_usage': "🔇 Përdorimi: /mute <minuta> (përgjigju mesazhit)",
        'unmute_usage': "🔊 Përdorimi: /unmute (përgjigju mesazhit)",
        'ban_usage': "🚫 Përdorimi: /ban <arsyeja> (përgjigju mesazhit)",
        'kick_usage': "👢 Përdorimi: /kick <arsyeja> (përgjigju mesazhit)",
        'pin_usage': "📍 Përdorimi: /pin (përgjigju mesazhit)",
        'unpin_usage': "📍 Përdorimi: /unpin",
        'pinned': "📍 Mesazhi u bë pin!",
        'unpinned': "📍 Pin-i u hoq!",
        'language_usage': "🌐 Përdorimi: /language <en/sq/mk>",
        'language_changed': "✅ Gjuha u ndryshua në {lang}!",
        'admin_list': "👑 **Administratorët:**\n\n",
        'bot_info': "🤖 **Informacioni i Bot-it:**\n\nVersioni: 2.3.0\nGjuha: Python 3.12\nDatabaza: SQLite\nFunksionet: Filtrat, Mirëseardhja, Rregullat, Shënimet, Menaxhimi",
        'help_text': (
            "🤖 **Rose Bot v2.3**\n\n"
            "📋 **Komandat:**\n\n"
            "**🔍 Filtrat:**\n"
            "/filter <fjalë> <përgjigje> - Vendos filtër\n"
            "/stop <fjalë> - Fshi filtër\n"
            "/filters - Listo filtrat\n\n"
            "**👋 Mirëseardhja:**\n"
            "/setwelcome <mesazh> - Vendos mirëseardhjen\n"
            "/setwelcome enable/disable - Aktivizon/Çaktivizon\n"
            "/setwelcome pin on/off - Bën pin\n"
            "/delwelcome - Fshi mirëseardhjen\n\n"
            "**📜 Rregullat:**\n"
            "/setrules <rregullat> - Vendos rregullat\n"
            "/rules - Shfaq rregullat\n\n"
            "**📝 Shënimet:**\n"
            "/note <emri> <përmbajtja> - Ruaj shënim\n"
            "/get <emri> - Merre shënimin\n"
            "/notes - Listo shënimet\n"
            "/delnote <emri> - Fshi shënimin\n\n"
            "**⚡ Menaxhimi:**\n"
            "/ban <arsyeja> - Ndal përdoruesin\n"
            "/kick <arsyeja> - Përjashto përdoruesin\n"
            "/mute <minuta> - Hesht përdoruesin\n"
            "/unmute - Hiq heshtjen\n"
            "/warn <arsyeja> - Paralajmëro përdoruesin\n"
            "/warns - Shiko paralajmërimet\n"
            "/resetwarns - Rivendos paralajmërimet\n\n"
            "**📍 Administratorët:**\n"
            "/pin - Bëj pin mesazhin\n"
            "/unpin - Hiq pin-in\n"
            "/admins - Listo administratorët\n\n"
            "**🌐 Të tjera:**\n"
            "/language <en/sq/mk> - Ndrysho gjuhën\n"
            "/stats - Statistikat e grupit\n"
            "/info - Informacioni i bot-it\n"
            "/help - Shfaq këtë ndihmë"
        )
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
        'need_reply': "⚠️ Ве молиме одговорете на пораката на корисникот!",
        'filter_usage': "📝 **Употреба:**\n\n**Текстуален филтер:**\n/filter <збор> <одговор>\n\n**Филтер со слика:**\n/filter <збор> photo:<URL>\n\n**Филтер со GIF:**\n/filter <збор> gif:<URL>\n\n**Филтер со стикер:**\n/filter <збор> sticker:<sticker_id>\n\n**Филтер со видео:**\n/filter <збор> video:<URL>\n\n**Примери:**\n/filter здраво Здраво!\n/filter доброутро photo:https://example.com/morning.jpg",
        'filter_set_text': "✅ Филтерот за '{word}' е поставен!",
        'filter_set_photo': "✅ Филтерот со слика за '{word}' е поставен!",
        'filter_set_gif': "✅ Филтерот со GIF за '{word}' е поставен!",
        'filter_set_sticker': "✅ Филтерот со стикер за '{word}' е поставен!",
        'filter_set_video': "✅ Филтерот со видео за '{word}' е поставен!",
        'filter_deleted': "✅ Филтерот за '{word}' е избришан!",
        'no_filters': "ℹ️ Нема поставено филтри.",
        'filters_list': "🔍 **Активни филтри:**\n\n",
        'muted_warning': "🔇 Вие сте занемени! Не можете да испраќате пораки.",
        'error_general': "❌ Се случи грешка. Ве молиме обидете се повторно.",
        'stats': "📊 **Статистики на групата**\n\n",
        'no_filter_word': "⚠️ Ве молиме внесете збор и одговор!\nУпотреба: /filter <збор> <одговор>",
        'welcome_usage': "📝 **Подесување на добредојде:**\n\n/setwelcome <порака> - Постави добредојде\n/setwelcome enable/disable - Овозможи/Оневозможи\n/setwelcome pin on/off - Закачи порака\n/setwelcome delete <минути> - Автоматско бришење\n/setwelcome preview - Преглед\n/setwelcome reset - Врати на стандардно\n\n**Променливи:**\n{user} - Цело име\n{first_name} - Име\n{username} - Корисничко име\n{id} - ID на корисник\n{group} - Име на група\n{members} - Број на членови",
        'welcome_set': "✅ Добредојде пораката е поставена!",
        'welcome_enabled': "✅ Добредојде пораката е овозможена!",
        'welcome_disabled': "⏸️ Добредојде пораката е оневозможена!",
        'welcome_pin_enabled': "📍 Добредојде пораката ќе биде закачена!",
        'welcome_pin_disabled': "📍 Закачувањето на добредојде е оневозможено!",
        'welcome_auto_delete': "⏰ Добредојде пораката ќе се избрише по {minutes} минути!",
        'welcome_preview': "👀 **Преглед:**\n\n",
        'welcome_reset': "🔄 Добредојде пораката е вратена на стандардна!",
        'rules_usage': "📝 Употреба: /setrules <правила>",
        'rules_set': "✅ Правилата се поставени!",
        'rules_updated': "✅ Правилата се ажурирани!",
        'note_usage': "📝 Употреба:\n/note <име> <содржина> - Зачувај белешка\n/get <име> - Земете белешка\n/notes - Листа на белешки\n/delnote <име> - Избриши белешка",
        'note_saved': "✅ Белешката '{name}' е зачувана!",
        'note_deleted': "✅ Белешката '{name}' е избришана!",
        'no_notes': "ℹ️ Нема зачувани белешки.",
        'notes_list': "📝 **Зачувани белешки:**\n\n",
        'warn_usage': "⚠️ Употреба: /warn <причина> (одговорете на порака)",
        'warn_count': "⚠️ Предупредување {count}/3",
        'warn_banned': "🚫 Корисникот е блокиран поради 3 предупредувања!",
        'warns_usage': "⚠️ Употреба: /warns (одговорете на порака)",
        'warns_count': "⚠️ Предупредувања: {count}/3",
        'resetwarns': "✅ Предупредувањата се ресетирани!",
        'mute_usage': "🔇 Употреба: /mute <минути> (одговорете на порака)",
        'unmute_usage': "🔊 Употреба: /unmute (одговорете на порака)",
        'ban_usage': "🚫 Употреба: /ban <причина> (одговорете на порака)",
        'kick_usage': "👢 Употреба: /kick <причина> (одговорете на порака)",
        'pin_usage': "📍 Употреба: /pin (одговорете на порака)",
        'unpin_usage': "📍 Употреба: /unpin",
        'pinned': "📍 Пораката е закачена!",
        'unpinned': "📍 Закачувањето е отстрането!",
        'language_usage': "🌐 Употреба: /language <en/sq/mk>",
        'language_changed': "✅ Јазикот е сменет на {lang}!",
        'admin_list': "👑 **Администратори:**\n\n",
        'bot_info': "🤖 **Информации за бот:**\n\nВерзија: 2.3.0\nЈазик: Python 3.12\nБаза: SQLite\nФункции: Филтри, Добредојде, Правила, Белешки, Модерација",
        'help_text': (
            "🤖 **Rose Bot v2.3**\n\n"
            "📋 **Команди:**\n\n"
            "**🔍 Филтри:**\n"
            "/filter <збор> <одговор> - Постави филтер\n"
            "/stop <збор> - Избриши филтер\n"
            "/filters - Листа на филтри\n\n"
            "**👋 Добредојде:**\n"
            "/setwelcome <порака> - Постави добредојде\n"
            "/setwelcome enable/disable - Овозможи/Оневозможи\n"
            "/setwelcome pin on/off - Закачи порака\n"
            "/delwelcome - Избриши добредојде\n\n"
            "**📜 Правила:**\n"
            "/setrules <правила> - Постави правила\n"
            "/rules - Прикажи правила\n\n"
            "**📝 Белешки:**\n"
            "/note <име> <содржина> - Зачувај белешка\n"
            "/get <име> - Земете белешка\n"
            "/notes - Листа на белешки\n"
            "/delnote <име> - Избриши белешка\n\n"
            "**⚡ Модерација:**\n"
            "/ban <причина> - Блокирај корисник\n"
            "/kick <причина> - Исфрли корисник\n"
            "/mute <минути> - Заними корисник\n"
            "/unmute - Отстрани занеменост\n"
            "/warn <причина> - Предупреди корисник\n"
            "/warns - Провери предупредувања\n"
            "/resetwarns - Ресетирај предупредувања\n\n"
            "**📍 Администратори:**\n"
            "/pin - Закачи порака\n"
            "/unpin - Отстрани закачување\n"
            "/admins - Листа на администратори\n\n"
            "**🌐 Други:**\n"
            "/language <en/sq/mk> - Смени јазик\n"
            "/stats - Статистики на група\n"
            "/info - Информации за бот\n"
            "/help - Прикажи помош"
        )
    }
}

# ==================== ENDPOINTI KRYESOR ====================
@app.route('/', methods=['POST', 'GET'])
def index():
    if request.method == 'GET':
        return jsonify({
            'status': 'running',
            'token_configured': bool(TOKEN),
            'version': '2.3.0',
            'name': 'Rose Bot',
            'features': [
                'filters', 'welcome', 'rules', 'notes', 
                'moderation', 'multi_language', 'pin', 
                'admins', 'stats'
            ]
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
        
        # Register group
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO groups (chat_id, chat_title)
                VALUES (?, ?)
            ''', (chat_id_str, msg['chat'].get('title', 'Unknown')))
        
        lang = get_chat_language(chat_id)
        texts = LANGUAGES[lang]
        
        # ========== NEW MEMBERS ==========
        if 'new_chat_members' in msg:
            for member in msg['new_chat_members']:
                if member.get('is_bot'):
                    continue
                
                name = member.get('first_name', 'User')
                username = member.get('username', '')
                user_id = member.get('id')
                
                # Get welcome settings
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        SELECT message, is_enabled, pin_enabled, delete_after_minutes 
                        FROM welcome_settings 
                        WHERE chat_id = ?
                    ''', (chat_id_str,))
                    settings = cursor.fetchone()
                
                if settings and not settings['is_enabled']:
                    continue
                
                welcome = settings['message'] if settings and settings['message'] else texts['welcome']
                
                # Replace variables
                welcome = welcome.replace('{user}', name)
                welcome = welcome.replace('{first_name}', name)
                welcome = welcome.replace('{username}', f"@{username}" if username else name)
                welcome = welcome.replace('{id}', str(user_id))
                welcome = welcome.replace('{group}', msg['chat'].get('title', 'Group'))
                
                # Get member count
                try:
                    url = f"https://api.telegram.org/bot{TOKEN}/getChatMembersCount"
                    response = requests.post(url, json={'chat_id': chat_id}, timeout=10)
                    if response.ok:
                        members_count = response.json().get('result', 0)
                        welcome = welcome.replace('{members}', str(members_count))
                except:
                    welcome = welcome.replace('{members}', '?')
                
                # Send welcome message
                sent = send_message(chat_id, welcome, reply_to_message_id=msg_id)
                
                # Pin if enabled
                if settings and settings.get('pin_enabled') and sent and sent.get('result', {}).get('message_id'):
                    pin_message(chat_id, sent['result']['message_id'])
                
                # Auto delete after minutes
                if settings and settings.get('delete_after_minutes', 0) > 0:
                    delete_after_seconds = settings['delete_after_minutes'] * 60
                    threading.Timer(delete_after_seconds, delete_message, 
                                  args=[chat_id, sent['result']['message_id']]).start()
                
                # Send rules
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT rules_text FROM rules WHERE chat_id = ?', (chat_id_str,))
                    result = cursor.fetchone()
                    if result:
                        send_message(chat_id, f"{texts['rules']}\n{result['rules_text']}")
            
            return jsonify({'ok': True})
        
        # ========== LEFT MEMBER ==========
        if 'left_chat_member' in msg:
            # Optional: Log or send goodbye message
            pass
        
        # ========== COMMANDS ==========
        if text and text.startswith('/'):
            parts = text.split()
            cmd = parts[0].lower().replace('@', '').split('/')[-1]
            args = parts[1:] if len(parts) > 1 else []
            
            # Check if command is disabled
            if is_command_disabled(chat_id, cmd) and not is_admin(chat_id, user_id):
                continue
            
            # ========== HELP ==========
            if cmd == 'help' or cmd == 'start':
                send_message(chat_id, texts['help_text'], reply_to_message_id=msg_id, parse_mode='Markdown')
            
            # ========== INFO ==========
            elif cmd == 'info':
                send_message(chat_id, texts['bot_info'], reply_to_message_id=msg_id, parse_mode='Markdown')
            
            # ========== FILTERS ==========
            elif cmd == 'filter':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif len(args) < 2:
                    send_message(chat_id, texts['filter_usage'], reply_to_message_id=msg_id, parse_mode='Markdown')
                else:
                    keyword = args[0].lower()
                    response = ' '.join(args[1:])
                    
                    # Check filter type
                    is_photo = response.startswith('photo:')
                    is_gif = response.startswith('gif:')
                    is_sticker = response.startswith('sticker:')
                    is_video = response.startswith('video:')
                    
                    media_url = None
                    text_response = response
                    
                    if is_photo:
                        media_url = response[6:]
                        text_response = None
                        filter_type = 'photo'
                    elif is_gif:
                        media_url = response[4:]
                        text_response = None
                        filter_type = 'gif'
                    elif is_sticker:
                        media_url = response[8:]
                        text_response = None
                        filter_type = 'sticker'
                    elif is_video:
                        media_url = response[6:]
                        text_response = None
                        filter_type = 'video'
                    else:
                        filter_type = 'text'
                    
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT OR REPLACE INTO filters 
                            (chat_id, keyword, response, is_photo, is_gif, is_sticker, is_video, media_url)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (chat_id_str, keyword, text_response, 
                              is_photo, is_gif, is_sticker, is_video, media_url))
                    
                    if filter_type == 'photo':
                        send_message(chat_id, texts['filter_set_photo'].format(word=keyword), 
                                   reply_to_message_id=msg_id)
                    elif filter_type == 'gif':
                        send_message(chat_id, texts['filter_set_gif'].format(word=keyword), 
                                   reply_to_message_id=msg_id)
                    elif filter_type == 'sticker':
                        send_message(chat_id, texts['filter_set_sticker'].format(word=keyword), 
                                   reply_to_message_id=msg_id)
                    elif filter_type == 'video':
                        send_message(chat_id, texts['filter_set_video'].format(word=keyword), 
                                   reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['filter_set_text'].format(word=keyword), 
                                   reply_to_message_id=msg_id)
            
            elif cmd == 'stop':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, "📝 Usage: /stop <keyword>", reply_to_message_id=msg_id)
                else:
                    keyword = args[0].lower()
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('DELETE FROM filters WHERE chat_id = ? AND keyword = ?', 
                                     (chat_id_str, keyword))
                    send_message(chat_id, texts['filter_deleted'].format(word=keyword), 
                               reply_to_message_id=msg_id)
            
            elif cmd == 'filters':
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        SELECT keyword, response, is_photo, is_gif, is_sticker, is_video, media_url 
                        FROM filters WHERE chat_id = ? ORDER BY keyword
                    ''', (chat_id_str,))
                    filters_list = cursor.fetchall()
                    
                    if filters_list:
                        filter_text = texts['filters_list']
                        for f in filters_list:
                            if f['is_gif']:
                                filter_text += f"🎬 • `{f['keyword']}`\n"
                            elif f['is_photo']:
                                filter_text += f"📸 • `{f['keyword']}`\n"
                            elif f['is_sticker']:
                                filter_text += f"🔖 • `{f['keyword']}`\n"
                            elif f['is_video']:
                                filter_text += f"🎥 • `{f['keyword']}`\n"
                            else:
                                filter_text += f"📝 • `{f['keyword']}` → {f['response'][:50]}\n"
                        send_message(chat_id, filter_text, reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['no_filters'], reply_to_message_id=msg_id)
            
            # ========== WELCOME ==========
            elif cmd == 'setwelcome':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, texts['welcome_usage'], reply_to_message_id=msg_id, parse_mode='Markdown')
                else:
                    if args[0].lower() == 'enable':
                        with db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute('''
                                INSERT OR REPLACE INTO welcome_settings (chat_id, is_enabled)
                                VALUES (?, 1)
                                ON CONFLICT(chat_id) DO UPDATE SET is_enabled = 1
                            ''', (chat_id_str,))
                        send_message(chat_id, texts['welcome_enabled'], reply_to_message_id=msg_id)
                    
                    elif args[0].lower() == 'disable':
                        with db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute('''
                                INSERT OR REPLACE INTO welcome_settings (chat_id, is_enabled)
                                VALUES (?, 0)
                                ON CONFLICT(chat_id) DO UPDATE SET is_enabled = 0
                            ''', (chat_id_str,))
                        send_message(chat_id, texts['welcome_disabled'], reply_to_message_id=msg_id)
                    
                    elif args[0].lower() == 'pin' and len(args) > 1:
                        if args[1].lower() == 'on':
                            with db.get_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute('''
                                    INSERT OR REPLACE INTO welcome_settings (chat_id, pin_enabled)
                                    VALUES (?, 1)
                                    ON CONFLICT(chat_id) DO UPDATE SET pin_enabled = 1
                                ''', (chat_id_str,))
                            send_message(chat_id, texts['welcome_pin_enabled'], reply_to_message_id=msg_id)
                        elif args[1].lower() == 'off':
                            with db.get_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute('''
                                    INSERT OR REPLACE INTO welcome_settings (chat_id, pin_enabled)
                                    VALUES (?, 0)
                                    ON CONFLICT(chat_id) DO UPDATE SET pin_enabled = 0
                                ''', (chat_id_str,))
                            send_message(chat_id, texts['welcome_pin_disabled'], reply_to_message_id=msg_id)
                    
                    elif args[0].lower() == 'delete' and len(args) > 1:
                        try:
                            minutes = int(args[1])
                            with db.get_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute('''
                                    INSERT OR REPLACE INTO welcome_settings (chat_id, delete_after_minutes)
                                    VALUES (?, ?)
                                    ON CONFLICT(chat_id) DO UPDATE SET delete_after_minutes = ?
                                ''', (chat_id_str, minutes, minutes))
                            send_message(chat_id, texts['welcome_auto_delete'].format(minutes=minutes), 
                                       reply_to_message_id=msg_id)
                        except ValueError:
                            send_message(chat_id, "❌ Please enter a valid number!", reply_to_message_id=msg_id)
                    
                    elif args[0].lower() == 'preview':
                        with db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute('SELECT message FROM welcome_settings WHERE chat_id = ?', (chat_id_str,))
                            result = cursor.fetchone()
                            welcome_text = result['message'] if result else texts['welcome']
                            
                            # Get member count
                            try:
                                url = f"https://api.telegram.org/bot{TOKEN}/getChatMembersCount"
                                response = requests.post(url, json={'chat_id': chat_id}, timeout=10)
                                members_count = response.json().get('result', 0) if response.ok else '?'
                            except:
                                members_count = '?'
                            
                            preview = welcome_text.replace('{user}', 'Test User')
                            preview = preview.replace('{first_name}', 'Test')
                            preview = preview.replace('{username}', '@testuser')
                            preview = preview.replace('{id}', '123456789')
                            preview = preview.replace('{group}', msg['chat'].get('title', 'Group'))
                            preview = preview.replace('{members}', str(members_count))
                            
                            send_message(chat_id, texts['welcome_preview'] + preview, 
                                       reply_to_message_id=msg_id)
                    
                    elif args[0].lower() == 'reset':
                        with db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute('DELETE FROM welcome_settings WHERE chat_id = ?', (chat_id_str,))
                        send_message(chat_id, texts['welcome_reset'], reply_to_message_id=msg_id)
                    
                    else:
                        welcome_text = ' '.join(args)
                        with db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute('''
                                INSERT OR REPLACE INTO welcome_settings (chat_id, message, updated_at)
                                VALUES (?, ?, CURRENT_TIMESTAMP)
                            ''', (chat_id_str, welcome_text))
                        send_message(chat_id, texts['welcome_set'], reply_to_message_id=msg_id)
            
            elif cmd == 'delwelcome':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('DELETE FROM welcome_settings WHERE chat_id = ?', (chat_id_str,))
                    send_message(chat_id, "✅ Welcome message has been deleted!", reply_to_message_id=msg_id)
            
            # ========== RULES ==========
            elif cmd == 'setrules':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, texts['rules_usage'], reply_to_message_id=msg_id)
                else:
                    rules_text = ' '.join(args)
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT OR REPLACE INTO rules (chat_id, rules_text, updated_at)
                            VALUES (?, ?, CURRENT_TIMESTAMP)
                        ''', (chat_id_str, rules_text))
                    send_message(chat_id, texts['rules_set'], reply_to_message_id=msg_id)
            
            elif cmd == 'rules':
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT rules_text FROM rules WHERE chat_id = ?', (chat_id_str,))
                    result = cursor.fetchone()
                    if result:
                        send_message(chat_id, f"{texts['rules']}\n{result['rules_text']}", 
                                   reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['no_rules'], reply_to_message_id=msg_id)
            
            # ========== NOTES ==========
            elif cmd == 'note':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, texts['group_only'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif len(args) < 2:
                    send_message(chat_id, texts['note_usage'], reply_to_message_id=msg_id)
                else:
                    name = args[0].lower()
                    content = ' '.join(args[1:])
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT OR REPLACE INTO notes (chat_id, name, content, created_by)
                            VALUES (?, ?, ?, ?)
                        ''', (chat_id_str, name, content, user_id))
                    send_message(chat_id, texts['note_saved'].format(name=name), reply_to_message_id=msg_id)
            
            elif cmd == 'get':
                if not args:
                    send_message(chat_id, texts['note_usage'], reply_to_message_id=msg_id)
                else:
                    name = args[0].lower()
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('SELECT content FROM notes WHERE chat_id = ? AND name = ?', 
                                     (chat_id_str, name))
                        result = cursor.fetchone()
                        if result:
                            send_message(chat_id, result['content'], reply_to_message_id=msg_id)
                        else:
                            send_message(chat_id, f"❌ Note '{name}' not found!", reply_to_message_id=msg_id)
            
            elif cmd == 'notes':
                with db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT name FROM notes WHERE chat_id = ? ORDER BY name', (chat_id_str,))
                    notes = cursor.fetchall()
                    if notes:
                        notes_text = texts['notes_list']
                        for note in notes:
                            notes_text += f"• /get {note['name']}\n"
                        send_message(chat_id, notes_text, reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['no_notes'], reply_to_message_id=msg_id)
            
            elif cmd == 'delnote':
                if not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, texts['note_usage'], reply_to_message_id=msg_id)
                else:
                    name = args[0].lower()
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('DELETE FROM notes WHERE chat_id = ? AND name = ?', 
                                     (chat_id_str, name))
                    send_message(chat_id, texts['note_deleted'].format(name=name), reply_to_message_id=msg_id)
            
            # ========== MODERATION ==========
            elif cmd == 'ban':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    reason = ' '.join(args) if args else 'No reason'
                    if ban_user(chat_id, target, True, reason):
                        send_message(chat_id, f"{texts['banned']}\nReason: {reason}", 
                                   reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['error_general'], reply_to_message_id=msg_id)
            
            elif cmd == 'kick':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    reason = ' '.join(args) if args else 'No reason'
                    if kick_user(chat_id, target, reason):
                        send_message(chat_id, f"{texts['kicked']}\nReason: {reason}", 
                                   reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['error_general'], reply_to_message_id=msg_id)
            
            elif cmd == 'mute':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    minutes = int(args[0]) if args and args[0].isdigit() else 5
                    reason = ' '.join(args[1:]) if len(args) > 1 else 'No reason'
                    mute_user(chat_id, target, minutes, reason)
                    send_message(chat_id, f"{texts['muted']} for {minutes} minutes!\nReason: {reason}", 
                               reply_to_message_id=msg_id)
            
            elif cmd == 'unmute':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    unmute_user(chat_id, target)
                    send_message(chat_id, texts['unmuted'], reply_to_message_id=msg_id)
            
            elif cmd == 'warn':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    reason = ' '.join(args) if args else 'No reason'
                    
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            INSERT INTO warnings (chat_id, user_id, count, reason)
                            VALUES (?, ?, 1, ?)
                            ON CONFLICT(chat_id, user_id) DO UPDATE SET 
                                count = count + 1,
                                reason = ?,
                                last_warning = CURRENT_TIMESTAMP
                        ''', (chat_id_str, target, reason, reason))
                        
                        cursor.execute('SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?', 
                                     (chat_id_str, target))
                        result = cursor.fetchone()
                        count = result['count']
                    
                    send_message(chat_id, texts['warn_count'].format(count=count), 
                               reply_to_message_id=msg_id)
                    
                    if count >= 3:
                        ban_user(chat_id, target, True, f"3 warnings: {reason}")
                        send_message(chat_id, texts['warn_banned'], reply_to_message_id=msg_id)
                        with db.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute('DELETE FROM warnings WHERE chat_id = ? AND user_id = ?', 
                                         (chat_id_str, target))
            
            elif cmd == 'warns':
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
                    send_message(chat_id, texts['warns_count'].format(count=count), 
                               reply_to_message_id=msg_id)
            
            elif cmd == 'resetwarns':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['need_reply'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('DELETE FROM warnings WHERE chat_id = ? AND user_id = ?', 
                                     (chat_id_str, target))
                    send_message(chat_id, texts['resetwarns'], reply_to_message_id=msg_id)
            
            # ========== PIN ==========
            elif cmd == 'pin':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, texts['pin_usage'], reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    target_msg_id = msg['reply_to_message']['message_id']
                    if pin_message(chat_id, target_msg_id):
                        send_message(chat_id, texts['pinned'], reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['error_general'], reply_to_message_id=msg_id)
            
            elif cmd == 'unpin':
                if not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    if unpin_message(chat_id):
                        send_message(chat_id, texts['unpinned'], reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, texts['error_general'], reply_to_message_id=msg_id)
            
            # ========== ADMINS ==========
            elif cmd == 'admins':
                url = f"https://api.telegram.org/bot{TOKEN}/getChatAdministrators"
                try:
                    response = requests.post(url, json={'chat_id': chat_id}, timeout=10)
                    if response.ok and response.json().get('ok'):
                        admins = response.json()['result']
                        admin_text = texts['admin_list']
                        for admin in admins:
                            user = admin['user']
                            name = user.get('first_name', '')
                            if user.get('username'):
                                name += f" (@{user['username']})"
                            admin_text += f"• {name}\n"
                        send_message(chat_id, admin_text, reply_to_message_id=msg_id)
                except Exception as e:
                    logger.error(f"Error getting admins: {e}")
                    send_message(chat_id, texts['error_general'], reply_to_message_id=msg_id)
            
            # ========== LANGUAGE ==========
            elif cmd == 'language':
                if not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args or args[0] not in ['en', 'sq', 'mk']:
                    send_message(chat_id, texts['language_usage'], reply_to_message_id=msg_id)
                else:
                    new_lang = args[0]
                    set_chat_language(chat_id, new_lang)
                    send_message(chat_id, texts['language_changed'].format(lang=new_lang.upper()), 
                               reply_to_message_id=msg_id)
            
            # ========== STATS ==========
            elif cmd == 'stats':
                if not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                else:
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        
                        cursor.execute('SELECT COUNT(*) as c FROM filters WHERE chat_id = ?', (chat_id_str,))
                        filters_count = cursor.fetchone()['c']
                        
                        cursor.execute('SELECT COUNT(*) as c FROM notes WHERE chat_id = ?', (chat_id_str,))
                        notes_count = cursor.fetchone()['c']
                        
                        cursor.execute('SELECT COUNT(*) as c FROM warnings WHERE chat_id = ?', (chat_id_str,))
                        warnings_count = cursor.fetchone()['c']
                        
                        cursor.execute('SELECT COUNT(*) as c FROM muted_users WHERE chat_id = ? AND until > datetime("now")', 
                                     (chat_id_str,))
                        muted_count = cursor.fetchone()['c']
                        
                        cursor.execute('SELECT COUNT(*) as c FROM banned_users WHERE chat_id = ?', (chat_id_str,))
                        banned_count = cursor.fetchone()['c']
                    
                    stats_text = (
                        f"{texts['stats']}"
                        f"🔍 Filters: {filters_count}\n"
                        f"📝 Notes: {notes_count}\n"
                        f"⚠️ Active warnings: {warnings_count}\n"
                        f"🔇 Muted: {muted_count}\n"
                        f"🚫 Banned: {banned_count}"
                    )
                    send_message(chat_id, stats_text, reply_to_message_id=msg_id)
            
            # ========== DISABLE/ENABLE COMMANDS ==========
            elif cmd == 'disable':
                if not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, "📝 Usage: /disable <command>", reply_to_message_id=msg_id)
                else:
                    command = args[0].lower().replace('/', '')
                    if command in ['enable', 'disable', 'help', 'start', 'info']:
                        send_message(chat_id, "❌ Cannot disable this command!", reply_to_message_id=msg_id)
                    else:
                        disable_command(chat_id, command)
                        send_message(chat_id, f"✅ Command '/{command}' has been disabled!", 
                                   reply_to_message_id=msg_id)
            
            elif cmd == 'enable':
                if not is_admin(chat_id, user_id):
                    send_message(chat_id, texts['admin_only'], reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, "📝 Usage: /enable <command>", reply_to_message_id=msg_id)
                else:
                    command = args[0].lower().replace('/', '')
                    enable_command(chat_id, command)
                    send_message(chat_id, f"✅ Command '/{command}' has been enabled!", 
                               reply_to_message_id=msg_id)
        
        # ========== FILTERS (LIKE ROSE) ==========
        elif text:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Check if user is muted
                cursor.execute('''
                    SELECT until FROM muted_users 
                    WHERE chat_id = ? AND user_id = ? AND until > datetime("now")
                ''', (chat_id_str, user_id))
                if cursor.fetchone():
                    send_message(chat_id, texts['muted_warning'], reply_to_message_id=msg_id)
                    return jsonify({'ok': True})
                
                # Check filters
                cursor.execute('''
                    SELECT keyword, response, is_photo, is_gif, is_sticker, is_video, media_url 
                    FROM filters 
                    WHERE chat_id = ?
                    ORDER BY length(keyword) DESC
                ''', (chat_id_str,))
                filters_list = cursor.fetchall()
                
                text_lower = text.lower()
                for filter_item in filters_list:
                    if filter_item['keyword'] in text_lower:
                        # Send response as reply (like Rose - no message deletion)
                        if filter_item['is_gif'] and filter_item['media_url']:
                            caption = filter_item['response'] if filter_item['response'] else None
                            send_gif(chat_id, filter_item['media_url'], caption=caption, 
                                   reply_to_message_id=msg_id)
                        elif filter_item['is_photo'] and filter_item['media_url']:
                            caption = filter_item['response'] if filter_item['response'] else None
                            send_photo(chat_id, filter_item['media_url'], caption=caption, 
                                     reply_to_message_id=msg_id)
                        elif filter_item['is_sticker'] and filter_item['media_url']:
                            send_sticker(chat_id, filter_item['media_url'], 
                                       reply_to_message_id=msg_id)
                        elif filter_item['is_video'] and filter_item['media_url']:
                            caption = filter_item['response'] if filter_item['response'] else None
                            send_video(chat_id, filter_item['media_url'], caption=caption, 
                                     reply_to_message_id=msg_id)
                        else:
                            if filter_item['response']:
                                send_message(chat_id, filter_item['response'], 
                                           reply_to_message_id=msg_id)
                        break
        
        return jsonify({'ok': True})
        
    except Exception as e:
        logger.error(f"Error processing update: {e}", exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

# ==================== MAIN ====================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    
    if not os.path.exists('data'):
        os.makedirs('data')
    
    logger.info("=" * 50)
    logger.info("🤖 Rose Bot v2.3 - Starting...")
    logger.info(f"Port: {port}")
    logger.info(f"Token configured: {bool(TOKEN)}")
    logger.info("Features: Filters, Welcome, Rules, Notes, Moderation")
    logger.info("=" * 50)
    
    app.run(host='0.0.0.0', port=port, debug=False)

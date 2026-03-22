import os
import json
import requests
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from typing import Dict, List, Optional

# Konfigurimi
app = Flask(__name__)
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')

# Konfigurimi i logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Struktura e të dhënave
class BotData:
    def __init__(self):
        self.welcome_messages: Dict[str, str] = {}
        self.rules: Dict[str, str] = {}
        self.filters: Dict[str, Dict[str, str]] = {}
        self.warnings: Dict[str, Dict[int, int]] = {}
        self.muted_users: Dict[str, Dict[int, datetime]] = {}
        self.group_languages: Dict[str, str] = {}

data = BotData()

# Gjuhet e disponueshme
LANGUAGES = {
    'sq': {
        'welcome': "👋 Mirë se vini në grup!",
        'rules': "📜 Rregullat e grupit:",
        'warning': "⚠️ Paralajmërim",
        'banned': "🚫 Përdoruesi u ndalua",
        'kicked': "👢 Përdoruesi u përjashtua",
        'muted': "🔇 Përdoruesi u hesht",
        'filter': "⚠️ Mesazhi përmban fjalë të ndaluara!",
        'no_rules': "⚠️ Nuk ka rregulla të vendosura.",
        'no_welcome': "⚠️ Nuk ka mesazh mirëseardhjeje të vendosur."
    },
    'mk': {
        'welcome': "👋 Добредојде во групата!",
        'rules': "📜 Правила на групата:",
        'warning': "⚠️ Предупредување",
        'banned': "🚫 Корисникот е блокиран",
        'kicked': "👢 Корисникот е исфрлен",
        'muted': "🔇 Корисникот е занемен",
        'filter': "⚠️ Пораката содржи забранети зборови!",
        'no_rules': "⚠️ Нема поставено правила.",
        'no_welcome': "⚠️ Нема поставено добредојде порака."
    }
}

def get_lang(chat_id: str) -> str:
    """Merr gjuhën e grupit"""
    return data.group_languages.get(chat_id, 'sq')

def send_message(chat_id: int, text: str, reply_to_message_id: Optional[int] = None,
                 keyboard: Optional[List[List[Dict]]] = None):
    """Dërgon mesazh në Telegram"""
    if not TOKEN:
        return
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML'
    }
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
    if keyboard:
        payload['reply_markup'] = json.dumps({'inline_keyboard': keyboard})
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        logger.info(f"Message sent to {chat_id}")
        return response.json()
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

def delete_message(chat_id: int, message_id: int):
    """Fshin mesazhin"""
    if not TOKEN:
        return
    
    url = f"https://api.telegram.org/bot{TOKEN}/deleteMessage"
    try:
        requests.post(url, json={'chat_id': chat_id, 'message_id': message_id}, timeout=10)
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

def ban_user(chat_id: int, user_id: int) -> bool:
    """Ndalon përdoruesin"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/banChatMember"
    try:
        response = requests.post(url, json={'chat_id': chat_id, 'user_id': user_id}, timeout=10)
        return response.json().get('ok', False)
    except Exception as e:
        logger.error(f"Error banning user: {e}")
        return False

def mute_user(chat_id: int, user_id: int, duration: int = 300):
    """Hesht përdoruesin për X sekonda"""
    chat_id_str = str(chat_id)
    if chat_id_str not in data.muted_users:
        data.muted_users[chat_id_str] = {}
    
    data.muted_users[chat_id_str][user_id] = datetime.now() + timedelta(seconds=duration)

def is_muted(chat_id: int, user_id: int) -> bool:
    """Kontrollon nëse përdoruesi është i heshtur"""
    chat_id_str = str(chat_id)
    if chat_id_str in data.muted_users and user_id in data.muted_users[chat_id_str]:
        if datetime.now() < data.muted_users[chat_id_str][user_id]:
            return True
        else:
            del data.muted_users[chat_id_str][user_id]
    return False

def is_admin(chat_id: int, user_id: int) -> bool:
    """Kontrollon nëse përdoruesi është administrator"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/getChatAdministrators"
    try:
        response = requests.post(url, json={'chat_id': chat_id}, timeout=10)
        if response.json().get('ok'):
            admins = [admin['user']['id'] for admin in response.json()['result']]
            return user_id in admins
    except Exception as e:
        logger.error(f"Error checking admin: {e}")
    
    return False

@app.route('/', methods=['POST', 'GET'])
def index():
    if request.method == 'GET':
        return jsonify({
            'status': 'running',
            'token_configured': bool(TOKEN),
            'version': '2.0',
            'message': 'Bot profesional për menaxhimin e grupeve'
        })
    
    try:
        update = request.get_json()
        if not update or 'message' not in update:
            return jsonify({'ok': True})
        
        message = update['message']
        chat = message.get('chat', {})
        chat_id = chat.get('id')
        chat_type = chat.get('type')
        message_id = message.get('message_id')
        text = message.get('text', '')
        from_user = message.get('from', {})
        user_id = from_user.get('id')
        
        chat_id_str = str(chat_id)
        lang = get_lang(chat_id_str)
        
        # Anëtarë të rinj
        if 'new_chat_members' in message:
            for member in message['new_chat_members']:
                first_name = member.get('first_name', 'Përdorues')
                username = member.get('username', first_name)
                
                if chat_id_str in data.welcome_messages:
                    welcome = data.welcome_messages[chat_id_str]
                    welcome = welcome.replace('{user}', f'@{username}')
                    welcome = welcome.replace('{first_name}', first_name)
                    welcome = welcome.replace('{username}', username)
                else:
                    welcome = f"{LANGUAGES[lang]['welcome']} {first_name}!"
                
                send_message(chat_id, welcome)
                
                if chat_id_str in data.rules:
                    send_message(chat_id, f"{LANGUAGES[lang]['rules']}\n{data.rules[chat_id_str]}")
            
            return jsonify({'ok': True})
        
        # Kontrollo nëse përdoruesi është i heshtur
        if is_muted(chat_id, user_id):
            delete_message(chat_id, message_id)
            return jsonify({'ok': True})
        
        # Komandat
        if text and text.startswith('/'):
            parts = text.split()
            command = parts[0].lower()
            args = parts[1:]
            
            # /start
            if command == '/start':
                keyboard = [[
                    {'text': '🇦🇱 Shqip', 'callback_data': 'lang_sq'},
                    {'text': '🇲🇰 Македонски', 'callback_data': 'lang_mk'}
                ]]
                send_message(chat_id, 
                    "🤖 **Bot Profesional për Menaxhimin e Grupeve**\n\n"
                    "📋 **Komandat:**\n"
                    "🔧 `/setwelcome` - Vendos mirëseardhjen\n"
                    "📜 `/setrules` - Vendos rregullat\n"
                    "🚫 `/setfilter` - Vendos filtrat\n"
                    "🔇 `/mute` - Hesht përdoruesin\n"
                    "👢 `/kick` - Përjashton përdoruesin\n"
                    "🚫 `/ban` - Ndalon përdoruesin\n"
                    "⚠️ `/warn` - Paralajmëron përdoruesin\n"
                    "🌐 `/language` - Ndrysho gjuhën\n\n"
                    "👑 **Vetëm administratorët** mund të përdorin komandat.",
                    reply_to_message_id=message_id,
                    keyboard=keyboard
                )
            
            # /language
            elif command == '/language':
                keyboard = [[
                    {'text': '🇦🇱 Shqip', 'callback_data': 'lang_sq'},
                    {'text': '🇲🇰 Македонски', 'callback_data': 'lang_mk'}
                ]]
                send_message(chat_id, "🌐 Zgjidhni gjuhën:\n🌐 Изберете јазик:", 
                           reply_to_message_id=message_id, keyboard=keyboard)
            
            # /setwelcome
            elif command == '/setwelcome':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, "⚠️ Kjo komandë funksionon vetëm në grupe!", 
                               reply_to_message_id=message_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët mund të përdorin këtë komandë!", 
                               reply_to_message_id=message_id)
                elif not args:
                    send_message(chat_id, 
                        "📝 **Përdorimi:** `/setwelcome <mesazhi>`\n\n"
                        "**Variablat:**\n"
                        "`{user}` - Përmend përdoruesin\n"
                        "`{first_name}` - Emri\n"
                        "`{username}` - Username\n\n"
                        "**Shembull:**\n"
                        "`/setwelcome Mirë se vini {user}!`",
                        reply_to_message_id=message_id)
                else:
                    data.welcome_messages[chat_id_str] = ' '.join(args)
                    send_message(chat_id, "✅ Mirëseardhja u vendos!", reply_to_message_id=message_id)
            
            # /setrules
            elif command == '/setrules':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, "⚠️ Kjo komandë funksionon vetëm në grupe!", 
                               reply_to_message_id=message_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët mund të përdorin këtë komandë!", 
                               reply_to_message_id=message_id)
                elif not args:
                    send_message(chat_id, "📝 Përdorimi: `/setrules <rregullat>`\n\nShembull:\n`/setrules 1. Respekti 2. Njo spam`", 
                               reply_to_message_id=message_id)
                else:
                    data.rules[chat_id_str] = ' '.join(args)
                    send_message(chat_id, "✅ Rregullat u vendosën!", reply_to_message_id=message_id)
            
            # /rules
            elif command == '/rules':
                rules = data.rules.get(chat_id_str)
                if rules:
                    send_message(chat_id, f"{LANGUAGES[lang]['rules']}\n{rules}", 
                               reply_to_message_id=message_id)
                else:
                    send_message(chat_id, LANGUAGES[lang]['no_rules'], reply_to_message_id=message_id)
            
            # /setfilter
            elif command == '/setfilter':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, "⚠️ Kjo komandë funksionon vetëm në grupe!", 
                               reply_to_message_id=message_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët mund të përdorin këtë komandë!", 
                               reply_to_message_id=message_id)
                elif len(args) < 2:
                    send_message(chat_id, "📝 Përdorimi: `/setfilter <fjalë> <përgjigje>`\n\nShembull:\n`/setfilter spam Mos spamoni!`", 
                               reply_to_message_id=message_id)
                else:
                    word = args[0].lower()
                    response = ' '.join(args[1:])
                    
                    if chat_id_str not in data.filters:
                        data.filters[chat_id_str] = {}
                    
                    data.filters[chat_id_str][word] = response
                    send_message(chat_id, f"✅ Filtri për '{word}' u vendos!", reply_to_message_id=message_id)
            
            # /ban
            elif command == '/ban':
                if not message.get('reply_to_message'):
                    send_message(chat_id, "⚠️ Përgjigjuni mesazhit të përdoruesit që dëshironi të ndaloni.", 
                               reply_to_message_id=message_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët mund të përdorin këtë komandë!", 
                               reply_to_message_id=message_id)
                else:
                    target_id = message['reply_to_message']['from']['id']
                    if ban_user(chat_id, target_id):
                        send_message(chat_id, f"{LANGUAGES[lang]['banned']}!", reply_to_message_id=message_id)
                    else:
                        send_message(chat_id, "❌ Gabim gjatë ndalimit!", reply_to_message_id=message_id)
            
            # /kick
            elif command == '/kick':
                if not message.get('reply_to_message'):
                    send_message(chat_id, "⚠️ Përgjigjuni mesazhit të përdoruesit që dëshironi të përjashtoni.", 
                               reply_to_message_id=message_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët mund të përdorin këtë komandë!", 
                               reply_to_message_id=message_id)
                else:
                    target_id = message['reply_to_message']['from']['id']
                    url_ban = f"https://api.telegram.org/bot{TOKEN}/banChatMember"
                    url_unban = f"https://api.telegram.org/bot{TOKEN}/unbanChatMember"
                    try:
                        requests.post(url_ban, json={'chat_id': chat_id, 'user_id': target_id}, timeout=10)
                        requests.post(url_unban, json={'chat_id': chat_id, 'user_id': target_id}, timeout=10)
                        send_message(chat_id, f"{LANGUAGES[lang]['kicked']}!", reply_to_message_id=message_id)
                    except Exception as e:
                        send_message(chat_id, f"❌ Gabim: {str(e)}", reply_to_message_id=message_id)
            
            # /mute
            elif command == '/mute':
                if not message.get('reply_to_message'):
                    send_message(chat_id, "⚠️ Përgjigjuni mesazhit të përdoruesit që dëshironi të heshtni.", 
                               reply_to_message_id=message_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët mund të përdorin këtë komandë!", 
                               reply_to_message_id=message_id)
                else:
                    duration = 300
                    if args and args[0].isdigit():
                        duration = int(args[0]) * 60
                    
                    target_id = message['reply_to_message']['from']['id']
                    mute_user(chat_id, target_id, duration)
                    minutes = duration // 60
                    send_message(chat_id, f"{LANGUAGES[lang]['muted']} për {minutes} minuta!", 
                               reply_to_message_id=message_id)
            
            # /warn
            elif command == '/warn':
                if not message.get('reply_to_message'):
                    send_message(chat_id, "⚠️ Përgjigjuni mesazhit të përdoruesit që dëshironi të paralajmëroni.", 
                               reply_to_message_id=message_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët mund të përdorin këtë komandë!", 
                               reply_to_message_id=message_id)
                else:
                    target_id = message['reply_to_message']['from']['id']
                    
                    if chat_id_str not in data.warnings:
                        data.warnings[chat_id_str] = {}
                    
                    data.warnings[chat_id_str][target_id] = data.warnings[chat_id_str].get(target_id, 0) + 1
                    count = data.warnings[chat_id_str][target_id]
                    
                    send_message(chat_id, f"{LANGUAGES[lang]['warning']} {count}/3", 
                               reply_to_message_id=message_id)
                    
                    if count >= 3:
                        if ban_user(chat_id, target_id):
                            send_message(chat_id, f"{LANGUAGES[lang]['banned']}!", reply_to_message_id=message_id)
                            del data.warnings[chat_id_str][target_id]
        
        # Filtra për mesazhet normale
        elif text:
            filters = data.filters.get(chat_id_str, {})
            text_lower = text.lower()
            
            for word, response in filters.items():
                if word in text_lower:
                    send_message(chat_id, f"⚠️ {response}", reply_to_message_id=message_id)
                    delete_message(chat_id, message_id)
                    break
        
        return jsonify({'ok': True})
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/callback', methods=['POST'])
def callback():
    """Për butonat inline"""
    try:
        data_cb = request.get_json()
        if data_cb and 'callback_query' in data_cb:
            query = data_cb['callback_query']
            chat_id = query['message']['chat']['id']
            message_id = query['message']['message_id']
            cb_data = query['data']
            
            if cb_data.startswith('lang_'):
                lang_code = cb_data.split('_')[1]
                data.group_languages[str(chat_id)] = lang_code
                
                lang_name = "Shqip" if lang_code == 'sq' else "Македонски"
                send_message(chat_id, f"🌐 Gjuha u ndryshua në: {lang_name}")
                
                delete_message(chat_id, message_id)
        
        return jsonify({'ok': True})
    except Exception as e:
        logger.error(f"Error in callback: {e}")
        return jsonify({'ok': False}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

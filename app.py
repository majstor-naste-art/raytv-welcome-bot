import os
import json
import requests
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

# Konfigurimi
app = Flask(__name__)
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')

# Konfigurimi i logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ruajtja e të dhënave
welcome_messages = {}
rules = {}
filters = {}
warnings = {}
muted_users = {}
group_languages = {}

LANGUAGES = {
    'sq': {
        'welcome': "👋 Mirë se vini në grup!",
        'rules': "📜 Rregullat e grupit:",
        'warning': "⚠️ Paralajmërim",
        'banned': "🚫 Përdoruesi u ndalua",
        'kicked': "👢 Përdoruesi u përjashtua",
        'muted': "🔇 Përdoruesi u hesht",
        'no_rules': "⚠️ Nuk ka rregulla të vendosura."
    },
    'mk': {
        'welcome': "👋 Добредојде во групата!",
        'rules': "📜 Правила на групата:",
        'warning': "⚠️ Предупредување",
        'banned': "🚫 Корисникот е блокиран",
        'kicked': "👢 Корисникот е исфрлен",
        'muted': "🔇 Корисникот е занемен",
        'no_rules': "⚠️ Нема поставено правила."
    }
}

def send_message(chat_id, text, reply_to_message_id=None):
    if not TOKEN:
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Error: {e}")

def delete_message(chat_id, message_id):
    if not TOKEN:
        return
    url = f"https://api.telegram.org/bot{TOKEN}/deleteMessage"
    try:
        requests.post(url, json={'chat_id': chat_id, 'message_id': message_id}, timeout=10)
    except Exception as e:
        logger.error(f"Error: {e}")

def ban_user(chat_id, user_id):
    if not TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TOKEN}/banChatMember"
    try:
        r = requests.post(url, json={'chat_id': chat_id, 'user_id': user_id}, timeout=10)
        return r.json().get('ok', False)
    except Exception as e:
        logger.error(f"Error: {e}")
        return False

def is_admin(chat_id, user_id):
    if not TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TOKEN}/getChatAdministrators"
    try:
        r = requests.post(url, json={'chat_id': chat_id}, timeout=10)
        if r.json().get('ok'):
            admins = [a['user']['id'] for a in r.json()['result']]
            return user_id in admins
    except Exception as e:
        logger.error(f"Error: {e}")
    return False

@app.route('/', methods=['POST', 'GET'])
def index():
    if request.method == 'GET':
        return jsonify({'status': 'running', 'token_configured': bool(TOKEN)})
    
    try:
        # Merr të dhënat
        if request.is_json:
            update = request.get_json()
        else:
            return jsonify({'ok': True})
        
        if not update or 'message' not in update:
            return jsonify({'ok': True})
        
        msg = update['message']
        chat_id = msg['chat']['id']
        msg_id = msg.get('message_id')
        text = msg.get('text', '')
        user_id = msg.get('from', {}).get('id')
        chat_type = msg.get('chat', {}).get('type')
        chat_id_str = str(chat_id)
        lang = group_languages.get(chat_id_str, 'sq')
        
        # Anëtarë të rinj
        if 'new_chat_members' in msg:
            for m in msg['new_chat_members']:
                name = m.get('first_name', 'Përdorues')
                username = m.get('username', name)
                welcome = welcome_messages.get(chat_id_str, LANGUAGES[lang]['welcome'])
                welcome = welcome.replace('{user}', f'@{username}')
                welcome = welcome.replace('{first_name}', name)
                welcome = welcome.replace('{username}', username)
                send_message(chat_id, welcome)
                if chat_id_str in rules:
                    send_message(chat_id, f"{LANGUAGES[lang]['rules']}\n{rules[chat_id_str]}")
            return jsonify({'ok': True})
        
        # Komandat
        if text and text.startswith('/'):
            parts = text.split()
            cmd = parts[0].lower()
            args = parts[1:]
            
            if cmd == '/start':
                send_message(chat_id, 
                    "🤖 **Bot për Menaxhimin e Grupeve**\n\n"
                    "📋 **Komandat:**\n"
                    "/setwelcome - Vendos mirëseardhjen\n"
                    "/setrules - Vendos rregullat\n"
                    "/rules - Shfaq rregullat\n"
                    "/setfilter - Vendos filtër\n"
                    "/ban - Ndalon përdoruesin\n"
                    "/kick - Përjashton përdoruesin\n"
                    "/mute - Hesht përdoruesin\n"
                    "/warn - Paralajmëron përdoruesin",
                    reply_to_message_id=msg_id)
            
            elif cmd == '/setwelcome':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, "⚠️ Funksionon vetëm në grupe!", reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët!", reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, "📝 Përdorimi: /setwelcome <mesazhi>\nVariablat: {user}, {first_name}, {username}", 
                               reply_to_message_id=msg_id)
                else:
                    welcome_messages[chat_id_str] = ' '.join(args)
                    send_message(chat_id, "✅ Mirëseardhja u vendos!", reply_to_message_id=msg_id)
            
            elif cmd == '/setrules':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, "⚠️ Funksionon vetëm në grupe!", reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët!", reply_to_message_id=msg_id)
                elif not args:
                    send_message(chat_id, "📝 Përdorimi: /setrules <rregullat>", reply_to_message_id=msg_id)
                else:
                    rules[chat_id_str] = ' '.join(args)
                    send_message(chat_id, "✅ Rregullat u vendosën!", reply_to_message_id=msg_id)
            
            elif cmd == '/rules':
                r = rules.get(chat_id_str)
                if r:
                    send_message(chat_id, f"{LANGUAGES[lang]['rules']}\n{r}", reply_to_message_id=msg_id)
                else:
                    send_message(chat_id, LANGUAGES[lang]['no_rules'], reply_to_message_id=msg_id)
            
            elif cmd == '/setfilter':
                if chat_type not in ['group', 'supergroup']:
                    send_message(chat_id, "⚠️ Funksionon vetëm në grupe!", reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët!", reply_to_message_id=msg_id)
                elif len(args) < 2:
                    send_message(chat_id, "📝 Përdorimi: /setfilter <fjalë> <përgjigje>", reply_to_message_id=msg_id)
                else:
                    word = args[0].lower()
                    resp = ' '.join(args[1:])
                    if chat_id_str not in filters:
                        filters[chat_id_str] = {}
                    filters[chat_id_str][word] = resp
                    send_message(chat_id, f"✅ Filtri për '{word}' u vendos!", reply_to_message_id=msg_id)
            
            elif cmd == '/ban':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, "⚠️ Përgjigjuni mesazhit!", reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët!", reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    if ban_user(chat_id, target):
                        send_message(chat_id, f"{LANGUAGES[lang]['banned']}!", reply_to_message_id=msg_id)
                    else:
                        send_message(chat_id, "❌ Gabim!", reply_to_message_id=msg_id)
            
            elif cmd == '/kick':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, "⚠️ Përgjigjuni mesazhit!", reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët!", reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    url_ban = f"https://api.telegram.org/bot{TOKEN}/banChatMember"
                    url_unban = f"https://api.telegram.org/bot{TOKEN}/unbanChatMember"
                    try:
                        requests.post(url_ban, json={'chat_id': chat_id, 'user_id': target}, timeout=10)
                        requests.post(url_unban, json={'chat_id': chat_id, 'user_id': target}, timeout=10)
                        send_message(chat_id, f"{LANGUAGES[lang]['kicked']}!", reply_to_message_id=msg_id)
                    except Exception as e:
                        send_message(chat_id, f"❌ Gabim!", reply_to_message_id=msg_id)
            
            elif cmd == '/mute':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, "⚠️ Përgjigjuni mesazhit!", reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët!", reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    duration = 300
                    if args and args[0].isdigit():
                        duration = int(args[0]) * 60
                    if chat_id_str not in muted_users:
                        muted_users[chat_id_str] = {}
                    muted_users[chat_id_str][target] = datetime.now() + timedelta(seconds=duration)
                    minutes = duration // 60
                    send_message(chat_id, f"{LANGUAGES[lang]['muted']} për {minutes} minuta!", reply_to_message_id=msg_id)
            
            elif cmd == '/warn':
                if not msg.get('reply_to_message'):
                    send_message(chat_id, "⚠️ Përgjigjuni mesazhit!", reply_to_message_id=msg_id)
                elif not is_admin(chat_id, user_id):
                    send_message(chat_id, "👑 Vetëm administratorët!", reply_to_message_id=msg_id)
                else:
                    target = msg['reply_to_message']['from']['id']
                    if chat_id_str not in warnings:
                        warnings[chat_id_str] = {}
                    warnings[chat_id_str][target] = warnings[chat_id_str].get(target, 0) + 1
                    count = warnings[chat_id_str][target]
                    send_message(chat_id, f"{LANGUAGES[lang]['warning']} {count}/3", reply_to_message_id=msg_id)
                    if count >= 3:
                        if ban_user(chat_id, target):
                            send_message(chat_id, f"{LANGUAGES[lang]['banned']}!", reply_to_message_id=msg_id)
                            del warnings[chat_id_str][target]
        
        # Filtra
        elif text:
            flt = filters.get(chat_id_str, {})
            text_lower = text.lower()
            for word, resp in flt.items():
                if word in text_lower:
                    send_message(chat_id, f"⚠️ {resp}", reply_to_message_id=msg_id)
                    delete_message(chat_id, msg_id)
                    break
        
        return jsonify({'ok': True})
        
    except Exception as e:
        logger.error(f"Error: {e}")
        return jsonify({'ok': False}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

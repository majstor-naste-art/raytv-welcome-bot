import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')

# Ruajtja e të dhënave
data = {
    'welcome_message': {},
    'filters': {},
    'rules': {}
}

warnings = {}  # Për paralajmërimet

def send_message(chat_id, text, reply_to_message_id=None):
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
    
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending message: {e}")

def delete_message(chat_id, message_id):
    """Fshin mesazhin"""
    if not TOKEN:
        return
    
    url = f"https://api.telegram.org/bot{TOKEN}/deleteMessage"
    try:
        requests.post(url, json={'chat_id': chat_id, 'message_id': message_id}, timeout=10)
    except Exception as e:
        print(f"Error deleting message: {e}")

def ban_user(chat_id, user_id):
    """Ndalon përdoruesin"""
    if not TOKEN:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/banChatMember"
    try:
        response = requests.post(url, json={'chat_id': chat_id, 'user_id': user_id}, timeout=10)
        return response.json().get('ok', False)
    except Exception as e:
        print(f"Error banning user: {e}")
        return False

@app.route('/', methods=['POST', 'GET'])
def index():
    if request.method == 'GET':
        return jsonify({
            'status': 'running',
            'token_configured': bool(TOKEN),
            'message': 'Bot is active. Send POST requests with Telegram updates.'
        })
    
    try:
        update = request.get_json()
        if not update or 'message' not in update:
            return jsonify({'ok': True})
        
        message = update['message']
        chat_id = message['chat']['id']
        message_id = message.get('message_id')
        text = message.get('text', '')
        from_user = message.get('from', {})
        
        # Anëtarë të rinj
        if 'new_chat_members' in message:
            chat_id_str = str(chat_id)
            for member in message['new_chat_members']:
                first_name = member.get('first_name', 'Përdorues')
                username = member.get('username', first_name)
                
                if chat_id_str in data.get('welcome_message', {}):
                    welcome = data['welcome_message'][chat_id_str]
                    welcome = welcome.replace('{user}', f'@{username}')
                    welcome = welcome.replace('{first_name}', first_name)
                    welcome = welcome.replace('{username}', username)
                else:
                    welcome = f"👋 Mirë se vini, {first_name}!"
                
                send_message(chat_id, welcome)
                
                if chat_id_str in data.get('rules', {}):
                    send_message(chat_id, f"📜 Rregullat:\n{data['rules'][chat_id_str]}")
            
            return jsonify({'ok': True})
        
        # Komandat
        if text and text.startswith('/'):
            parts = text.split()
            command = parts[0].lower()
            args = parts[1:]
            
            # /start
            if command == '/start':
                send_message(chat_id, 
                    "👋 Përshëndetje! Unë jam bot për menaxhimin e grupeve.\n\n"
                    "📋 **Komandat e disponueshme:**\n\n"
                    "🔧 **Konfigurimi:**\n"
                    "/setwelcome <mesazhi> - Vendos mirëseardhjen\n"
                    "/setrules <rregullat> - Vendos rregullat\n"
                    "/setfilter <fjalë> <përgjigje> - Vendos filtër\n\n"
                    "📜 **Informacioni:**\n"
                    "/rules - Shfaq rregullat\n\n"
                    "⚡ **Moderimi:**\n"
                    "/ban (reply) - Ndalon përdoruesin\n"
                    "/kick (reply) - Përjashton përdoruesin\n"
                    "/warn (reply) - Paralajmëron përdoruesin",
                    reply_to_message_id=message_id
                )
            
            # /setwelcome
            elif command == '/setwelcome':
                if not args:
                    send_message(chat_id, "📝 Përdorimi: /setwelcome <mesazhi>\n\nVariablat: {user}, {first_name}, {username}", 
                                reply_to_message_id=message_id)
                else:
                    data['welcome_message'][str(chat_id)] = ' '.join(args)
                    send_message(chat_id, "✅ Mirëseardhja u vendos!", reply_to_message_id=message_id)
            
            # /setrules
            elif command == '/setrules':
                if not args:
                    send_message(chat_id, "📝 Përdorimi: /setrules <rregullat>", reply_to_message_id=message_id)
                else:
                    data['rules'][str(chat_id)] = ' '.join(args)
                    send_message(chat_id, "✅ Rregullat u vendosën!", reply_to_message_id=message_id)
            
            # /rules
            elif command == '/rules':
                rules = data.get('rules', {}).get(str(chat_id))
                if rules:
                    send_message(chat_id, f"📜 **Rregullat e grupit:**\n{rules}", reply_to_message_id=message_id)
                else:
                    send_message(chat_id, "⚠️ Nuk ka rregulla të vendosura. Përdorni /setrules për të vendosur rregullat.", 
                                reply_to_message_id=message_id)
            
            # /setfilter
            elif command == '/setfilter':
                if len(args) < 2:
                    send_message(chat_id, "📝 Përdorimi: /setfilter <fjalë> <përgjigje>", reply_to_message_id=message_id)
                else:
                    word = args[0].lower()
                    response = ' '.join(args[1:])
                    chat_id_str = str(chat_id)
                    
                    if chat_id_str not in data.get('filters', {}):
                        data['filters'][chat_id_str] = {}
                    
                    data['filters'][chat_id_str][word] = response
                    send_message(chat_id, f"✅ Filtri për '{word}' u vendos!", reply_to_message_id=message_id)
            
            # /ban
            elif command == '/ban':
                if not message.get('reply_to_message'):
                    send_message(chat_id, "⚠️ Përgjigjuni mesazhit të përdoruesit që dëshironi të ndaloni.", 
                                reply_to_message_id=message_id)
                else:
                    user_id = message['reply_to_message']['from']['id']
                    if ban_user(chat_id, user_id):
                        send_message(chat_id, "✅ Përdoruesi u ndalua!", reply_to_message_id=message_id)
                    else:
                        send_message(chat_id, "❌ Gabim gjatë ndalimit!", reply_to_message_id=message_id)
            
            # /kick
            elif command == '/kick':
                if not message.get('reply_to_message'):
                    send_message(chat_id, "⚠️ Përgjigjuni mesazhit të përdoruesit që dëshironi të përjashtoni.", 
                                reply_to_message_id=message_id)
                else:
                    user_id = message['reply_to_message']['from']['id']
                    # Ban then unban për kick
                    url_ban = f"https://api.telegram.org/bot{TOKEN}/banChatMember"
                    url_unban = f"https://api.telegram.org/bot{TOKEN}/unbanChatMember"
                    try:
                        requests.post(url_ban, json={'chat_id': chat_id, 'user_id': user_id}, timeout=10)
                        requests.post(url_unban, json={'chat_id': chat_id, 'user_id': user_id}, timeout=10)
                        send_message(chat_id, "✅ Përdoruesi u përjashtua!", reply_to_message_id=message_id)
                    except Exception as e:
                        send_message(chat_id, f"❌ Gabim: {str(e)}", reply_to_message_id=message_id)
            
            # /warn
            elif command == '/warn':
                if not message.get('reply_to_message'):
                    send_message(chat_id, "⚠️ Përgjigjuni mesazhit të përdoruesit që dëshironi të paralajmëroni.", 
                                reply_to_message_id=message_id)
                else:
                    chat_id_str = str(chat_id)
                    user_id = message['reply_to_message']['from']['id']
                    
                    if chat_id_str not in warnings:
                        warnings[chat_id_str] = {}
                    
                    warnings[chat_id_str][user_id] = warnings[chat_id_str].get(user_id, 0) + 1
                    count = warnings[chat_id_str][user_id]
                    
                    send_message(chat_id, f"⚠️ Paralajmërim {count}/3", reply_to_message_id=message_id)
                    
                    if count >= 3:
                        if ban_user(chat_id, user_id):
                            send_message(chat_id, "🚫 Përdoruesi u ndalua pas 3 paralajmërimeve!", 
                                        reply_to_message_id=message_id)
                            del warnings[chat_id_str][user_id]
                        else:
                            send_message(chat_id, "❌ Gabim gjatë ndalimit!", reply_to_message_id=message_id)
        
        # Filtra për mesazhet normale
        elif text:
            chat_id_str = str(chat_id)
            filters = data.get('filters', {}).get(chat_id_str, {})
            text_lower = text.lower()
            
            for word, response in filters.items():
                if word in text_lower:
                    send_message(chat_id, f"⚠️ {response}", reply_to_message_id=message_id)
                    delete_message(chat_id, message_id)
                    break
        
        return jsonify({'ok': True})
        
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

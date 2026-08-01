import telebot
from telebot import types
import imaplib
import email
from email.header import decode_header
import psycopg2
import logging
import requests
from datetime import datetime
import re
import html
import os
import threading
import time
import hmac
import base64
import struct
import hashlib
from flask import Flask

# ==========================================
# ⚙️ CONFIGURATIONS & LOGGING
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = '8465423862:AAHkZn88S_jr1aZpBZXzJb_EUxLSXscPZzo'
bot = telebot.TeleBot(BOT_TOKEN)

# 👑 ADMIN CONFIG
ADMIN_ID = 5605925198 
ADMIN_USERNAME_LINK = "[@sayem6t9](https://t.me/sayem6t9)"
BANNED_MSG = f"🚫 **You have been BANNED from using this bot!**\n\nTo request an unban, please message the Admin: {ADMIN_USERNAME_LINK}"
MAINTENANCE_MSG = "🛠️ **Bot is under Maintenance!**\n\nThe Admin is currently updating the system. Please try again later."

# 🐘 SUPABASE POSTGRESQL DATABASE URL
DATABASE_URL = "postgresql://postgres.cvqaqgqzlgbrlntvvlfn:WQsa9069%23%2A6T9@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# ==========================================
# 🧹 STRICT UI TRACKER
# ==========================================
chat_history = {}

def track_message(chat_id, message_id):
    if chat_id not in chat_history: chat_history[chat_id] = []
    if message_id not in chat_history[chat_id]: chat_history[chat_id].append(message_id)

def clear_chat_history(chat_id, keep_message_id=None):
    if chat_id in chat_history:
        for msg_id in chat_history[chat_id]:
            if msg_id != keep_message_id:
                try: bot.delete_message(chat_id, msg_id)
                except: pass
        chat_history[chat_id] = []
        if keep_message_id: chat_history[chat_id].append(keep_message_id)

# ==========================================
# 🌐 FLASK SERVER
# ==========================================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is Running Perfectly!", 200

# ==========================================
# 💾 DATABASE MANAGEMENT & SETTINGS
# ==========================================
def init_db():
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cursor = conn.cursor()
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, email TEXT, password TEXT, provider TEXT, refresh_token TEXT, client_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS email_cache (user_id BIGINT, idx INTEGER, subject TEXT, sender TEXT, full_content TEXT, PRIMARY KEY (user_id, idx))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS user_settings (user_id BIGINT PRIMARY KEY, api_key TEXT, base_email TEXT, base_password TEXT, temp_alias TEXT, temp_provider TEXT, temp_name TEXT, username TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS bulk_accounts (id SERIAL, owner_id BIGINT, email TEXT PRIMARY KEY, password TEXT, provider TEXT, refresh_token TEXT, client_id TEXT, is_used BOOLEAN DEFAULT FALSE, used_at TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS purchase_history (owner_id BIGINT, email TEXT, password TEXT, token TEXT, client_id TEXT, order_id TEXT, provider TEXT, purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS alias_history (id SERIAL PRIMARY KEY, owner_id BIGINT, email TEXT, password TEXT, provider TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS banned_users (user_id BIGINT PRIMARY KEY)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT)''')
        
        cursor.execute("INSERT INTO bot_settings (key, value) VALUES ('global_auto_delete', '1') ON CONFLICT (key) DO NOTHING")
        cursor.execute("INSERT INTO bot_settings (key, value) VALUES ('maintenance_mode', '0') ON CONFLICT (key) DO NOTHING")
        cursor.execute("INSERT INTO bot_settings (key, value) VALUES ('gmail_stock_alert', '1') ON CONFLICT (key) DO NOTHING")
        cursor.execute("INSERT INTO bot_settings (key, value) VALUES ('last_gmail_stock', '0') ON CONFLICT (key) DO NOTHING")
        
        try: cursor.execute("ALTER TABLE purchase_history ADD COLUMN password TEXT")
        except: pass
        try: cursor.execute("ALTER TABLE purchase_history ADD COLUMN token TEXT")
        except: pass
        try: cursor.execute("ALTER TABLE purchase_history ADD COLUMN client_id TEXT")
        except: pass
        try: cursor.execute("ALTER TABLE bulk_accounts ADD COLUMN is_used BOOLEAN DEFAULT FALSE")
        except: pass
        try: cursor.execute("ALTER TABLE bulk_accounts ADD COLUMN used_at TIMESTAMP")
        except: pass
        
        cursor.close()
        conn.close()
    except Exception as e: logging.error(f"Database init error: {e}")

def get_bot_setting(key, default='0'):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT value FROM bot_settings WHERE key=%s", (key,))
                row = cursor.fetchone()
                return row[0] if row else default
    except: return default

def set_bot_setting(key, value):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, value))
            conn.commit()
    except: pass

def is_maintenance(chat_id):
    if chat_id == ADMIN_ID: return False
    return get_bot_setting('maintenance_mode') == '1'

def get_all_user_ids():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as c:
                c.execute("SELECT user_id FROM user_settings")
                return [row[0] for row in c.fetchall()]
    except: return []

# ==========================================
# 🔄 BACKGROUND THREADS
# ==========================================
def auto_cleaner():
    while True:
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM bulk_accounts WHERE is_used=TRUE AND used_at < NOW() - INTERVAL '7 days'")
                conn.commit()
        except: pass
        time.sleep(43200)

def gmail_stock_tracker():
    while True:
        try:
            if get_bot_setting('gmail_stock_alert') == '1':
                current_stock_str = get_service_stock(None, "facebook")
                current_stock = int(current_stock_str) if current_stock_str.isdigit() else 0
                last_stock = int(get_bot_setting('last_gmail_stock', '0'))
                
                if current_stock > 0 and last_stock == 0:
                    users = get_all_user_ids()
                    for uid in users:
                        try: bot.send_message(uid, "🎉 **GMAIL STOCK ALERT!** 🛒\n━━━━━━━━━━━━━━━━━━━\n\nNew Facebook Gmails have just arrived in the server! Stock is now available.\n\n👇 Buy quickly before it runs out!", parse_mode="Markdown")
                        except: pass
                
                if current_stock != last_stock:
                    set_bot_setting('last_gmail_stock', str(current_stock))
        except Exception as e: logging.error(f"Stock Tracker Error: {e}")
        time.sleep(300)

def save_user_info(user_id, username):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id FROM user_settings WHERE user_id=%s", (user_id,))
                if not cursor.fetchone(): cursor.execute("INSERT INTO user_settings (user_id) VALUES (%s)", (user_id,))
                if username: cursor.execute("UPDATE user_settings SET username=%s WHERE user_id=%s", (username.lower(), user_id))
            conn.commit()
    except: pass

def is_user_banned(user_id):
    if user_id == ADMIN_ID: return False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM banned_users WHERE user_id=%s", (user_id,))
                return cursor.fetchone() is not None
    except: return False

def get_user_settings(user_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT api_key, base_email, base_password, temp_alias, temp_provider, temp_name FROM user_settings WHERE user_id=%s", (user_id,))
                row = cursor.fetchone()
                if row: return {"api_key": row[0], "base_email": row[1], "base_password": row[2], "temp_alias": row[3], "temp_provider": row[4], "temp_name": row[5]}
    except: pass
    return {"api_key": None, "base_email": None, "base_password": None, "temp_alias": None, "temp_provider": None, "temp_name": None}

def set_user_api_key(user_id, api_key):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id FROM user_settings WHERE user_id=%s", (user_id,))
                if cursor.fetchone(): cursor.execute("UPDATE user_settings SET api_key=%s WHERE user_id=%s", (api_key, user_id))
                else: cursor.execute("INSERT INTO user_settings (user_id, api_key) VALUES (%s, %s)", (user_id, api_key))
            conn.commit()
    except: pass

def set_user_base_credentials(user_id, base_email, base_password):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id FROM user_settings WHERE user_id=%s", (user_id,))
                if cursor.fetchone(): cursor.execute("UPDATE user_settings SET base_email=%s, base_password=%s WHERE user_id=%s", (base_email, base_password, user_id))
                else: cursor.execute("INSERT INTO user_settings (user_id, base_email, base_password) VALUES (%s, %s, %s)", (user_id, base_email, base_password))
            conn.commit()
    except: pass

def set_temp_data(user_id, alias, provider, name):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id FROM user_settings WHERE user_id=%s", (user_id,))
                if cursor.fetchone(): cursor.execute("UPDATE user_settings SET temp_alias=%s, temp_provider=%s, temp_name=%s WHERE user_id=%s", (alias, provider, name, user_id))
                else: cursor.execute("INSERT INTO user_settings (user_id, temp_alias, temp_provider, temp_name) VALUES (%s, %s, %s, %s)", (user_id, alias, provider, name))
            conn.commit()
    except: pass

def verify_yshshop_api(api_key):
    if len(api_key) < 20 or " " in api_key: return False
    try:
        if "balance" in requests.get("https://yshshopmails.com/v1/api/user", headers={"api_key": api_key}, timeout=5).json(): return True
    except: pass
    return False

def toggle_global_auto_delete():
    current = get_bot_setting('global_auto_delete')
    set_bot_setting('global_auto_delete', '0' if current == '1' else '1')

# ==========================================
# 🛠️ CORE LOGIC & PARSERS
# ==========================================
def clean_html_tags(raw_html):
    if not raw_html: return "No Content"
    text = html.unescape(raw_html)
    text = re.sub(r'<(style|script)[^>]*>[\s\S]*?</\1>', '', text, flags=re.IGNORECASE)
    clean_text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', clean_text).strip()

def get_html_body(msg):
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html": return part.get_payload(decode=True).decode(errors='ignore')
            for part in msg.walk():
                if part.get_content_type() == "text/plain": return part.get_payload(decode=True).decode(errors='ignore')
        else: return msg.get_payload(decode=True).decode(errors='ignore')
    except: pass
    return "No HTML Content Found."

def detect_facebook_otp(subject, content):
    combined_text = (subject + " " + content).lower()
    if "facebook" in combined_text or "fb" in combined_text:
        code_match = re.search(r'\b\d{6,8}\b', combined_text)
        if code_match: return code_match.group(0)
    return None

def get_service_stock(api_key, service_name):
    try:
        if service_name == "facebook":
            r = requests.get("https://facebook.yshshopmails.com/v1/api/stock", timeout=10).json()
            if isinstance(r, dict) and "stock" in r: return str(r["stock"])
            elif isinstance(r, (int, str)): return str(r)
        else:
            r = requests.get("https://yshshopmails.com/v1/stock", params={"service": service_name}, headers={"api_key": api_key} if api_key else {}, timeout=10).json()
            if isinstance(r, dict) and "stock" in r: return str(r["stock"])
    except: pass
    return "0"

def call_buy_api(api_key, service):
    if service == "facebook":
        try: return requests.get(f"https://yshshopmails.com/v1/api/create-order.php?key={api_key}&service=facebook", timeout=10).json()
        except Exception as e: return {"error": f"Gmail API Error: {str(e)}"}
    else:
        try:
            r1 = requests.get(f"https://yshshopmails.com/v2/api/pre-order.php?key={api_key}&service={service}", timeout=10).json()
            if r1.get("status") in ["error", "fail", False] or "error" in r1: return r1
            order_url = r1.get("url")
            if not order_url: return {"error": f"Step 1 Failed: {r1}"}
            if "fetch=" not in order_url: order_url += "&fetch=true"
            return requests.get(order_url, timeout=15).json()
        except Exception as e: return {"error": f"API Error: {str(e)}"}

def extract_account_details(resp, default_order):
    if not isinstance(resp, dict): return None, "", "", "", default_order
    acc_data = resp.get("data") if isinstance(resp.get("data"), dict) else resp
    raw_acc = acc_data.get("mail") or acc_data.get("email") or acc_data.get("account")
    if not raw_acc and isinstance(resp.get("data"), str) and "@" in resp.get("data"): raw_acc = resp.get("data")
        
    eml, pwd, token, client_id = "", "", "", ""
    ord_id = resp.get("order_id") or resp.get("id") or default_order
    
    if raw_acc and isinstance(raw_acc, str) and "@" in raw_acc:
        parts = raw_acc.split("|")
        eml = parts[0].strip()
        if len(parts) > 1: pwd = parts[1].strip()
        if len(parts) > 2: token = parts[2].strip()
        if len(parts) > 3: client_id = parts[3].strip()
    
    if not pwd: pwd = acc_data.get("password") or acc_data.get("pwd") or ""
    if not token: token = acc_data.get("token") or acc_data.get("refresh_token") or ""
    if not client_id: client_id = acc_data.get("client_id") or ""
    
    return eml, pwd, token, client_id, ord_id

def get_totp_token(secret):
    try:
        secret = secret.replace(' ', '').upper()
        if len(secret) % 8 != 0: secret += '=' * (8 - (len(secret) % 8))
        mac = hmac.new(base64.b32decode(secret, casefold=True), struct.pack(">Q", int(time.time() // 30)), hashlib.sha1).digest()
        offset = mac[-1] & 0x0f
        return str((struct.unpack('>L', mac[offset:offset+4])[0] & 0x7fffffff) % 1000000).zfill(6)
    except: return None

# ==========================================
# 📱 MAIN MENU INTERFACE
# ==========================================
@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    chat_id = message.chat.id
    save_user_info(chat_id, message.from_user.username)
    track_message(chat_id, message.message_id)
    clear_chat_history(chat_id)
    
    if is_user_banned(chat_id): return bot.send_message(chat_id, BANNED_MSG, parse_mode="Markdown")
    if is_maintenance(chat_id): return bot.send_message(chat_id, MAINTENANCE_MSG, parse_mode="Markdown")
    show_main_instruction(chat_id)

def show_main_instruction(chat_id, message_id=None):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("🛒 Buy Gmail", callback_data="action_buy_gmail"), types.InlineKeyboardButton("🔥 Buy Trust Mail", callback_data="action_buy_hotmail_menu"))
    markup.add(types.InlineKeyboardButton("🛠️ Zoho/Yandex Alias", callback_data="action_alias_maker"), types.InlineKeyboardButton("📊 Check Stock", callback_data="action_check_stock"))
    markup.add(types.InlineKeyboardButton("📁 My Bulk Accounts", callback_data="action_bulk_list"), types.InlineKeyboardButton("📜 History Center", callback_data="action_buy_history"))
    markup.add(types.InlineKeyboardButton("👤 Fake Name Gen", callback_data="action_fake_name"), types.InlineKeyboardButton("🔄 Refresh Inbox", callback_data="action_refresh_direct"))
    markup.add(types.InlineKeyboardButton("⚙️ Settings", callback_data="action_settings"))
    if chat_id == ADMIN_ID: markup.add(types.InlineKeyboardButton("👨‍💻 Admin Panel (Boss Only)", callback_data="action_admin_panel"))
    
    instruction_text = (
        "🤖 **Auto Secure Mail & OTP Reader Bot**\n\n"
        "**🔥 CLOUD SECURE BULK MODE ACTIVE!**\n"
        "1. Send a `.txt` file (It stays Private to you).\n"
        "2. Click **📁 My Bulk Accounts** to pick an email.\n\n"
        "**Manual Input Format:**\n"
        "🏢 **Zoho/Yandex:** `email|AppPassword`\n"
        "🔴 **Gmail:** `email@gmail.com|OrderID`\n"
        "🔥 **Hotmail/Outlook Trust:** `email|password|token|client_id`\n"
        "🔐 **2FA Code:** Send `Secret Key` (e.g. JBSWY3DPEHPK3PXP)"
    )
    
    if message_id:
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=instruction_text, parse_mode="Markdown", reply_markup=markup)
            track_message(chat_id, message_id)
            return
        except: pass
            
    sent_msg = bot.send_message(chat_id, instruction_text, parse_mode="Markdown", reply_markup=markup)
    track_message(chat_id, sent_msg.message_id)

# ==========================================
# 🕹️ BUTTON CALLBACK HANDLERS
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    track_message(chat_id, message_id)
    
    if is_user_banned(chat_id): return bot.answer_callback_query(call.id, "🚫 You are BANNED!", show_alert=True)
    if is_maintenance(chat_id) and call.data != "action_admin_panel" and not call.data.startswith("admin_"): 
        return bot.answer_callback_query(call.id, "🛠️ Bot is under Maintenance!", show_alert=True)

    if call.data == "action_menu":
        bot.clear_step_handler_by_chat_id(chat_id)
        clear_chat_history(chat_id, keep_message_id=message_id)
        show_main_instruction(chat_id, message_id=message_id)
        return

    # --- FAKE NAME GENERATOR ---
    elif call.data == "action_fake_name":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("👨 Male (US/UK)", callback_data="gen_name_male"),
            types.InlineKeyboardButton("👩 Female (US/UK)", callback_data="gen_name_female")
        )
        markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        try: bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="👤 **Fake Name Generator (US/UK)**\n━━━━━━━━━━━━━━━━━━━\n\nChoose the gender to generate a completely random English name:", parse_mode="Markdown", reply_markup=markup)
        except: pass

    elif call.data.startswith("gen_name_"):
        gender = call.data.split("_")[2]
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Generating Name...**", parse_mode="Markdown")
        try:
            resp = requests.get(f"https://randomuser.me/api/?nat=us,gb&gender={gender}", timeout=5).json()
            name_data = resp["results"][0]["name"]
            full_name = f"{name_data['first']} {name_data['last']}"
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton("📨 Create Alias with this Name", callback_data=f"use_fake_name_{full_name}"))
            markup.row(types.InlineKeyboardButton("🔄 Generate Again", callback_data=f"gen_name_{gender}"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"👤 **Generated Fake Name:**\n━━━━━━━━━━━━━━━━━━━\n\n📝 `{full_name}`\n\n*Tap the name to copy it, or click below to directly create an alias!*", parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ **Failed to generate name.** Try again.", parse_mode="Markdown", reply_markup=markup)

    elif call.data.startswith("use_fake_name_"):
        name = call.data.split("_", 3)[3]
        settings = get_user_settings(chat_id)
        if not settings["base_email"]:
            return bot.answer_callback_query(call.id, "⚠️ Set Base Email in Zoho/Yandex settings first!", show_alert=True)
        set_temp_data(chat_id, None, None, name)
        markup = types.InlineKeyboardMarkup(row_width=2).add(types.InlineKeyboardButton("✅ Yes", callback_data="confirm_alias_yes"), types.InlineKeyboardButton("❌ No", callback_data="confirm_alias_no"))
        try: bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"📌 Do you want to create an alias mail for **{name}**?", parse_mode="Markdown", reply_markup=markup)
        except: pass
    # ---------------------------

    elif call.data.startswith("refresh_2fa_"):
        secret = call.data.replace("refresh_2fa_", "")
        code = get_totp_token(secret)
        if code:
            markup = types.InlineKeyboardMarkup(row_width=1).add(types.InlineKeyboardButton("🔄 Refresh Code", callback_data=f"refresh_2fa_{secret}"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            try: bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"🔐 **Live 2FA Generator**\n━━━━━━━━━━━━━━━━━━━\n\n👇 **Tap the code below to copy:**\n\n`{code}`\n\n🔑 **Secret:** `{secret}`\n\n*(Refreshed at {datetime.now().strftime('%I:%M:%S %p')})*", parse_mode="Markdown", reply_markup=markup)
            except: pass
            bot.answer_callback_query(call.id, "✅ Code Refreshed!", show_alert=False)
        else: bot.answer_callback_query(call.id, "❌ Error generating 2FA code!", show_alert=True)
        return

    elif call.data == "action_alias_maker":
        settings = get_user_settings(chat_id)
        markup = types.InlineKeyboardMarkup(row_width=2).add(types.InlineKeyboardButton("⚙️ Set Base Email & Pass", callback_data="action_set_base_email"), types.InlineKeyboardButton("📜 Created Aliases History", callback_data="hist_alias")).row(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        try: bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"🛠️ **Zoho & Yandex Alias Generator**\n━━━━━━━━━━━━━━━━━━━\n📌 **Your Base Email:** `{settings['base_email'] or 'Not Set'}`\n\nSend any name (English ONLY), and I will automatically generate the corresponding domain alias for you!\n\n👇 **Options below:**", parse_mode="Markdown", reply_markup=markup)
        except: pass

    elif call.data == "action_set_base_email":
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        msg = bot.send_message(chat_id, "👇 **Please send your Base Email and App Password using the pipe (`|`) format:**\n(Example: `example@zohomail.com|AppPassword` or `example@yandex.com|AppPassword`)", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_base_email_step, msg.message_id)

    elif call.data.startswith("chk_alias_"):
        r_id = call.data.split("_")[2]
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT email, password, provider FROM alias_history WHERE id=%s AND owner_id=%s", (r_id, chat_id))
                    row = cursor.fetchone()
            if row:
                eml, pwd, prov = row
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET email=EXCLUDED.email, password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=NULL, client_id=NULL", (chat_id, eml, pwd, prov))
                    conn.commit()
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"⏳ **Working...**\nChecking Facebook OTP for `{eml}`", parse_mode="Markdown")
                fetch_and_send_emails(chat_id, edit_message_id=message_id)
            else: bot.answer_callback_query(call.id, "⚠️ Alias record not found!", show_alert=True)
        except Exception as e: bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)

    elif call.data == "confirm_alias_yes":
        settings = get_user_settings(chat_id)
        if not settings["base_email"] or not settings["temp_name"]: return bot.answer_callback_query(call.id, "⚠️ Session expired!", show_alert=True)
        clean_name = re.sub(r'\s+', '', settings["temp_name"]).lower()
        user_part, domain_part = settings["base_email"].split('@') if '@' in settings["base_email"] else ("example", "zohomail.com")
        provider = "yandex" if "yandex" in domain_part.lower() else "zoho"
        target_alias = f"{user_part}+{clean_name}@yandex.com" if provider == "yandex" else f"{user_part}+{clean_name}@zohomail.com"

        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor: cursor.execute("INSERT INTO alias_history (owner_id, email, password, provider) VALUES (%s, %s, %s, %s)", (chat_id, target_alias, settings["base_password"], provider))
                conn.commit()
        except: pass
        set_temp_data(chat_id, target_alias, provider, settings["temp_name"])
        markup = types.InlineKeyboardMarkup(row_width=1).add(types.InlineKeyboardButton("📥 Check Inbox (Facebook OTP)", callback_data="action_check_latest_alias"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        try: bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"✨ **Alias Mail Generated Successfully!**\n━━━━━━━━━━━━━━━━━━━\n\n🏢 **Your {provider.upper()} Alias:**\n`{target_alias}`\n\n👇 *Click the button below to check inbox for Facebook OTP instantly!*", parse_mode="Markdown", reply_markup=markup)
        except: pass

    elif call.data == "action_check_latest_alias":
        settings = get_user_settings(chat_id)
        if not settings["temp_alias"]: return bot.answer_callback_query(call.id, "⚠️ No active alias found!", show_alert=True)
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor: cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET email=EXCLUDED.email, password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=NULL, client_id=NULL", (chat_id, settings["temp_alias"], settings["base_password"], settings["temp_provider"] or "zoho"))
                conn.commit()
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"⏳ **Working...**\nChecking Facebook OTP for `{settings['temp_alias']}`", parse_mode="Markdown")
            fetch_and_send_emails(chat_id, edit_message_id=message_id)
        except Exception as e: bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)

    elif call.data == "confirm_alias_no":
        bot.answer_callback_query(call.id, "Cancelled.")
        show_main_instruction(chat_id, message_id=message_id)

    # 🟢 ADMIN PANEL & NEW FEATURES
    elif call.data == "action_admin_panel":
        if chat_id != ADMIN_ID: return
        try:
            with get_db_connection() as conn:
                with conn.cursor() as c:
                    c.execute("SELECT COUNT(DISTINCT user_id) FROM user_settings")
                    total_users = c.fetchone()[0]
                    c.execute("SELECT COUNT(*) FROM banned_users")
                    banned_count = c.fetchone()[0]
            
            global_del = "🟢 ON" if get_bot_setting('global_auto_delete') == '1' else "🔴 OFF"
            m_mode = "🟢 ON" if get_bot_setting('maintenance_mode') == '1' else "🔴 OFF"
            a_mode = "🟢 ON" if get_bot_setting('gmail_stock_alert') == '1' else "🔴 OFF"
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_broadcast"))
            markup.add(types.InlineKeyboardButton("👥 View All Users", callback_data="admin_view_users"))
            markup.add(types.InlineKeyboardButton("🚫 Ban", callback_data="admin_ban_user"), types.InlineKeyboardButton("✅ Unban", callback_data="admin_unban_user"))
            markup.add(types.InlineKeyboardButton(f"Global Auto-Del: {global_del}", callback_data="admin_toggle_autodel"))
            markup.add(types.InlineKeyboardButton(f"🛠️ Maint. Mode: {m_mode}", callback_data="admin_toggle_maint"))
            markup.add(types.InlineKeyboardButton(f"🔔 Gmail Alert: {a_mode}", callback_data="admin_toggle_alert"))
            markup.add(types.InlineKeyboardButton("🏠 Back to Main Menu", callback_data="action_menu"))
            
            stats = f"👨‍💻 **Secret Boss Dashboard (Cloud)**\n━━━━━━━━━━━━━━━━━━━\n👥 **Registered Users:** `{total_users}`\n🚫 **Banned Users:** `{banned_count}`\n━━━━━━━━━━━━━━━━━━━\n🛡️ What would you like to do?"
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=stats, parse_mode="Markdown", reply_markup=markup)
        except Exception as e: bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ Admin Error: {e}")

    elif call.data == "admin_toggle_autodel":
        if chat_id != ADMIN_ID: return
        toggle_global_auto_delete()
        call.data = "action_admin_panel"
        handle_query(call)
        
    elif call.data == "admin_toggle_maint":
        if chat_id != ADMIN_ID: return
        current = get_bot_setting('maintenance_mode')
        set_bot_setting('maintenance_mode', '0' if current == '1' else '1')
        call.data = "action_admin_panel"
        handle_query(call)

    elif call.data == "admin_toggle_alert":
        if chat_id != ADMIN_ID: return
        current = get_bot_setting('gmail_stock_alert')
        set_bot_setting('gmail_stock_alert', '0' if current == '1' else '1')
        call.data = "action_admin_panel"
        handle_query(call)
        
    elif call.data == "admin_broadcast":
        if chat_id != ADMIN_ID: return
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Cancel & Main Menu", callback_data="action_menu"))
        msg = bot.send_message(chat_id, "📢 **Enter the message you want to broadcast to ALL USERS:**", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_broadcast_step)

    elif call.data == "admin_view_users":
        if chat_id != ADMIN_ID: return
        bot.answer_callback_query(call.id, "Generating User List...")
        try:
            with get_db_connection() as conn:
                with conn.cursor() as c: c.execute("SELECT user_id, username FROM user_settings")
                users = c.fetchall()
            filename = f"Bot_Users_List.txt"
            with open(filename, "w") as f:
                f.write("--- 👥 Bot Registered Users ---\n\n")
                for i, u in enumerate(users, 1): f.write(f"{i}. ID: {u[0]} | Username: {f'@{u[1]}' if u[1] else 'No Username'}\n")
            with open(filename, "rb") as f: track_message(chat_id, bot.send_document(chat_id, f, caption=f"📊 **Total Users:** {len(users)}", parse_mode="Markdown").message_id)
            os.remove(filename)
        except Exception as e: bot.send_message(chat_id, f"❌ Error: {e}")

    elif call.data == "admin_ban_user":
        if chat_id != ADMIN_ID: return
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Cancel & Main Menu", callback_data="action_menu"))
        msg = bot.send_message(chat_id, "👇 **Send the User ID or @username you want to BAN:**", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_ban_step, msg.message_id)

    elif call.data == "admin_unban_user":
        if chat_id != ADMIN_ID: return
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Cancel & Main Menu", callback_data="action_menu"))
        msg = bot.send_message(chat_id, "👇 **Send the User ID or @username you want to UNBAN:**", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_unban_step, msg.message_id)

    elif call.data == "action_settings":
        api_status = "✅ Set & Validated" if get_user_settings(chat_id)["api_key"] else "❌ Not Set"
        markup = types.InlineKeyboardMarkup(row_width=1).add(types.InlineKeyboardButton("🔑 Update yshshopmails API Key", callback_data="action_set_api")).add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        try: bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"⚙️ **Bot Preferences & Settings**\n━━━━━━━━━━━━━━━━━━━\n\n🔑 **yshshopmails API Key:** {api_status}\n\n*(Note: Auto-Delete and features are managed globally by the Admin.)*", parse_mode="Markdown", reply_markup=markup)
        except: pass

    elif call.data == "action_set_api":
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Cancel & Main Menu", callback_data="action_menu"))
        msg = bot.send_message(chat_id, "👇 **Please send your valid 'yshshopmails' API Key now:**", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_api_key_step, msg.message_id)

    # 📜 PRO HISTORY SUB-MENU SYSTEM
    elif call.data == "action_buy_history":
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🔴 Gmail Buy History", callback_data="hist_gmail"))
        markup.add(types.InlineKeyboardButton("👤 Single Trust Mail History", callback_data="hist_trust_single"))
        markup.add(types.InlineKeyboardButton("📦 Bulk Trust Mail History", callback_data="hist_trust_bulk"))
        markup.add(types.InlineKeyboardButton("🛠️ Zoho / Yandex Alias History", callback_data="hist_alias"))
        markup.row(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        try: bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="📜 **Account & Purchase History Center**\n━━━━━━━━━━━━━━━━━━━\n\nPlease select which history record you want to view:", parse_mode="Markdown", reply_markup=markup)
        except: pass

    elif call.data == "hist_gmail":
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT email, order_id, purchased_at FROM purchase_history WHERE owner_id=%s AND (provider='gmail' OR email LIKE '%%@gmail.com') ORDER BY purchased_at DESC LIMIT 20", (chat_id,))
                    rows = cursor.fetchall()
            if not rows: return bot.answer_callback_query(call.id, "⚠️ Your Gmail purchase history is empty.", show_alert=True)
            
            history_text = "🔴 **Your Purchased Gmail History (Last 20)**\n━━━━━━━━━━━━━━━━━━━\n\n"
            for idx, (eml, ord_id, date_str) in enumerate(rows, 1):
                dt = date_str.strftime('%d-%b %I:%M %p') if isinstance(date_str, datetime) else str(date_str)[:16]
                history_text += f"**{idx}.** `{eml}`\n  🆔 Order ID: `{ord_id}` | 🕒 {dt}\n\n"
            
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Back to History Menu", callback_data="action_buy_history"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=history_text, parse_mode="Markdown", reply_markup=markup)
        except Exception as e: bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)

    # 🆕 নতুন সিঙ্গেল ট্রাস্ট হিস্টরি
    elif call.data == "hist_trust_single":
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT email, purchased_at FROM purchase_history WHERE owner_id=%s AND provider IN ('hotmail', 'outlook', 'hotmailtrust', 'outlooktrust') ORDER BY purchased_at DESC LIMIT 10", (chat_id,))
                    rows = cursor.fetchall()
            if not rows: return bot.answer_callback_query(call.id, "⚠️ Your Single Trust Mail history is empty.", show_alert=True)
            
            history_text = "👤 **Your Single Trust History (Last 10)**\n━━━━━━━━━━━━━━━━━━━\n👇 **Tap an email below to view & copy the full details:**\n"
            markup = types.InlineKeyboardMarkup(row_width=1)
            for eml, date_str in rows:
                markup.add(types.InlineKeyboardButton(f"📧 {eml}", callback_data=f"vts_{eml}"))
            
            markup.add(types.InlineKeyboardButton("📥 Download All Single History (.txt)", callback_data="dl_hist_trust_single"))
            markup.row(types.InlineKeyboardButton("⬅️ Back", callback_data="action_buy_history"), types.InlineKeyboardButton("🏠 Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=history_text, parse_mode="Markdown", reply_markup=markup)
        except Exception as e: bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)

    # 🆕 নতুন বাল্ক ট্রাস্ট হিস্টরি
    elif call.data == "hist_trust_bulk":
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT email, purchased_at FROM purchase_history WHERE owner_id=%s AND provider IN ('hotmail_bulk', 'outlook_bulk') ORDER BY purchased_at DESC LIMIT 10", (chat_id,))
                    rows = cursor.fetchall()
            if not rows: return bot.answer_callback_query(call.id, "⚠️ Your Bulk Trust Mail history is empty.", show_alert=True)
            
            history_text = "📦 **Your Bulk Trust History (Last 10)**\n━━━━━━━━━━━━━━━━━━━\n👇 **Tap an email below to view & copy the full details:**\n"
            markup = types.InlineKeyboardMarkup(row_width=1)
            for eml, date_str in rows:
                markup.add(types.InlineKeyboardButton(f"📧 {eml}", callback_data=f"vtb_{eml}"))
            
            markup.add(types.InlineKeyboardButton("📥 Download All Bulk History (.txt)", callback_data="dl_hist_trust_bulk"))
            markup.row(types.InlineKeyboardButton("⬅️ Back", callback_data="action_buy_history"), types.InlineKeyboardButton("🏠 Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=history_text, parse_mode="Markdown", reply_markup=markup)
        except Exception as e: bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)

    # 🆕 ওয়ান ক্লিক ফুল কপি ভিউয়ার
    elif call.data.startswith("vts_") or call.data.startswith("vtb_"):
        is_bulk = call.data.startswith("vtb_")
        target_email = call.data[4:]
        back_cb = "hist_trust_bulk" if is_bulk else "hist_trust_single"
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT email, password, token, client_id FROM purchase_history WHERE owner_id=%s AND email=%s LIMIT 1", (chat_id, target_email))
                    row = cursor.fetchone()
            if not row: return bot.answer_callback_query(call.id, "⚠️ Data not found!", show_alert=True)
            
            eml, pwd, tok, cli = row
            full_acc = eml
            if pwd: full_acc += f"|{pwd}"
            if tok: full_acc += f"|{tok}"
            if cli: full_acc += f"|{cli}"
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.row(types.InlineKeyboardButton("⬅️ Back to List", callback_data=back_cb), types.InlineKeyboardButton("🏠 Menu", callback_data="action_menu"))
            
            msg_text = f"📋 **Full Account Details**\n━━━━━━━━━━━━━━━━━━━\n👇 Tap the box below to copy:\n\n`{full_acc}`"
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=msg_text, parse_mode="Markdown", reply_markup=markup)
        except Exception as e: bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)

    # 🆕 সিঙ্গেল হিস্টরি ডাউনলোড
    elif call.data == "dl_hist_trust_single":
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT email, password, token, client_id FROM purchase_history WHERE owner_id=%s AND provider IN ('hotmail', 'outlook', 'hotmailtrust', 'outlooktrust')", (chat_id,))
                    rows = cursor.fetchall()
            if not rows: return bot.answer_callback_query(call.id, "⚠️ Your history is empty.", show_alert=True)
            
            filename = f"Single_Trust_Mails_{chat_id}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                for eml, pwd, tok, cli in rows:
                    full_acc = eml
                    if pwd: full_acc += f"|{pwd}"
                    if tok: full_acc += f"|{tok}"
                    if cli: full_acc += f"|{cli}"
                    f.write(f"{full_acc}\n")
                    
            with open(filename, "rb") as f:
                doc_msg = bot.send_document(chat_id, f, caption=f"📥 **Single History Export Successful!**\nTotal Accounts: {len(rows)}", parse_mode="Markdown")
                track_message(chat_id, doc_msg.message_id)
            os.remove(filename) 
            bot.answer_callback_query(call.id, "Download Complete!")
        except Exception as e: bot.send_message(chat_id, f"❌ Export Error: {e}")

    # 🆕 বাল্ক হিস্টরি ডাউনলোড
    elif call.data == "dl_hist_trust_bulk":
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT email, password, token, client_id FROM purchase_history WHERE owner_id=%s AND provider IN ('hotmail_bulk', 'outlook_bulk')", (chat_id,))
                    rows = cursor.fetchall()
            if not rows: return bot.answer_callback_query(call.id, "⚠️ Your bulk history is empty.", show_alert=True)
            
            filename = f"Bulk_Trust_Mails_{chat_id}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                for eml, pwd, tok, cli in rows:
                    full_acc = eml
                    if pwd: full_acc += f"|{pwd}"
                    if tok: full_acc += f"|{tok}"
                    if cli: full_acc += f"|{cli}"
                    f.write(f"{full_acc}\n")
                    
            with open(filename, "rb") as f:
                doc_msg = bot.send_document(chat_id, f, caption=f"📦 **Bulk History Export Successful!**\nTotal Accounts: {len(rows)}", parse_mode="Markdown")
                track_message(chat_id, doc_msg.message_id)
            os.remove(filename) 
            bot.answer_callback_query(call.id, "Download Complete!")
        except Exception as e: bot.send_message(chat_id, f"❌ Export Error: {e}")

    elif call.data == "hist_alias":
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT id, email, password, provider, created_at FROM alias_history WHERE owner_id=%s ORDER BY created_at DESC LIMIT 20", (chat_id,))
                    rows = cursor.fetchall()
            if not rows: return bot.answer_callback_query(call.id, "⚠️ Your alias history is empty.", show_alert=True)
            
            history_text = "🛠️ **Your Created Alias History (Last 20)**\n━━━━━━━━━━━━━━━━━━━\nTap any alias below to fetch Facebook OTP instantly:\n\n"
            markup = types.InlineKeyboardMarkup(row_width=1)
            for r_id, eml, pwd, prov, date_str in rows:
                markup.add(types.InlineKeyboardButton(f"📥 {eml} ({prov.upper()})", callback_data=f"chk_alias_{r_id}"))
            markup.row(types.InlineKeyboardButton("⬅️ Back to History Menu", callback_data="action_buy_history"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=history_text, parse_mode="Markdown", reply_markup=markup)
        except Exception as e: bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)

    elif call.data == "action_bulk_list":
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT id, email FROM bulk_accounts WHERE owner_id=%s AND (is_used=FALSE OR is_used IS NULL) LIMIT 10", (chat_id,))
                    rows = cursor.fetchall()
                    cursor.execute("SELECT COUNT(*) FROM bulk_accounts WHERE owner_id=%s AND (is_used=FALSE OR is_used IS NULL)", (chat_id,))
                    total = cursor.fetchone()[0]
            if not rows: return bot.answer_callback_query(call.id, "⚠️ Your Cloud Bulk List is empty! Upload a .txt file first.", show_alert=True)
            
            list_text = f"📁 **Your Fresh Bulk Accounts ({total} remaining)**\n\n👇 Click an email below to fetch Facebook OTP:"
            markup = types.InlineKeyboardMarkup(row_width=1)
            for r_id, eml in rows: markup.add(types.InlineKeyboardButton(eml, callback_data=f"bf_{r_id}"))
            markup.row(types.InlineKeyboardButton("📤 Export Fresh", callback_data="action_export_bulk"), types.InlineKeyboardButton("🗑️ Export Used", callback_data="action_export_used"))
            markup.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_bulk_list"), types.InlineKeyboardButton("🏠 Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=list_text, parse_mode="Markdown", reply_markup=markup)
        except Exception as e: bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)

    elif call.data == "action_export_bulk":
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Generating your Fresh File from Cloud...**", parse_mode="Markdown")
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM bulk_accounts WHERE owner_id=%s AND (is_used=FALSE OR is_used IS NULL)", (chat_id,))
                    rows = cursor.fetchall()
            if not rows: return bot.answer_callback_query(call.id, "⚠️ Your list is empty.", show_alert=True)
            filename = f"exported_fresh_accounts_{chat_id}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                for row in rows:
                    full = row[0]
                    if row[1]: full += f"|{row[1]}"
                    if row[3]: full += f"|{row[3]}"
                    if row[4]: full += f"|{row[4]}"
                    f.write(f"{full}\n")
            with open(filename, "rb") as f:
                doc_msg = bot.send_document(chat_id, f, caption=f"📤 **Fresh Export Successful!**\nTotal Accounts: {len(rows)}", parse_mode="Markdown")
                track_message(chat_id, doc_msg.message_id)
            os.remove(filename) 
            show_main_instruction(chat_id, message_id=message_id)
        except Exception as e: bot.send_message(chat_id, f"❌ Export Error: {e}")

    elif call.data == "action_export_used":
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Generating your Used File from Cloud...**", parse_mode="Markdown")
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM bulk_accounts WHERE owner_id=%s AND is_used=TRUE", (chat_id,))
                    rows = cursor.fetchall()
            if not rows: return bot.answer_callback_query(call.id, "⚠️ You have no used accounts saved.", show_alert=True)
            filename = f"exported_used_accounts_{chat_id}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"--- Used Bulk Accounts (Auto-deletes after 7 days) ---\n\n")
                for row in rows:
                    full = row[0]
                    if row[1]: full += f"|{row[1]}"
                    if row[3]: full += f"|{row[3]}"
                    if row[4]: full += f"|{row[4]}"
                    f.write(f"{full}\n")
            with open(filename, "rb") as f:
                doc_msg = bot.send_document(chat_id, f, caption=f"🗑️ **Used Accounts Export Successful!**\nTotal Accounts: {len(rows)}", parse_mode="Markdown")
                track_message(chat_id, doc_msg.message_id)
            os.remove(filename) 
            show_main_instruction(chat_id, message_id=message_id)
        except Exception as e: bot.send_message(chat_id, f"❌ Export Error: {e}")

    elif call.data.startswith("bf_"):
        row_id = call.data.split("_")[1]
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM bulk_accounts WHERE id=%s AND owner_id=%s", (row_id, chat_id))
                    row = cursor.fetchone()
            if row:
                eml, pwd, prov, ref, cli = row
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET email=EXCLUDED.email, password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, eml, pwd, prov, ref, cli))
                    conn.commit()
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"⏳ **Working...**\nChecking Facebook OTP for `{eml}`", parse_mode="Markdown")
                fetch_and_send_emails(chat_id, edit_message_id=message_id, bulk_email_to_delete=eml)
            else: bot.answer_callback_query(call.id, "⚠️ Account not found!", show_alert=True)
        except Exception as e: bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)

    elif call.data == "action_check_stock":
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Fetching Live Data**", parse_mode="Markdown")
            
            api_key = get_user_settings(chat_id)["api_key"]
            gmail_stock = get_service_stock(api_key, "facebook")
            hotmail_stock = get_service_stock(api_key, "hotmailtrust")
            outlook_stock = get_service_stock(api_key, "outlooktrust")

            balance = "⚠️ API Key not set"
            if api_key:
                try:
                    bal_resp = requests.get("https://yshshopmails.com/v1/api/user", headers={"api_key": api_key}, timeout=5).json()
                    if "balance" in bal_resp: balance = f"${bal_resp['balance']}"
                    else: balance = "❌ Invalid API Key"
                except: balance = "❌ Balance Error"

            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM bulk_accounts WHERE owner_id=%s AND (is_used=FALSE OR is_used IS NULL)", (chat_id,))
                    local_stock = cursor.fetchone()[0]

            dashboard_text = (
                "📊 **Server Stock Dashboard**\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"📦 **Gmail Stock:** `{gmail_stock}` pcs\n"
                f"🔥 **Hotmail Trust Stock:** `{hotmail_stock}` pcs\n"
                f"🌐 **Outlook Trust Stock:** `{outlook_stock}` pcs\n"
                f"💳 **Your Balance:** `{balance}`\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"📁 **Your Fresh Cloud Stock:** `{local_stock}` accounts."
            )
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_check_stock"), types.InlineKeyboardButton("🛒 Buy Gmail", callback_data="action_buy_gmail"))
            markup.add(types.InlineKeyboardButton("🔥 Buy Trust Mail", callback_data="action_buy_hotmail_menu"))
            markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=dashboard_text, parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **API Error:** {e}", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "action_buy_gmail":
        if not get_user_settings(chat_id)["api_key"]:
            return bot.answer_callback_query(call.id, "⚠️ Set your yshshopmails API Key in Settings first!", show_alert=True)
        markup = types.InlineKeyboardMarkup(row_width=2).add(types.InlineKeyboardButton("✅ Confirm", callback_data="confirm_buy_gmail"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="🛒 **Checkout Confirmation (Gmail)**\n\nAre you sure you want to deduct balance and buy 1 Facebook Gmail?", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "action_buy_hotmail_menu":
        if not get_user_settings(chat_id)["api_key"]:
            return bot.answer_callback_query(call.id, "⚠️ Set your yshshopmails API Key in Settings first!", show_alert=True)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🔥 Buy Hotmail Trust (Single/Bulk)", callback_data="buy_hm_trust_menu"))
        markup.add(types.InlineKeyboardButton("🌐 Buy Outlook Trust (Single/Bulk)", callback_data="buy_out_trust_menu"))
        markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="🔥 **Trust Mail Purchase Menu**\n\nSelect which trust mail you want to buy:", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "buy_hm_trust_menu":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("👤 Single Buy", callback_data="buy_hm_single"), types.InlineKeyboardButton("📦 Bulk Buy", callback_data="buy_hm_bulk"))
        markup.row(types.InlineKeyboardButton("⬅️ Back", callback_data="action_buy_hotmail_menu"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="🔥 **Hotmail Trust Purchase Mode**\n\nChoose how you want to buy:", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "buy_out_trust_menu":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("👤 Single Buy", callback_data="buy_out_single"), types.InlineKeyboardButton("📦 Bulk Buy", callback_data="buy_out_bulk"))
        markup.row(types.InlineKeyboardButton("⬅️ Back", callback_data="action_buy_hotmail_menu"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="🌐 **Outlook Trust Purchase Mode**\n\nChoose how you want to buy:", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "buy_hm_single":
        if not get_user_settings(chat_id)["api_key"]:
            return bot.answer_callback_query(call.id, "⚠️ Set your yshshopmails API Key in Settings first!", show_alert=True)
        markup = types.InlineKeyboardMarkup(row_width=2).add(types.InlineKeyboardButton("✅ Confirm", callback_data="confirm_buy_hotmail"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="🔥 **Single Checkout (Hotmail Trust)**\n\nAre you sure you want to buy 1 Hotmail Trust account?", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "buy_hm_bulk":
        if not get_user_settings(chat_id)["api_key"]:
            return bot.answer_callback_query(call.id, "⚠️ Set your yshshopmails API Key in Settings first!", show_alert=True)
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Cancel & Main Menu", callback_data="action_menu"))
        msg = bot.send_message(chat_id, "👇 **How many Hotmail Trust accounts do you want to buy?**\n(Type a number between 1 and 50, e.g., `5`):", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_hotmail_bulk_step, msg.message_id)

    elif call.data == "buy_out_single":
        if not get_user_settings(chat_id)["api_key"]:
            return bot.answer_callback_query(call.id, "⚠️ Set your yshshopmails API Key in Settings first!", show_alert=True)
        markup = types.InlineKeyboardMarkup(row_width=2).add(types.InlineKeyboardButton("✅ Confirm", callback_data="confirm_buy_outlook"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="🌐 **Single Checkout (Outlook Trust)**\n\nAre you sure you want to buy 1 Outlook Trust account?", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "buy_out_bulk":
        if not get_user_settings(chat_id)["api_key"]:
            return bot.answer_callback_query(call.id, "⚠️ Set your yshshopmails API Key in Settings first!", show_alert=True)
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Cancel & Main Menu", callback_data="action_menu"))
        msg = bot.send_message(chat_id, "👇 **How many Outlook Trust accounts do you want to buy?**\n(Type a number between 1 and 50, e.g., `5`):", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_outlook_bulk_step, msg.message_id)

    elif call.data == "confirm_buy_gmail":
        api_key = get_user_settings(chat_id)["api_key"]
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Buying Gmail**", parse_mode="Markdown")
            resp = call_buy_api(api_key, "facebook")
            
            if "error" in resp or resp.get("status") in ["error", "fail", False]:
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
                err_msg = resp.get("msg") or resp.get("message") or resp.get("error") or str(resp)
                return bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **API Error:** `{err_msg}`", parse_mode="Markdown", reply_markup=markup)

            eml, pwd, token, client_id, ord_id = extract_account_details(resp, "GMAIL_ORDER")
            
            if eml and "@" in str(eml):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET email=EXCLUDED.email, password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=NULL, client_id=NULL", (chat_id, eml, ord_id, 'gmail'))
                        cursor.execute("INSERT INTO purchase_history (owner_id, email, order_id, provider) VALUES (%s, %s, %s, 'gmail')", (chat_id, eml, str(ord_id)))
                    conn.commit()
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"🎉 **Success!**\n📧 `{eml}`\n⏳ *Fetching initial Facebook OTP...*", parse_mode="Markdown")
                time.sleep(1.5)
                fetch_and_send_emails(chat_id, edit_message_id=message_id)
            else:
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Gmail Buy Failed:** `{resp}`", parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Error:** {e}", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "confirm_buy_hotmail":
        api_key = get_user_settings(chat_id)["api_key"]
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Buying Hotmail Trust**", parse_mode="Markdown")
            resp = call_buy_api(api_key, "hotmailtrust")
            
            if "error" in resp or resp.get("status") in ["error", "fail", False]:
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
                err_msg = resp.get("msg") or resp.get("message") or resp.get("error") or str(resp)
                return bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **API Error:** `{err_msg}`", parse_mode="Markdown", reply_markup=markup)

            eml, pwd, token, client_id, ord_id = extract_account_details(resp, "HOTMAIL_TRUST_ORDER")
            
            if eml and "@" in str(eml):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, 'hotmail', %s, %s) ON CONFLICT (user_id) DO UPDATE SET email=EXCLUDED.email, password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, eml, pwd, token, client_id))
                        cursor.execute("INSERT INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id, is_used) VALUES (%s, %s, %s, 'hotmail', %s, %s, FALSE) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, eml, pwd, token, client_id))
                        cursor.execute("INSERT INTO purchase_history (owner_id, email, password, token, client_id, order_id, provider) VALUES (%s, %s, %s, %s, %s, %s, 'hotmail')", (chat_id, eml, pwd, token, client_id, str(ord_id)))
                    conn.commit()
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"🎉 **Hotmail Trust Buy Success!**\n📧 `{eml}`\n⏳ *Checking Outlook Inbox for Facebook OTP...*", parse_mode="Markdown")
                time.sleep(1.5)
                fetch_and_send_emails(chat_id, edit_message_id=message_id)
            else:
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Hotmail Trust Buy Failed:** `{resp}`", parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Error:** {e}", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "confirm_buy_outlook":
        api_key = get_user_settings(chat_id)["api_key"]
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Buying Outlook Trust**", parse_mode="Markdown")
            resp = call_buy_api(api_key, "outlooktrust")
            
            if "error" in resp or resp.get("status") in ["error", "fail", False]:
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
                err_msg = resp.get("msg") or resp.get("message") or resp.get("error") or str(resp)
                return bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **API Error:** `{err_msg}`", parse_mode="Markdown", reply_markup=markup)

            eml, pwd, token, client_id, ord_id = extract_account_details(resp, "OUTLOOK_TRUST_ORDER")
            
            if eml and "@" in str(eml):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, 'hotmail', %s, %s) ON CONFLICT (user_id) DO UPDATE SET email=EXCLUDED.email, password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, eml, pwd, token, client_id))
                        cursor.execute("INSERT INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id, is_used) VALUES (%s, %s, %s, 'hotmail', %s, %s, FALSE) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, eml, pwd, token, client_id))
                        cursor.execute("INSERT INTO purchase_history (owner_id, email, password, token, client_id, order_id, provider) VALUES (%s, %s, %s, %s, %s, %s, 'outlook')", (chat_id, eml, pwd, token, client_id, str(ord_id)))
                    conn.commit()
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"🎉 **Outlook Trust Buy Success!**\n📧 `{eml}`\n⏳ *Checking Outlook Inbox for Facebook OTP...*", parse_mode="Markdown")
                time.sleep(1.5)
                fetch_and_send_emails(chat_id, edit_message_id=message_id)
            else:
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Outlook Trust Buy Failed:** `{resp}`", parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Error:** {e}", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "action_refresh" or call.data == "action_refresh_direct":
        bot.answer_callback_query(call.id, "Refreshing Secure Inbox...")
        try: bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Syncing with Mail Server**", parse_mode="Markdown")
        except: pass
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT email FROM users WHERE user_id=%s", (chat_id,))
                user_eml = cursor.fetchone()
                cursor.execute("SELECT email FROM bulk_accounts WHERE email=%s AND owner_id=%s", (user_eml[0] if user_eml else "", chat_id))
                bulk_eml = cursor.fetchone()
        fetch_and_send_emails(chat_id, edit_message_id=message_id, bulk_email_to_delete=bulk_eml[0] if bulk_eml else None)
        
    elif call.data.startswith("view_mail_"):
        idx = int(call.data.split("_")[2])
        send_full_mail_to_chat(chat_id, idx)
        bot.answer_callback_query(call.id)

# ==========================================
# 👑 ADMIN BROADCAST & BULK SUB-HANDLERS
# ==========================================
def process_broadcast_step(message):
    chat_id = message.chat.id
    if chat_id != ADMIN_ID: return
    text = message.text
    clear_chat_history(chat_id)
    msg = bot.send_message(chat_id, "⏳ **Broadcasting message... Please wait.**", parse_mode="Markdown")
    users = get_all_user_ids()
    sent, failed = 0, 0
    for uid in users:
        try:
            bot.send_message(uid, f"📢 **Global Notice**\n━━━━━━━━━━━━━━━━━━━\n\n{text}", parse_mode="Markdown")
            sent += 1
        except: failed += 1
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
    bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"✅ **Broadcast Complete!**\n\n🟢 Sent to: `{sent}` users\n🔴 Failed: `{failed}` users", parse_mode="Markdown", reply_markup=markup)

def process_base_email_step(message, edit_msg_id):
    chat_id = message.chat.id
    text = message.text.strip()
    track_message(chat_id, message.message_id)
    clear_chat_history(chat_id)
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
    if "|" not in text or "@" not in text:
        msg = bot.send_message(chat_id, "❌ **Invalid Format!** Please send using `email|AppPassword` format.", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        return
    parts = [p.strip() for p in text.split('|')]
    set_user_base_credentials(chat_id, parts[0], parts[1])
    msg = bot.send_message(chat_id, f"✅ **Success! Base Email & App Password saved.**\n\nBase Email: `{parts[0]}`\n\nNow simply send any English name in chat to generate your domain alias automatically!", parse_mode="Markdown", reply_markup=markup)
    track_message(chat_id, msg.message_id)

def process_hotmail_bulk_step(message, edit_msg_id):
    chat_id = message.chat.id
    text = message.text.strip()
    track_message(chat_id, message.message_id)
    clear_chat_history(chat_id)
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
    if not text.isdigit():
        msg = bot.send_message(chat_id, "❌ **Invalid Number!**", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        return
    qty = int(text)
    if qty < 1 or qty > 50:
        msg = bot.send_message(chat_id, "❌ **Limit Exceeded!** You can buy between 1 and 50 accounts at once.", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        return
    api_key = get_user_settings(chat_id)["api_key"]
    if not api_key:
        msg = bot.send_message(chat_id, "❌ **API Key Missing!** Set it in Settings first.", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        return
    status_msg = bot.send_message(chat_id, f"⏳ **Working... Purchasing {qty} Hotmail Trust accounts. Please wait...**", parse_mode="Markdown")
    track_message(chat_id, status_msg.message_id)

    success_accounts = []
    for _ in range(qty):
        resp = call_buy_api(api_key, "hotmailtrust")
        eml, pwd, token, client_id, ord_id = extract_account_details(resp, "HOTMAIL_TRUST_BULK")
        if eml and "@" in str(eml): success_accounts.append((eml, pwd, token, client_id, ord_id))

    if not success_accounts:
        try: bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="❌ **Bulk Buy Failed!** Could not fetch accounts from server.", parse_mode="Markdown", reply_markup=markup)
        except: pass
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                for eml, pwd, token, client_id, ord_id in success_accounts:
                    cursor.execute("INSERT INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id, is_used) VALUES (%s, %s, %s, 'hotmail', %s, %s, FALSE) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, eml, pwd, token, client_id))
                    # 🆕 এখানে provider 'hotmail_bulk' দেওয়া হয়েছে
                    cursor.execute("INSERT INTO purchase_history (owner_id, email, password, token, client_id, order_id, provider) VALUES (%s, %s, %s, %s, %s, %s, 'hotmail_bulk')", (chat_id, eml, pwd, token, client_id, str(ord_id)))
            conn.commit()
    except Exception as e:
        bot.send_message(chat_id, f"❌ DB Error: {e}", reply_markup=markup)
        return

    filename = f"Hotmail_Trust_Bulk_{chat_id}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        for eml, pwd, token, client_id, _ in success_accounts:
            full = eml
            if pwd: full += f"|{pwd}"
            if token: full += f"|{token}"
            if client_id: full += f"|{client_id}"
            f.write(f"{full}\n")

    try: bot.delete_message(chat_id, status_msg.message_id)
    except: pass
    with open(filename, "rb") as f:
        doc_msg = bot.send_document(chat_id, f, caption=f"🎉 **Hotmail Trust Bulk Purchase Successful!**\n📦 Total Bought: `{len(success_accounts)}` Accounts\n\n*(Also added to your Cloud Bulk List & Purchase History)*", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, doc_msg.message_id)
    os.remove(filename)

def process_outlook_bulk_step(message, edit_msg_id):
    chat_id = message.chat.id
    text = message.text.strip()
    track_message(chat_id, message.message_id)
    clear_chat_history(chat_id)
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))

    if not text.isdigit():
        msg = bot.send_message(chat_id, "❌ **Invalid Number!**", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        return
    qty = int(text)
    if qty < 1 or qty > 50:
        msg = bot.send_message(chat_id, "❌ **Limit Exceeded!**", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        return
    api_key = get_user_settings(chat_id)["api_key"]
    if not api_key:
        msg = bot.send_message(chat_id, "❌ **API Key Missing!** Set it in Settings first.", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        return
    status_msg = bot.send_message(chat_id, f"⏳ **Working... Purchasing {qty} Outlook Trust accounts. Please wait...**", parse_mode="Markdown")
    track_message(chat_id, status_msg.message_id)

    success_accounts = []
    for _ in range(qty):
        resp = call_buy_api(api_key, "outlooktrust")
        eml, pwd, token, client_id, ord_id = extract_account_details(resp, "OUTLOOK_TRUST_BULK")
        if eml and "@" in str(eml): success_accounts.append((eml, pwd, token, client_id, ord_id))

    if not success_accounts:
        try: bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="❌ **Bulk Buy Failed!** Could not fetch accounts from server.", parse_mode="Markdown", reply_markup=markup)
        except: pass
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                for eml, pwd, token, client_id, ord_id in success_accounts:
                    cursor.execute("INSERT INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id, is_used) VALUES (%s, %s, %s, 'hotmail', %s, %s, FALSE) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, eml, pwd, token, client_id))
                    # 🆕 এখানে provider 'outlook_bulk' দেওয়া হয়েছে
                    cursor.execute("INSERT INTO purchase_history (owner_id, email, password, token, client_id, order_id, provider) VALUES (%s, %s, %s, %s, %s, %s, 'outlook_bulk')", (chat_id, eml, pwd, token, client_id, str(ord_id)))
            conn.commit()
    except Exception as e:
        bot.send_message(chat_id, f"❌ DB Error: {e}", reply_markup=markup)
        return

    filename = f"Outlook_Trust_Bulk_{chat_id}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        for eml, pwd, token, client_id, _ in success_accounts:
            full = eml
            if pwd: full += f"|{pwd}"
            if token: full += f"|{token}"
            if client_id: full += f"|{client_id}"
            f.write(f"{full}\n")

    try: bot.delete_message(chat_id, status_msg.message_id)
    except: pass
    with open(filename, "rb") as f:
        doc_msg = bot.send_document(chat_id, f, caption=f"🎉 **Outlook Trust Bulk Purchase Successful!**\n📦 Total Bought: `{len(success_accounts)}` Accounts\n\n*(Also added to your Cloud Bulk List & Purchase History)*", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, doc_msg.message_id)
    os.remove(filename)

def process_ban_step(message, edit_msg_id):
    chat_id = message.chat.id
    if chat_id != ADMIN_ID: return
    target_input = message.text.strip()
    track_message(chat_id, message.message_id)
    clear_chat_history(chat_id)
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                user_to_ban = int(target_input) if target_input.isdigit() else None
                if not user_to_ban:
                    cursor.execute("SELECT user_id FROM user_settings WHERE username=%s", (target_input.replace('@', '').lower(),))
                    row = cursor.fetchone()
                    if row: user_to_ban = row[0]
                if user_to_ban == ADMIN_ID: return bot.send_message(chat_id, "❌ **Boss, Admin ke ban kora jabe na!**", parse_mode="Markdown", reply_markup=markup)
                if user_to_ban:
                    cursor.execute("INSERT INTO banned_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_to_ban,))
                    conn.commit()
                    bot.send_message(chat_id, f"✅ **Success!** User `{target_input}` has been **BANNED**.", parse_mode="Markdown", reply_markup=markup)
                else: bot.send_message(chat_id, f"❌ **User Not Found!**", parse_mode="Markdown", reply_markup=markup)
    except Exception as e: bot.send_message(chat_id, f"❌ Error: {e}", reply_markup=markup)

def process_unban_step(message, edit_msg_id):
    chat_id = message.chat.id
    if chat_id != ADMIN_ID: return
    target_input = message.text.strip()
    track_message(chat_id, message.message_id)
    clear_chat_history(chat_id)
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                user_to_unban = int(target_input) if target_input.isdigit() else None
                if not user_to_unban:
                    cursor.execute("SELECT user_id FROM user_settings WHERE username=%s", (target_input.replace('@', '').lower(),))
                    row = cursor.fetchone()
                    if row: user_to_unban = row[0]
                if user_to_unban:
                    cursor.execute("DELETE FROM banned_users WHERE user_id=%s", (user_to_unban,))
                    conn.commit()
                    bot.send_message(chat_id, f"✅ **Success!** User `{target_input}` has been **UNBANNED**.", parse_mode="Markdown", reply_markup=markup)
                else: bot.send_message(chat_id, f"❌ **User Not Found!**", parse_mode="Markdown", reply_markup=markup)
    except Exception as e: bot.send_message(chat_id, f"❌ Error: {e}", reply_markup=markup)

def process_api_key_step(message, edit_msg_id):
    chat_id, api_key = message.chat.id, message.text.strip()
    track_message(chat_id, message.message_id)
    clear_chat_history(chat_id)
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
    if verify_yshshop_api(api_key):
        set_user_api_key(chat_id, api_key)
        bot.send_message(chat_id, "✅ **Success! API Key Validated & Saved.**", parse_mode="Markdown", reply_markup=markup)
    else: bot.send_message(chat_id, "❌ **Invalid API Key!**", parse_mode="Markdown", reply_markup=markup)

# ==========================================
# 📄 BULK UPLOAD HANDLER (.TXT)
# ==========================================
@bot.message_handler(content_types=['document'])
def handle_document(message):
    chat_id = message.chat.id
    save_user_info(chat_id, message.from_user.username)
    track_message(chat_id, message.message_id)
    clear_chat_history(chat_id)
    
    if is_user_banned(chat_id): return bot.send_message(chat_id, BANNED_MSG, parse_mode="Markdown")
    if is_maintenance(chat_id): return bot.send_message(chat_id, MAINTENANCE_MSG, parse_mode="Markdown")
        
    if not message.document.file_name.endswith('.txt'): 
        return bot.send_message(chat_id, "⚠️ Please upload a valid `.txt` file.")
    try:
        status_msg = bot.send_message(chat_id, "⏳ **Working... Syncing to Cloud Database (Checking Duplicates)**", parse_mode="Markdown")
        track_message(chat_id, status_msg.message_id)
        file_info = bot.get_file(message.document.file_id)
        lines = bot.download_file(file_info.file_path).decode('utf-8').strip().split('\n')
        
        unique_lines = {}
        total_valid = 0
        for line in lines:
            line = line.strip()
            if not line or '|' not in line: continue
            total_valid += 1
            parts = [p.strip() for p in line.split('|')]
            eml = parts[0].lower()
            if eml not in unique_lines: unique_lines[eml] = parts
                
        duplicates_removed = total_valid - len(unique_lines)
        success_count = 0
        
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                for eml_lower, parts in unique_lines.items():
                    eml = parts[0]
                    prov = 'gmail' if 'gmail' in eml.lower() else 'zoho' if 'zoho' in eml.lower() else 'yandex' if 'yandex' in eml.lower() else 'hotmail' if len(parts) >= 4 else 'zoho'
                    
                    if len(parts) == 2:
                        cursor.execute("INSERT INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id, is_used) VALUES (%s, %s, %s, %s, NULL, NULL, FALSE) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, provider=EXCLUDED.provider", (chat_id, eml, parts[1], prov))
                        success_count += 1
                    elif len(parts) >= 4:
                        cursor.execute("INSERT INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id, is_used) VALUES (%s, %s, %s, %s, %s, %s, FALSE) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, eml, parts[1], prov, parts[2], parts[3]))
                        success_count += 1
            conn.commit()
            
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=f"✅ **Cloud Sync Complete!**\n\n🔒 Successfully Added: `{success_count}` unique accounts.\n🗑️ Duplicates Ignored: `{duplicates_removed}`", parse_mode="Markdown", reply_markup=markup)
    except Exception as e: bot.send_message(chat_id, f"❌ File Processing Error: {e}")

# ==========================================
# 💬 GLOBAL TEXT LISTENER
# ==========================================
@bot.message_handler(func=lambda message: message.text and not message.text.startswith('/'))
def process_text_messages(message):
    chat_id, text = message.chat.id, message.text.strip()
    save_user_info(chat_id, message.from_user.username)
    track_message(chat_id, message.message_id)
    clear_chat_history(chat_id)
    
    if is_user_banned(chat_id): return bot.send_message(chat_id, BANNED_MSG, parse_mode="Markdown")
    if is_maintenance(chat_id): return bot.send_message(chat_id, MAINTENANCE_MSG, parse_mode="Markdown")

    text_lower = text.lower()
    
    # 🟢 Hi / Hello / Menu Router
    if text_lower in ['hi', 'hello', 'hey', 'start', 'menu', 'help', 'bot']:
        bot.clear_step_handler_by_chat_id(chat_id)
        show_main_instruction(chat_id)
        return

    settings = get_user_settings(chat_id)
    
    # 🟢 Strict English Name Checker for Alias Mail
    if settings["base_email"] and re.match(r'^[a-zA-Z\s]+$', text) and len(text) < 40 and not re.match(r'^[A-Z2-7]{16,100}$', text.replace(" ", "").upper()):
        set_temp_data(chat_id, None, None, text)
        markup = types.InlineKeyboardMarkup(row_width=2).add(types.InlineKeyboardButton("✅ Yes", callback_data="confirm_alias_yes"), types.InlineKeyboardButton("❌ No", callback_data="confirm_alias_no"))
        msg = bot.send_message(chat_id, f"📌 Do you want to create an alias mail for **{text}**?", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        return

    if '|' in text:
        try:
            parts = [p.strip() for p in text.split('|')]
            eml, pwd = parts[0], parts[1]
            prov = 'gmail' if 'gmail' in eml.lower() else 'zoho' if 'zoho' in eml.lower() else 'yandex' if 'yandex' in eml.lower() else 'hotmail' if len(parts)>=4 else 'zoho'
            
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    if len(parts) == 2:
                        cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET email=EXCLUDED.email, password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=NULL, client_id=NULL", (chat_id, eml, pwd, prov))
                        cursor.execute("INSERT INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id, is_used) VALUES (%s, %s, %s, %s, NULL, NULL, FALSE) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, provider=EXCLUDED.provider", (chat_id, eml, pwd, prov))
                        cursor.execute("INSERT INTO alias_history (owner_id, email, password, provider) VALUES (%s, %s, %s, %s)", (chat_id, eml, pwd, prov))
                    elif len(parts) >= 4:
                        cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET email=EXCLUDED.email, password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, eml, pwd, prov, parts[2], parts[3]))
                conn.commit()

            msg = bot.send_message(chat_id, f"⏳ **Working...**\nChecking Facebook OTP for `{eml}`", parse_mode="Markdown")
            track_message(chat_id, msg.message_id)
            fetch_and_send_emails(chat_id, edit_message_id=msg.message_id)
        except Exception as e:
            err = bot.send_message(chat_id, f"❌ **Format Error!** {e}", parse_mode="Markdown")
            track_message(chat_id, err.message_id)

    elif '@' in text and '.' in text:
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT password, provider, refresh_token, client_id FROM bulk_accounts WHERE email=%s AND owner_id=%s", (text, chat_id))
                    row = cursor.fetchone()
                    if row:
                        pwd, prov, ref, cli = row
                        cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET email=EXCLUDED.email, password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, text, pwd, prov, ref, cli))
                        conn.commit()
                        msg = bot.send_message(chat_id, f"⏳ **Working...**\nConnecting to `{text}`", parse_mode="Markdown")
                        track_message(chat_id, msg.message_id)
                        fetch_and_send_emails(chat_id, edit_message_id=msg.message_id, bulk_email_to_delete=text)
                    else:
                        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
                        err = bot.send_message(chat_id, f"❌ **Error:** `{text}` not found in your Cloud DB!", parse_mode="Markdown", reply_markup=markup)
                        track_message(chat_id, err.message_id)
        except Exception as e: pass
                
    elif re.match(r'^[A-Z2-7]{16,100}$', text.replace(" ", "").upper()):
        code = get_totp_token(text)
        if code:
            markup = types.InlineKeyboardMarkup(row_width=1).add(types.InlineKeyboardButton("🔄 Refresh Code", callback_data=f"refresh_2fa_{text}"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            msg = bot.send_message(chat_id, f"🔐 **Live 2FA Generator**\n━━━━━━━━━━━━━━━━━━━\n\n👇 **Tap the code below to copy:**\n\n`{code}`\n\n🔑 **Secret:** `{text}`\n", parse_mode="Markdown", reply_markup=markup)
            track_message(chat_id, msg.message_id)
        else:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            err = bot.send_message(chat_id, "❌ **Invalid 2FA secret key!** Please check your key and try again.", parse_mode="Markdown", reply_markup=markup)
            track_message(chat_id, err.message_id)
    else:
        clear_chat_history(chat_id)
        show_main_instruction(chat_id)

# ==========================================
# 📧 CORE EMAIL ENGINE
# ==========================================
def send_full_mail_to_chat(chat_id, idx):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT subject, sender, full_content FROM email_cache WHERE user_id=%s AND idx=%s", (chat_id, idx))
                row = cursor.fetchone()
                cursor.execute("SELECT provider FROM users WHERE user_id=%s", (chat_id,))
                provider = cursor.fetchone()[0] if cursor.rowcount else 'unknown'
        
        if not row: return
        subject, sender, full_content = row
        
        safe_body_lines = clean_html_tags(full_content).replace('*', '').replace('_', '').replace('`', '').replace('[', '').replace(']', '').split('\n')
        formatted_body = "\n".join([f"    {line.strip()}" for line in safe_body_lines if line.strip()]) 
        
        logo_url = "https://cdn-icons-png.flaticon.com/512/732/732200.png"
        if provider == 'gmail': logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7e/Gmail_icon_%282020%29.svg/512px-Gmail_icon_%282020%29.svg.png"
        elif provider == 'hotmail': logo_url = "https://i.ibb.co.com/x8LVnqMr/image-removebg-preview.png"
        elif provider == 'zoho': logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Zoho_Corporation_logo.svg/512px-Zoho_Corporation_logo.svg.png"
        elif provider == 'yandex': logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/8/80/Yandex_Mail_icon.svg/512px-Yandex_Mail_icon.svg.png"
        
        message_text = f"📬 **Secure Encrypted Mail Viewer**\n\n👤 **From:** `{sender}`\n📌 **Subject:** `{subject}`\n━━━━━━━━━━━━━━━━━━━\n\n```text\n{formatted_body[:3000]}\n```\n━━━━━━━━━━━━━━━━━━━\n⚠️ *Data Auto-Destructs in 10 mins.*"
        
        try:
            sent_msg = bot.send_photo(chat_id, logo_url, caption=message_text, parse_mode="Markdown")
            if sent_msg: track_message(chat_id, sent_msg.message_id)
        except:
            sent_msg = bot.send_message(chat_id, message_text, parse_mode="Markdown", disable_web_page_preview=True)
            if sent_msg: track_message(chat_id, sent_msg.message_id)
    except: pass

def fetch_and_send_emails(chat_id, edit_message_id=None, bulk_email_to_delete=None):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM users WHERE user_id=%s", (chat_id,))
                result = cursor.fetchone()

        def _create_markup(emails_cached, is_bulk):
            m = types.InlineKeyboardMarkup()
            if emails_cached: m.row(types.InlineKeyboardButton("📖 View Full Email", callback_data="view_mail_0"))
            if is_bulk: m.row(types.InlineKeyboardButton("🔄 Re-Sync Inbox", callback_data="action_refresh"), types.InlineKeyboardButton("➡️ Next Account", callback_data="action_bulk_list"))
            else: m.row(types.InlineKeyboardButton("🔄 Re-Sync Inbox", callback_data="action_refresh"))
            m.row(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            return m

        if not result: return show_main_instruction(chat_id, message_id=edit_message_id)

        email_address, password, provider, refresh_token, client_id = result
        target_eml_lower = email_address.lower().strip()
        response_text, cached_emails, otp_found = "", [], False
        
        if provider == 'gmail':
            api_key = get_user_settings(chat_id)["api_key"]
            if not api_key: response_text = "❌ **API Key Missing!** Set it in Settings."
            else:
                try:
                    data = requests.get(f"https://yshshopmails.com/v1/api/check-otp.php?key={api_key}&id={password}", timeout=10).json()
                    if "otp" in data and data["otp"]:
                        otp_found, otp_code = True, data["otp"]
                        subject = f"Facebook OTP: {otp_code}"
                        cached_emails.append((subject, "API@yshshopmails", f"Facebook OTP Code: {otp_code} (Verified API)"))
                        response_text = f"📨 **Live Inbox ({email_address}) [yshshopmails API]:**\n\n🔹 **[📘 FACEBOOK OTP]** Code: `{otp_code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                    elif "error" in data: response_text = f"❌ **API Sync Error:** {data['error']}"
                    else: response_text = f"📭 **Live Inbox ({email_address})**\nNo Facebook OTP found."
                except: response_text = "❌ **API Connection Timeout.** Try again."

        elif provider in ['zoho', 'yandex']:
            login_email = email_address
            if '+' in login_email and '@' in login_email: login_email = f"{login_email.split('+')[0]}@{login_email.split('@')[1]}"
            imap_server = 'imap.zoho.com' if provider == 'zoho' else 'imap.yandex.com'
            try:
                mail = imaplib.IMAP4_SSL(imap_server)
                mail.login(login_email, password)
                mail.select("inbox")
                status, messages = mail.search(None, "ALL")
                email_ids = messages[0].split()

                if not email_ids: response_text = f"📭 **Live Inbox ({email_address})** is empty."
                else:
                    response_text = f"📨 **Live Inbox ({email_address}):**\n\n"
                    fb_found = False
                    for e_id in reversed(email_ids[-15:]):
                        status, msg_data = mail.fetch(e_id, "(RFC822)")
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                to_header = msg.get("To", "")
                                if to_header:
                                    to_hdr_decoded = decode_header(to_header)[0]
                                    to_str = to_hdr_decoded[0]
                                    if isinstance(to_str, bytes): to_str = to_str.decode(to_hdr_decoded[1] if to_hdr_decoded[1] else 'utf-8', errors='ignore')
                                    if target_eml_lower not in to_str.lower(): continue 

                                raw_html, subject, encoding = get_html_body(msg), decode_header(msg["Subject"])[0], None
                                if isinstance(subject, bytes): subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")
                                from_ = msg.get("From", "Unknown")
                                
                                fb_code = detect_facebook_otp(subject, clean_html_tags(raw_html))
                                if fb_code: 
                                    cached_emails.append((subject, from_, raw_html))
                                    response_text += f"🔹 **[📘 FACEBOOK OTP]** Code: `{fb_code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                                    fb_found, otp_found = True, True
                                    break
                        if fb_found: break
                    if not fb_found: response_text = f"📭 **Live Inbox ({email_address})**\nNo Facebook OTP found for this specific alias."
                mail.logout()
            except imaplib.IMAP4.error: response_text = "❌ **IMAP Authentication Failed!** Check App Password / Provider."

        elif provider == 'hotmail':
            url = "https://api-tools.yshshopmails.shop/api/v1/public/outlook/read_inbox"
            try:
                response = requests.post(url, json={"data": f"{email_address}|{password}|{refresh_token}|{client_id}"}, headers={'Content-Type': 'application/json'}, timeout=15)
                if response.status_code == 200 and response.json().get("success"):
                    emails = response.json().get("data", [])
                    if not emails: response_text = f"📭 **Live Inbox ({email_address})** is empty."
                    else:
                        response_text = f"📨 **Live Inbox ({email_address}):**\n\n"
                        fb_found = False
                        for msg in emails[:10]:
                            msg_to = str(msg.get("to", "")).lower()
                            if msg_to and target_eml_lower not in msg_to: continue
                            raw_body, subject, from_sender = msg.get("message", "No Content"), msg.get("subject", "No Subject"), msg.get("from", "Outlook System")
                            
                            fb_code = detect_facebook_otp(subject, clean_html_tags(raw_body))
                            if fb_code:
                                cached_emails.append((subject, from_sender, raw_body))
                                response_text += f"🔹 **[📘 FACEBOOK OTP]** Code: `{fb_code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                                fb_found, otp_found = True, True
                                break
                        if not fb_found: response_text = f"📭 **Live Inbox ({email_address})**\nNo Facebook OTP matched."
                else: response_text = "❌ **Outlook Server Error:** Gateway unavailable."
            except: response_text = "❌ **Outlook API Timeout:** Server took too long to respond."

        if otp_found:
            with get_db_connection() as conn:
                with conn.cursor() as cursor: cursor.execute("UPDATE bulk_accounts SET is_used=TRUE, used_at=CURRENT_TIMESTAMP WHERE email=%s AND owner_id=%s", (email_address, chat_id))
                conn.commit()
            
            if get_bot_setting('global_auto_delete') == '1':
                with get_db_connection() as conn:
                    with conn.cursor() as cursor: cursor.execute("DELETE FROM bulk_accounts WHERE email=%s AND owner_id=%s", (email_address, chat_id))
                    conn.commit()
                response_text += f"\n✅ *Account removed from list (Global Auto-Delete ON)*"
            else: response_text += f"\n✅ *Account Marked as USED (Moved to Used DB)*"
        else: response_text += f"\nℹ️ *Account kept in Fresh queue (No OTP found yet).* "

        if cached_emails:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM email_cache WHERE user_id=%s", (chat_id,))
                    for idx, (sub, snd, html_content) in enumerate(cached_emails): 
                        cursor.execute("INSERT INTO email_cache (user_id, idx, subject, sender, full_content) VALUES (%s, %s, %s, %s, %s)", (chat_id, idx, sub, snd, html_content))
                conn.commit()

        response_text += f"\n🕒 *Server Sync Time:* {datetime.now().strftime('%I:%M:%S %p')}"
        markup = _create_markup(bool(cached_emails), bool(bulk_email_to_delete))

        if edit_message_id:
            try: bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=response_text, parse_mode="Markdown", reply_markup=markup)
            except: 
                sent_msg = bot.send_message(chat_id, response_text, parse_mode="Markdown", reply_markup=markup)
                track_message(chat_id, sent_msg.message_id)
        else:
            sent_msg = bot.send_message(chat_id, response_text, parse_mode="Markdown", reply_markup=markup)
            track_message(chat_id, sent_msg.message_id)
    except Exception as e:
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        try:
            if edit_message_id: bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=f"⚠️ Critical Error: {e}", parse_mode="Markdown", reply_markup=markup)
            else: 
                err = bot.send_message(chat_id, f"⚠️ Critical Error: {e}", reply_markup=markup)
                track_message(chat_id, err.message_id)
        except: pass

# ==========================================
# 🚀 MAIN EXECUTION
# ==========================================
def start_bot():
    while type(True) is bool:
        try:
            bot.remove_webhook()
            bot.infinity_polling(skip_pending=True, interval=1, timeout=20)
        except Exception as e:
            logging.error(f"Polling crashed: {e}. Restarting in 5 seconds...")
            time.sleep(5)

if __name__ == "__main__":
    init_db()
    threading.Thread(target=auto_cleaner, daemon=True).start()
    threading.Thread(target=gmail_stock_tracker, daemon=True).start()
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))
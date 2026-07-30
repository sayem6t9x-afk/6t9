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

# 🐘 SUPABASE POSTGRESQL DATABASE URL (Render Ready)
DATABASE_URL = "postgresql://postgres.cvqaqgqzlgbrlntvvlfn:WQsa9069%23%2A6T9@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# ==========================================
# 🧹 STRICT UI TRACKER
# ==========================================
chat_history = {}
active_mail_messages = {}

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

def safe_delete(chat_id, message_id):
    try: bot.delete_message(chat_id, message_id)
    except: pass

# ==========================================
# 🌐 FLASK SERVER (Render Dummy Web Service)
# ==========================================
app = Flask(__name__)

@app.route("/")
def home():
    return "Render Web Service is Live and Telegram Bot is Running Perfectly!", 200

# ==========================================
# 💾 DATABASE MANAGEMENT (POSTGRESQL)
# ==========================================
def init_db():
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cursor = conn.cursor()
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, email TEXT, password TEXT, provider TEXT, refresh_token TEXT, client_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS email_cache (user_id BIGINT, idx INTEGER, subject TEXT, sender TEXT, full_content TEXT, PRIMARY KEY (user_id, idx))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS user_settings (user_id BIGINT PRIMARY KEY, api_key TEXT, base_email TEXT, temp_alias TEXT, temp_provider TEXT, username TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS bulk_accounts (id SERIAL, owner_id BIGINT, email TEXT PRIMARY KEY, password TEXT, provider TEXT, refresh_token TEXT, client_id TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS purchase_history (owner_id BIGINT, email TEXT, order_id TEXT, purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS banned_users (user_id BIGINT PRIMARY KEY)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT)''')
        
        cursor.execute("INSERT INTO bot_settings (key, value) VALUES ('global_auto_delete', '1') ON CONFLICT (key) DO NOTHING")
        
        try: cursor.execute("ALTER TABLE user_settings ADD COLUMN username TEXT")
        except psycopg2.Error: pass
        try: cursor.execute("ALTER TABLE user_settings ADD COLUMN base_email TEXT")
        except psycopg2.Error: pass
        try: cursor.execute("ALTER TABLE user_settings ADD COLUMN temp_alias TEXT")
        except psycopg2.Error: pass
        try: cursor.execute("ALTER TABLE user_settings ADD COLUMN temp_provider TEXT")
        except psycopg2.Error: pass
        
        try: cursor.execute("DELETE FROM banned_users WHERE user_id=%s", (ADMIN_ID,))
        except psycopg2.Error: pass
        
        cursor.close()
        conn.close()
        logging.info("Database initialized successfully.")
    except Exception as e:
        logging.error(f"Database init error: {e}")

def get_global_auto_delete():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT value FROM bot_settings WHERE key='global_auto_delete'")
                row = cursor.fetchone()
                return row[0] == '1' if row else True
    except:
        return True

def toggle_global_auto_delete():
    current = get_global_auto_delete()
    new_val = '0' if current else '1'
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO bot_settings (key, value) VALUES ('global_auto_delete', %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (new_val,))
            conn.commit()
    except: pass
    return new_val == '1'

def save_user_info(user_id, username):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id FROM user_settings WHERE user_id=%s", (user_id,))
                if not cursor.fetchone():
                    cursor.execute("INSERT INTO user_settings (user_id) VALUES (%s)", (user_id,))
                if username:
                    cursor.execute("UPDATE user_settings SET username=%s WHERE user_id=%s", (username.lower(), user_id))
            conn.commit()
    except: pass

def is_user_banned(user_id):
    if user_id == ADMIN_ID: return False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM banned_users WHERE user_id=%s", (user_id,))
                return cursor.fetchone() is not None
    except:
        return False

def get_user_settings(user_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT api_key, base_email, temp_alias, temp_provider FROM user_settings WHERE user_id=%s", (user_id,))
                row = cursor.fetchone()
                if row: return {"api_key": row[0], "base_email": row[1], "temp_alias": row[2], "temp_provider": row[3]}
    except: pass
    return {"api_key": None, "base_email": None, "temp_alias": None, "temp_provider": None}

def set_user_api_key(user_id, api_key):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id FROM user_settings WHERE user_id=%s", (user_id,))
                if cursor.fetchone(): cursor.execute("UPDATE user_settings SET api_key=%s WHERE user_id=%s", (api_key, user_id))
                else: cursor.execute("INSERT INTO user_settings (user_id, api_key) VALUES (%s, %s)", (user_id, api_key))
            conn.commit()
    except: pass

def set_user_base_email(user_id, base_email):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id FROM user_settings WHERE user_id=%s", (user_id,))
                if cursor.fetchone(): cursor.execute("UPDATE user_settings SET base_email=%s WHERE user_id=%s", (base_email, user_id))
                else: cursor.execute("INSERT INTO user_settings (user_id, base_email) VALUES (%s, %s)", (user_id, base_email))
            conn.commit()
    except: pass

def set_temp_alias(user_id, alias, provider):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id FROM user_settings WHERE user_id=%s", (user_id,))
                if cursor.fetchone(): cursor.execute("UPDATE user_settings SET temp_alias=%s, temp_provider=%s WHERE user_id=%s", (alias, provider, user_id))
                else: cursor.execute("INSERT INTO user_settings (user_id, temp_alias, temp_provider) VALUES (%s, %s, %s)", (user_id, alias, provider))
            conn.commit()
    except: pass

def verify_yshshop_api(api_key):
    if len(api_key) < 20 or " " in api_key: return False
    try:
        bal_resp = requests.get("https://yshshopmails.com/v1/api/user", headers={"api_key": api_key}, timeout=5).json()
        if "balance" in bal_resp: return True
    except: pass
    return False

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

def detect_otp_type(subject, content):
    combined_text = (subject + " " + content).lower()
    if "facebook" in combined_text or "fb" in combined_text:
        code_match = re.search(r'\b\d{6,8}\b', combined_text)
        return "📘 FACEBOOK OTP", (code_match.group(0) if code_match else "Not Found")
    return None, None

import hmac
import base64
import struct
import hashlib

def get_totp_token(secret):
    try:
        secret = secret.replace(' ', '').upper()
        missing_padding = len(secret) % 8
        if missing_padding != 0: secret += '=' * (8 - missing_padding)
        key = base64.b32decode(secret, casefold=True)
        msg = struct.pack(">Q", int(time.time() // 30))
        mac = hmac.new(key, msg, hashlib.sha1).digest()
        offset = mac[-1] & 0x0f
        binary = struct.unpack('>L', mac[offset:offset+4])[0] & 0x7fffffff
        return str(binary % 1000000).zfill(6)
    except Exception: return None

# ==========================================
# 📱 MAIN MENU INTERFACE
# ==========================================
@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    chat_id = message.chat.id
    save_user_info(chat_id, message.from_user.username)
    track_message(chat_id, message.message_id)
    clear_chat_history(chat_id)
    
    if is_user_banned(chat_id):
        msg = bot.send_message(chat_id, BANNED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        track_message(chat_id, msg.message_id)
        return
    show_main_instruction(chat_id)

def show_main_instruction(chat_id, message_id=None):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("🛒 Buy Gmail", callback_data="action_buy_gmail"), types.InlineKeyboardButton("🔥 Buy Hotmail", callback_data="action_buy_hotmail_menu"))
    markup.add(types.InlineKeyboardButton("🛠️ Zoho/Yandex Alias", callback_data="action_alias_maker"), types.InlineKeyboardButton("📊 Check Stock", callback_data="action_check_stock"))
    markup.add(types.InlineKeyboardButton("📁 My Bulk Accounts", callback_data="action_bulk_list"), types.InlineKeyboardButton("📜 Buy History", callback_data="action_buy_history"))
    markup.add(types.InlineKeyboardButton("🔄 Refresh Inbox", callback_data="action_refresh_direct"), types.InlineKeyboardButton("⚙️ Settings", callback_data="action_settings"))
    if chat_id == ADMIN_ID: markup.add(types.InlineKeyboardButton("👨‍💻 Admin Panel (Boss Only)", callback_data="action_admin_panel"))
    
    instruction_text = (
        "🤖 **Auto Secure Mail & OTP Reader Bot**\n\n"
        "**🔥 CLOUD SECURE BULK MODE ACTIVE!**\n"
        "1. Send a `.txt` file (It stays Private to you).\n"
        "2. Click **📁 My Bulk Accounts** to pick an email.\n\n"
        "**Manual Input Format:**\n"
        "🏢 **Zoho/Yandex:** `email|AppPassword`\n"
        "🔴 **Gmail:** `email@gmail.com|OrderID`\n"
        "🔥 **Hotmail:** `email|password|token|client_id`\n"
        "🔐 **2FA Code:** Send `Secret Key` (e.g. JBSWY3DPEHPK3PXP)"
    )
    
    if message_id:
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=instruction_text, parse_mode="Markdown", reply_markup=markup)
            track_message(chat_id, message_id)
            return
        except Exception: pass
            
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
    
    if is_user_banned(chat_id):
        bot.answer_callback_query(call.id, "🚫 You are BANNED!", show_alert=True)
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=BANNED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        except: pass
        return

    if call.data == "action_menu":
        clear_chat_history(chat_id, keep_message_id=message_id)
        show_main_instruction(chat_id, message_id=message_id)
        return

    elif call.data == "action_alias_maker":
        settings = get_user_settings(chat_id)
        base_eml = settings["base_email"] or "Not Set"
        alias_text = (
            "🛠️ **Zoho & Yandex Alias Generator**\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"📌 **Your Base Email:** `{base_eml}`\n\n"
            "Send a name (e.g., `sayem ahamed`), and I will generate formatted alias emails with instant inbox check options!\n\n"
            "👇 **Send your name now or update your base email in Settings first!**"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("⚙️ Set Base Email", callback_data="action_set_base_email"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=alias_text, parse_mode="Markdown", reply_markup=markup)
        except: pass

    elif call.data == "action_set_base_email":
        msg = bot.send_message(chat_id, "👇 **Please send your Base Email address** (e.g., `sayem@zohomail.com`):", parse_mode="Markdown")
        track_message(chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_base_email_step, msg.message_id)

    elif call.data == "check_alias_zoho":
        settings = get_user_settings(chat_id)
        alias = settings["temp_alias"]
        if not alias or "zohomail.com" not in alias:
            bot.answer_callback_query(call.id, "⚠️ Please generate a Zoho alias first!", show_alert=True)
            return
        msg = bot.send_message(chat_id, f"👇 **Please send your Zoho App Password** for `{alias}` to check inbox and fetch code instantly:", parse_mode="Markdown")
        track_message(chat_id, msg.message_id)
        set_temp_alias(chat_id, alias, "zoho")
        bot.register_next_step_handler(msg, process_alias_password_step, msg.message_id)

    elif call.data == "check_alias_yandex":
        settings = get_user_settings(chat_id)
        alias = settings["temp_alias"]
        if not alias or "yandex.com" not in alias:
            bot.answer_callback_query(call.id, "⚠️ Please generate a Yandex alias first!", show_alert=True)
            return
        msg = bot.send_message(chat_id, f"👇 **Please send your Yandex App Password** for `{alias}` to check inbox and fetch code instantly:", parse_mode="Markdown")
        track_message(chat_id, msg.message_id)
        set_temp_alias(chat_id, alias, "yandex")
        bot.register_next_step_handler(msg, process_alias_password_step, msg.message_id)

    elif call.data == "action_admin_panel":
        if chat_id != ADMIN_ID: return
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Loading Admin Stats from Cloud...**", parse_mode="Markdown")
        except: pass
        try:
            with get_db_connection() as conn:
                with conn.cursor() as c:
                    c.execute("SELECT COUNT(DISTINCT user_id) FROM user_settings")
                    total_users = c.fetchone()[0]
                    c.execute("SELECT COUNT(*) FROM banned_users")
                    banned_count = c.fetchone()[0]
                
            global_del = "🟢 ON" if get_global_auto_delete() else "🔴 OFF"
            stats_msg = (
                "👨‍💻 **Secret Boss Dashboard (Cloud)**\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"👥 **Total Registered Users:** `{total_users}`\n"
                f"🚫 **Total Banned Users:** `{banned_count}`\n"
                f"🗑️ **Global Auto-Delete (All Users):** `{global_del}`\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "🛡️ What would you like to do?"
            )
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton("👥 View All Users", callback_data="admin_view_users"))
            markup.add(types.InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban_user"), types.InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_user"))
            markup.add(types.InlineKeyboardButton(f"Toggle Global Auto-Delete", callback_data="admin_toggle_autodel"))
            markup.add(types.InlineKeyboardButton("🏠 Back to Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=stats_msg, parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            try: bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ Admin Error: {e}")
            except: pass

    elif call.data == "admin_toggle_autodel":
        if chat_id != ADMIN_ID: return
        toggle_global_auto_delete()
        handle_query(types.CallbackQuery(call.id, call.from_user, call.data, call.chat_instance, call.message, data="action_admin_panel"))

    elif call.data == "admin_view_users":
        if chat_id != ADMIN_ID: return
        bot.answer_callback_query(call.id, "Generating User List...")
        try:
            with get_db_connection() as conn:
                with conn.cursor() as c:
                    c.execute("SELECT user_id, username FROM user_settings")
                    users = c.fetchall()
            
            if not users: 
                bot.send_message(chat_id, "⚠️ No users found in database.")
                return
                
            filename = f"Bot_Users_List.txt"
            with open(filename, "w") as f:
                f.write("--- 👥 Bot Registered Users ---\n\n")
                for i, u in enumerate(users, 1): 
                    uname = f"@{u[1]}" if u[1] else "No Username"
                    f.write(f"{i}. ID: {u[0]} | Username: {uname}\n")
                
            with open(filename, "rb") as f: 
                doc = bot.send_document(chat_id, f, caption=f"📊 **Total Users:** {len(users)}", parse_mode="Markdown")
                track_message(chat_id, doc.message_id)
            os.remove(filename)
        except Exception as e:
            bot.send_message(chat_id, f"❌ Error: {e}")

    elif call.data == "admin_ban_user":
        if chat_id != ADMIN_ID: return
        msg = bot.send_message(chat_id, "👇 **Send the User ID or @username you want to BAN:**", parse_mode="Markdown")
        track_message(chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_ban_step, msg.message_id)

    elif call.data == "admin_unban_user":
        if chat_id != ADMIN_ID: return
        msg = bot.send_message(chat_id, "👇 **Send the User ID or @username you want to UNBAN:**", parse_mode="Markdown")
        track_message(chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_unban_step, msg.message_id)

    elif call.data == "action_settings":
        settings = get_user_settings(chat_id)
        api_status = "✅ Set & Validated" if settings["api_key"] else "❌ Not Set"
        base_status = f"`{settings['base_email']}`" if settings["base_email"] else "❌ Not Set"
        
        settings_text = (
            "⚙️ **Bot Preferences & Settings**\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔑 **yshshopmails API Key:** {api_status}\n"
            f"📧 **Base Email (Alias Maker):** {base_status}\n\n"
            "*(Note: Auto-Delete feature is managed globally by the Admin for all users.)*"
        )
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🔑 Update yshshopmails API Key", callback_data="action_set_api"))
        markup.add(types.InlineKeyboardButton("📧 Update Base Email for Aliases", callback_data="action_set_base_email"))
        markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=settings_text, parse_mode="Markdown", reply_markup=markup)
        except: pass

    elif call.data == "action_set_api":
        msg = bot.send_message(chat_id, "👇 **Please send your valid 'yshshopmails' API Key now:**", parse_mode="Markdown")
        track_message(chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_api_key_step, msg.message_id)

    elif call.data == "action_buy_history":
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT email, order_id, purchased_at FROM purchase_history WHERE owner_id=%s ORDER BY purchased_at DESC LIMIT 15", (chat_id,))
                    rows = cursor.fetchall()
                
            if not rows: 
                bot.answer_callback_query(call.id, "⚠️ Your purchase history is empty.", show_alert=True)
                return
                
            history_text = "📜 **Your Last 15 Purchased Accounts**\n━━━━━━━━━━━━━━━━━━━\n\n"
            for idx, (eml, ord_id, date_str) in enumerate(rows, 1):
                history_text += f"**{idx}.** `{eml}|{ord_id}`\n"
            
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=history_text, parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)

    elif call.data == "action_bulk_list":
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT id, email FROM bulk_accounts WHERE owner_id=%s LIMIT 10", (chat_id,))
                    rows = cursor.fetchall()
                    cursor.execute("SELECT COUNT(*) FROM bulk_accounts WHERE owner_id=%s", (chat_id,))
                    total = cursor.fetchone()[0]
                
            if not rows: 
                bot.answer_callback_query(call.id, "⚠️ Your Cloud Bulk List is empty! Upload a .txt file first.", show_alert=True)
                return
                
            list_text = f"📁 **Your Private Bulk Accounts ({total} remaining)**\n\n👇 Click an email below to fetch OTP:"
            markup = types.InlineKeyboardMarkup(row_width=1)
            for r_id, eml in rows: markup.add(types.InlineKeyboardButton(eml, callback_data=f"bf_{r_id}"))
            markup.row(types.InlineKeyboardButton("📤 Export List", callback_data="action_export_bulk"))
            markup.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_bulk_list"), types.InlineKeyboardButton("🏠 Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=list_text, parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)

    elif call.data == "action_export_bulk":
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Generating your File from Cloud...**", parse_mode="Markdown")
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM bulk_accounts WHERE owner_id=%s", (chat_id,))
                    rows = cursor.fetchall()
            if not rows: 
                bot.answer_callback_query(call.id, "⚠️ Your list is empty.", show_alert=True)
                return
            
            filename = f"exported_accounts_{chat_id}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                for row in rows:
                    if row[2] == 'hotmail' and row[3] and row[4]: f.write(f"{row[0]}|{row[1]}|{row[3]}|{row[4]}\n")
                    else: f.write(f"{row[0]}|{row[1]}\n")
            with open(filename, "rb") as f:
                doc_msg = bot.send_document(chat_id, f, caption=f"📤 **Export Successful!**\nTotal Accounts: {len(rows)}", parse_mode="Markdown")
                track_message(chat_id, doc_msg.message_id)
            os.remove(filename) 
            show_main_instruction(chat_id, message_id=message_id)
        except Exception as e: 
            bot.send_message(chat_id, f"❌ Export Error: {e}")

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
                        cursor.execute("SELECT user_id FROM users WHERE user_id=%s", (chat_id,))
                        if cursor.fetchone(): cursor.execute("UPDATE users SET email=%s, password=%s, provider=%s, refresh_token=%s, client_id=%s WHERE user_id=%s", (eml, pwd, prov, ref, cli, chat_id))
                        else: cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, %s, %s, %s)", (chat_id, eml, pwd, prov, ref, cli))
                    conn.commit()
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"⏳ **Working...**\nChecking `{eml}`", parse_mode="Markdown")
                fetch_and_send_emails(chat_id, edit_message_id=message_id, bulk_email_to_delete=eml)
            else:
                bot.answer_callback_query(call.id, "⚠️ Account not found!", show_alert=True)
        except Exception as e:
            bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)

    elif call.data == "action_check_stock":
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Fetching Live Data**", parse_mode="Markdown")
            
            try:
                stock_resp = requests.get("https://facebook.yshshopmails.com/v1/api/stock", timeout=10).json()
                if isinstance(stock_resp, dict): gmail_stock = stock_resp.get("stock", stock_resp.get("count", stock_resp.get("data", "Available")))
                else: gmail_stock = str(stock_resp)
            except: gmail_stock = "Live"

            try:
                hm_stock_resp = requests.get("https://api-tools.yshshopmails.shop/api/v1/public/outlook/stock", timeout=10).json()
                if isinstance(hm_stock_resp, dict): hotmail_stock = hm_stock_resp.get("stock", hm_stock_resp.get("count", hm_stock_resp.get("data", "Available")))
                else: hotmail_stock = str(hm_stock_resp)
            except: hotmail_stock = "Live"

            balance = "⚠️ API Key not set"
            api_key = get_user_settings(chat_id)["api_key"]
            if api_key:
                try:
                    bal_resp = requests.get("https://yshshopmails.com/v1/api/user", headers={"api_key": api_key}, timeout=5).json()
                    if "balance" in bal_resp: balance = f"${bal_resp['balance']}"
                    else: balance = "❌ Invalid API Key"
                except: balance = "❌ Balance Error"

            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM bulk_accounts WHERE owner_id=%s", (chat_id,))
                    local_stock = cursor.fetchone()[0]

            dashboard_text = (
                "📊 **Server Stock Dashboard**\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"📦 **Gmail Stock:** `{gmail_stock}` pcs\n"
                f"🔥 **Hotmail/Outlook Stock:** `{hotmail_stock}` pcs\n"
                f"💳 **Your Balance:** `{balance}`\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"📁 **Your Cloud TXT Stock:** `{local_stock}` accounts."
            )
            markup = types.InlineKeyboardMarkup()
            markup.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_check_stock"), types.InlineKeyboardButton("🛒 Buy Gmail", callback_data="action_buy_gmail"))
            markup.row(types.InlineKeyboardButton("🔥 Buy Hotmail", callback_data="action_buy_hotmail_menu"))
            markup.row(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
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
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("👤 Single Buy", callback_data="buy_hm_single"), types.InlineKeyboardButton("📦 Bulk Buy", callback_data="buy_hm_bulk"))
        markup.row(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="🔥 **Hotmail / Outlook Trust Purchase Mode**\n\nChoose how you want to buy:", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "buy_hm_single":
        if not get_user_settings(chat_id)["api_key"]:
            return bot.answer_callback_query(call.id, "⚠️ Set your yshshopmails API Key in Settings first!", show_alert=True)
        markup = types.InlineKeyboardMarkup(row_width=2).add(types.InlineKeyboardButton("✅ Confirm", callback_data="confirm_buy_hotmail"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="🔥 **Single Checkout (Hotmail/Outlook)**\n\nAre you sure you want to buy 1 account?", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "buy_hm_bulk":
        if not get_user_settings(chat_id)["api_key"]:
            return bot.answer_callback_query(call.id, "⚠️ Set your yshshopmails API Key in Settings first!", show_alert=True)
        msg = bot.send_message(chat_id, "👇 **How many Hotmail accounts do you want to buy?**\n(Type a number between 1 and 50, e.g., `5`):", parse_mode="Markdown")
        track_message(chat_id, msg.message_id)
        bot.register_next_step_handler(msg, process_hotmail_bulk_step, msg.message_id)

    elif call.data == "confirm_buy_gmail":
        api_key = get_user_settings(chat_id)["api_key"]
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Buying Gmail**", parse_mode="Markdown")
            order_urls = [
                f"https://facebook.yshshopmails.com/v1/api/create-order.php?key={api_key}",
                f"https://facebook.yshshopmails.com/v1/api/order?key={api_key}"
            ]
            resp, raw_resp = None, None
            for url in order_urls:
                try:
                    raw_resp = requests.get(url, timeout=10)
                    data = raw_resp.json()
                    if isinstance(data, dict) and data.get("status") == "error": continue
                    resp = data
                    break
                except: continue
            
            if not resp and raw_resp:
                try: resp = raw_resp.json()
                except: resp = {"mail": raw_resp.text.strip(), "order_id": "API_ORDER"}
            elif not resp: resp = {"error": "Order failed"}

            eml = resp.get("mail") or resp.get("email") or resp.get("account") or resp.get("data")
            ord_id = resp.get("order_id") or resp.get("id") or "AUTO_ORDER"
            
            if eml and "@" in str(eml):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT user_id FROM users WHERE user_id=%s", (chat_id,))
                        if cursor.fetchone(): cursor.execute("UPDATE users SET email=%s, password=%s, provider=%s, refresh_token=NULL, client_id=NULL WHERE user_id=%s", (eml, ord_id, 'gmail', chat_id))
                        else: cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (%s, %s, %s, %s)", (chat_id, eml, ord_id, 'gmail'))
                        cursor.execute("INSERT INTO purchase_history (owner_id, email, order_id) VALUES (%s, %s, %s)", (chat_id, eml, str(ord_id)))
                    conn.commit()
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"🎉 **Success!**\n📧 `{eml}`\n⏳ *Fetching initial OTP...*", parse_mode="Markdown")
                time.sleep(1.5)
                fetch_and_send_emails(chat_id, edit_message_id=message_id)
            else:
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Failed:** `{resp}`", parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Error:** {e}", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "confirm_buy_hotmail":
        api_key = get_user_settings(chat_id)["api_key"]
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Buying Hotmail**", parse_mode="Markdown")
            hotmail_urls = [
                f"https://api-tools.yshshopmails.shop/api/v1/public/outlook/buy?key={api_key}",
                f"https://outlook.yshshopmails.com/v1/api/create-order.php?key={api_key}",
                f"https://yshshopmails.com/v1/api/outlook/create-order.php?key={api_key}"
            ]
            resp, raw_resp = None, None
            for url in hotmail_urls:
                try:
                    raw_resp = requests.get(url, timeout=10)
                    data = raw_resp.json()
                    if isinstance(data, dict) and data.get("status") == "error": continue
                    resp = data
                    break
                except: continue
            
            if not resp and raw_resp:
                try: resp = raw_resp.json()
                except: resp = {"mail": raw_resp.text.strip()}
            elif not resp: resp = {"error": "Hotmail order failed"}

            eml = resp.get("mail") or resp.get("email") or resp.get("account") or resp.get("data")
            pwd = resp.get("password") or resp.get("pwd") or ""
            token = resp.get("token") or resp.get("refresh_token") or ""
            client_id = resp.get("client_id") or ""
            ord_id = resp.get("order_id") or "HOTMAIL_ORDER"
            
            if eml and "@" in str(eml):
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT user_id FROM users WHERE user_id=%s", (chat_id,))
                        if cursor.fetchone(): cursor.execute("UPDATE users SET email=%s, password=%s, provider=%s, refresh_token=%s, client_id=%s WHERE user_id=%s", (eml, pwd, 'hotmail', token, client_id, chat_id))
                        else: cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, 'hotmail', %s, %s)", (chat_id, eml, pwd, token, client_id))
                        
                        cursor.execute("INSERT INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, 'hotmail', %s, %s) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, eml, pwd, token, client_id))
                        cursor.execute("INSERT INTO purchase_history (owner_id, email, order_id) VALUES (%s, %s, %s)", (chat_id, eml, str(ord_id)))
                    conn.commit()
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"🎉 **Hotmail Buy Success!**\n📧 `{eml}`\n⏳ *Checking Outlook Inbox...*", parse_mode="Markdown")
                time.sleep(1.5)
                fetch_and_send_emails(chat_id, edit_message_id=message_id)
            else:
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Hotmail Buy Failed:** `{resp}`", parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Error:** {e}", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "action_refresh" or call.data == "action_refresh_direct":
        bot.answer_callback_query(call.id, "Refreshing Secure Inbox...")
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Syncing with Mail Server**", parse_mode="Markdown")
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
# 👑 ADMIN & ALIAS / BULK SUB-HANDLERS
# ==========================================
def process_base_email_step(message, edit_msg_id):
    chat_id = message.chat.id
    base_eml = message.text.strip()
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))

    if "@" not in base_eml or "." not in base_eml:
        msg = bot.send_message(chat_id, "❌ **Invalid Email!** Please provide a valid base email address.", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        return

    set_user_base_email(chat_id, base_eml)
    msg = bot.send_message(chat_id, f"✅ **Success! Base Email saved as:** `{base_eml}`\n\nNow you can click '🛠️ Zoho/Yandex Alias' and send any name to generate aliases instantly!", parse_mode="Markdown", reply_markup=markup)
    track_message(chat_id, msg.message_id)

def process_alias_password_step(message, edit_msg_id):
    chat_id = message.chat.id
    app_password = message.text.strip()
    track_message(chat_id, message.message_id)

    settings = get_user_settings(chat_id)
    alias = settings["temp_alias"]
    provider = settings["temp_provider"]

    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))

    if not alias or not provider:
        msg = bot.send_message(chat_id, "❌ **Session Expired!** Please generate a new alias first.", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id FROM users WHERE user_id=%s", (chat_id,))
                if cursor.fetchone():
                    cursor.execute("UPDATE users SET email=%s, password=%s, provider=%s, refresh_token=NULL, client_id=NULL WHERE user_id=%s", (alias, app_password, provider, chat_id))
                else:
                    cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (%s, %s, %s, %s)", (chat_id, alias, app_password, provider))
                
                cursor.execute("INSERT INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, %s, NULL, NULL) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, provider=EXCLUDED.provider", (chat_id, alias, app_password, provider))
                cursor.execute("INSERT INTO purchase_history (owner_id, email, order_id) VALUES (%s, %s, 'ALIAS_GEN')", (chat_id, alias))
            conn.commit()
    except Exception as e:
        bot.send_message(chat_id, f"❌ DB Error: {e}", reply_markup=markup)
        return

    msg = bot.send_message(chat_id, f"⏳ **Connecting to IMAP Server for `{alias}`...**", parse_mode="Markdown")
    track_message(chat_id, msg.message_id)
    fetch_and_send_emails(chat_id, edit_message_id=msg.message_id)

def process_hotmail_bulk_step(message, edit_msg_id):
    chat_id = message.chat.id
    text = message.text.strip()
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))

    if not text.isdigit():
        msg = bot.send_message(chat_id, "❌ **Invalid Number!** Please send a valid digit (e.g., 5).", parse_mode="Markdown", reply_markup=markup)
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

    status_msg = bot.send_message(chat_id, f"⏳ **Working... Purchasing {qty} Hotmail accounts. Please wait...**", parse_mode="Markdown")
    track_message(chat_id, status_msg.message_id)

    success_accounts = []
    hotmail_urls = [
        f"https://api-tools.yshshopmails.shop/api/v1/public/outlook/buy?key={api_key}",
        f"https://outlook.yshshopmails.com/v1/api/create-order.php?key={api_key}",
        f"https://yshshopmails.com/v1/api/outlook/create-order.php?key={api_key}"
    ]

    for _ in range(qty):
        resp = None
        for url in hotmail_urls:
            try:
                raw_resp = requests.get(url, timeout=10)
                data = raw_resp.json()
                if isinstance(data, dict) and data.get("status") == "error": continue
                resp = data
                break
            except: continue

        if resp and isinstance(resp, dict):
            eml = resp.get("mail") or resp.get("email") or resp.get("account") or resp.get("data")
            pwd = resp.get("password") or resp.get("pwd") or ""
            token = resp.get("token") or resp.get("refresh_token") or ""
            client_id = resp.get("client_id") or ""
            ord_id = resp.get("order_id") or "HOTMAIL_BULK"

            if eml and "@" in str(eml):
                success_accounts.append((eml, pwd, token, client_id, ord_id))

    if not success_accounts:
        try: bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="❌ **Bulk Buy Failed!** Could not fetch accounts from server.", parse_mode="Markdown", reply_markup=markup)
        except: pass
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                for eml, pwd, token, client_id, ord_id in success_accounts:
                    cursor.execute("INSERT INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, 'hotmail', %s, %s) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, eml, pwd, token, client_id))
                    cursor.execute("INSERT INTO purchase_history (owner_id, email, order_id) VALUES (%s, %s, %s)", (chat_id, eml, str(ord_id)))
            conn.commit()
    except Exception as e:
        bot.send_message(chat_id, f"❌ DB Error: {e}", reply_markup=markup)
        return

    filename = f"Hotmail_Bulk_{chat_id}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        for eml, pwd, token, client_id, _ in success_accounts:
            f.write(f"{eml}|{pwd}|{token}|{client_id}\n")

    try: bot.delete_message(chat_id, status_msg.message_id)
    except: pass

    with open(filename, "rb") as f:
        doc_msg = bot.send_document(chat_id, f, caption=f"🎉 **Bulk Purchase Successful!**\n📦 Total Bought: `{len(success_accounts)}` Hotmail Accounts\n\n*(Also added to your Cloud Bulk List & Purchase History)*", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, doc_msg.message_id)
    os.remove(filename)

def process_ban_step(message, edit_msg_id):
    chat_id = message.chat.id
    if chat_id != ADMIN_ID: return
    target_input = message.text.strip()
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="action_admin_panel"))
    user_to_ban = None
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if target_input.isdigit(): user_to_ban = int(target_input)
                else:
                    uname = target_input.replace('@', '').lower()
                    cursor.execute("SELECT user_id FROM user_settings WHERE username=%s", (uname,))
                    row = cursor.fetchone()
                    if row: user_to_ban = row[0]
                    
                if user_to_ban == ADMIN_ID:
                    msg = bot.send_message(chat_id, "❌ **Boss, Admin ke ban kora jabe na!**", parse_mode="Markdown", reply_markup=markup)
                    track_message(chat_id, msg.message_id)
                    return

                if user_to_ban:
                    cursor.execute("INSERT INTO banned_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_to_ban,))
                    conn.commit()
                    msg = bot.send_message(chat_id, f"✅ **Success!**\nUser / ID `{target_input}` has been **BANNED**.", parse_mode="Markdown", reply_markup=markup)
                    track_message(chat_id, msg.message_id)
                else: 
                    msg = bot.send_message(chat_id, f"❌ **User Not Found!**", parse_mode="Markdown", reply_markup=markup)
                    track_message(chat_id, msg.message_id)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {e}")

def process_unban_step(message, edit_msg_id):
    chat_id = message.chat.id
    if chat_id != ADMIN_ID: return
    target_input = message.text.strip()
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="action_admin_panel"))
    user_to_unban = None
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if target_input.isdigit(): user_to_unban = int(target_input)
                else:
                    uname = target_input.replace('@', '').lower()
                    cursor.execute("SELECT user_id FROM user_settings WHERE username=%s", (uname,))
                    row = cursor.fetchone()
                    if row: user_to_unban = row[0]
                    
                if user_to_unban:
                    cursor.execute("DELETE FROM banned_users WHERE user_id=%s", (user_to_unban,))
                    conn.commit()
                    msg = bot.send_message(chat_id, f"✅ **Success!**\nUser / ID `{target_input}` has been **UNBANNED**.", parse_mode="Markdown", reply_markup=markup)
                    track_message(chat_id, msg.message_id)
                else: 
                    msg = bot.send_message(chat_id, f"❌ **User Not Found!**", parse_mode="Markdown", reply_markup=markup)
                    track_message(chat_id, msg.message_id)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {e}")

def process_api_key_step(message, edit_msg_id):
    chat_id, api_key = message.chat.id, message.text.strip()
    track_message(chat_id, message.message_id)
    
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
    
    if verify_yshshop_api(api_key):
        set_user_api_key(chat_id, api_key)
        msg = bot.send_message(chat_id, "✅ **Success! API Key Validated & Saved.**", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
    else:
        msg = bot.send_message(chat_id, "❌ **Invalid API Key!**", parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)

# ==========================================
# 📄 BULK UPLOAD HANDLER (.TXT)
# ==========================================
@bot.message_handler(content_types=['document'])
def handle_document(message):
    chat_id = message.chat.id
    save_user_info(chat_id, message.from_user.username)
    track_message(chat_id, message.message_id)
    
    if is_user_banned(chat_id):
        msg = bot.send_message(chat_id, BANNED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        track_message(chat_id, msg.message_id)
        return
        
    if not message.document.file_name.endswith('.txt'): 
        msg = bot.send_message(chat_id, "⚠️ Please upload a valid `.txt` file.")
        track_message(chat_id, msg.message_id)
        return
    try:
        status_msg = bot.send_message(chat_id, "⏳ **Working... Syncing to Cloud Database**", parse_mode="Markdown")
        track_message(chat_id, status_msg.message_id)
        file_info = bot.get_file(message.document.file_id)
        lines = bot.download_file(file_info.file_path).decode('utf-8').strip().split('\n')
        
        success_count = 0
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                for line in lines:
                    line = line.strip()
                    if not line or '|' not in line: continue
                    parts = [p.strip() for p in line.split('|')]
                    eml = parts[0]
                    prov = 'gmail' if 'gmail' in eml.lower() else 'zoho' if 'zoho' in eml.lower() else 'yandex' if 'yandex' in eml.lower() else 'hotmail' if len(parts) >= 4 else 'zoho'
                    
                    if len(parts) == 2:
                        cursor.execute("INSERT INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, %s, NULL, NULL) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, provider=EXCLUDED.provider", (chat_id, eml, parts[1], prov))
                        success_count += 1
                    elif len(parts) >= 4:
                        cursor.execute("INSERT INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, provider=EXCLUDED.provider, refresh_token=EXCLUDED.refresh_token, client_id=EXCLUDED.client_id", (chat_id, eml, parts[1], prov, parts[2], parts[3]))
                        success_count += 1
            conn.commit()
            
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=f"✅ **Cloud Sync Complete!**\n\n🔒 Added `{success_count}` accounts to your Private PostgreSQL Storage.", parse_mode="Markdown", reply_markup=markup)
    except Exception as e:
        err = bot.send_message(chat_id, f"❌ File Processing Error: {e}")
        track_message(chat_id, err.message_id)

# ==========================================
# 💬 GLOBAL TEXT LISTENER
# ==========================================
@bot.message_handler(func=lambda message: message.text and not message.text.startswith('/'))
def process_text_messages(message):
    chat_id, text = message.chat.id, message.text.strip()
    save_user_info(chat_id, message.from_user.username)
    track_message(chat_id, message.message_id)
    
    if is_user_banned(chat_id):
        clear_chat_history(chat_id)
        msg = bot.send_message(chat_id, BANNED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        track_message(chat_id, msg.message_id)
        return

    settings = get_user_settings(chat_id)
    if settings["base_email"] and (" " in text or len(text.split()) > 0) and "@" not in text and "|" not in text and len(text) < 40 and not re.match(r'^[A-Z2-7]{16,100}$', text.replace(" ", "").upper()):
        clean_name = re.sub(r'\s+', '', text).lower()
        user_part, domain_part = settings["base_email"].split('@') if '@' in settings["base_email"] else ("example", "zohomail.com")
        zoho_alias = f"{user_part}+{clean_name}@zohomail.com"
        yandex_alias = f"{user_part}+{clean_name}@yandex.com"

        set_temp_alias(chat_id, zoho_alias, "zoho")

        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("📥 Check Zoho Inbox", callback_data="check_alias_zoho"), types.InlineKeyboardButton("📥 Check Yandex Inbox", callback_data="check_alias_yandex"))
        markup.row(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))

        res_txt = (
            "✨ **Generated Aliases Successfully!**\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🏢 **Zoho Alias Format:**\n"
            f"`{zoho_alias}|AppPassword`\n\n"
            "🔴 **Yandex Alias Format:**\n"
            f"`{yandex_alias}|AppPassword`\n\n"
            "👇 **Click below to check inbox instantly:**"
        )
        msg = bot.send_message(chat_id, res_txt, parse_mode="Markdown", reply_markup=markup)
        track_message(chat_id, msg.message_id)
        return

    if '|' in text:
        try:
            parts = [p.strip() for p in text.split('|')]
            eml = parts[0]
            prov = 'gmail' if 'gmail' in eml.lower() else 'zoho' if 'zoho' in eml.lower() else 'yandex' if 'yandex' in eml.lower() else 'hotmail' if len(parts)>=4 else 'zoho'
            
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    if len(parts) == 2:
                        cursor.execute("SELECT user_id FROM users WHERE user_id=%s", (chat_id,))
                        if cursor.fetchone(): cursor.execute("UPDATE users SET email=%s, password=%s, provider=%s, refresh_token=NULL, client_id=NULL WHERE user_id=%s", (eml, parts[1], prov, chat_id))
                        else: cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (%s, %s, %s, %s)", (chat_id, eml, parts[1], prov))
                    elif len(parts) >= 4:
                        cursor.execute("SELECT user_id FROM users WHERE user_id=%s", (chat_id,))
                        if cursor.fetchone(): cursor.execute("UPDATE users SET email=%s, password=%s, provider=%s, refresh_token=%s, client_id=%s WHERE user_id=%s", (eml, parts[1], prov, parts[2], parts[3], chat_id))
                        else: cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, %s, %s, %s)", (chat_id, eml, parts[1], prov, parts[2], parts[3]))
                conn.commit()

            msg = bot.send_message(chat_id, f"⏳ **Working...**\nChecking `{eml}`", parse_mode="Markdown")
            track_message(chat_id, msg.message_id)
            fetch_and_send_emails(chat_id, edit_message_id=msg.message_id)
        except Exception as e:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            err = bot.send_message(chat_id, f"❌ **Format Error!** {e}", parse_mode="Markdown", reply_markup=markup)
            track_message(chat_id, err.message_id)

    elif '@' in text and '.' in text:
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT password, provider, refresh_token, client_id FROM bulk_accounts WHERE email=%s AND owner_id=%s", (text, chat_id))
                    row = cursor.fetchone()
                    
                    if row:
                        pwd, prov, ref, cli = row
                        cursor.execute("SELECT user_id FROM users WHERE user_id=%s", (chat_id,))
                        if cursor.fetchone(): cursor.execute("UPDATE users SET email=%s, password=%s, provider=%s, refresh_token=%s, client_id=%s WHERE user_id=%s", (text, pwd, prov, ref, cli, chat_id))
                        else: cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (%s, %s, %s, %s, %s, %s)", (chat_id, text, pwd, prov, ref, cli))
                        conn.commit()
                        
                        msg = bot.send_message(chat_id, f"⏳ **Working...**\nConnecting to `{text}`", parse_mode="Markdown")
                        track_message(chat_id, msg.message_id)
                        fetch_and_send_emails(chat_id, edit_message_id=msg.message_id, bulk_email_to_delete=text)
                    else:
                        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
                        err = bot.send_message(chat_id, f"❌ **Error:** `{text}` not found in your Cloud DB!", parse_mode="Markdown", reply_markup=markup)
                        track_message(chat_id, err.message_id)
        except Exception as e:
            pass
                
    elif re.match(r'^[A-Z2-7]{16,100}$', text.replace(" ", "").upper()):
        code = get_totp_token(text)
        if code:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            msg = bot.send_message(chat_id, f"🔐 **Live 2FA Generator**\n━━━━━━━━━━━━━━━━━━━\n\n🔹 **Code:** `{code}`\n🔑 **Secret:** `{text}`\n\n*(Updates automatically every 30s)*", parse_mode="Markdown", reply_markup=markup)
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
                user_row = cursor.fetchone()
                provider = user_row[0] if user_row else 'unknown'
        
        if not row: return
            
        subject, sender, full_content = row
        safe_body = clean_html_tags(full_content).replace('*', '').replace('_', '').replace('`', '').replace('[', '').replace(']', '')
        
        logo_url = "https://cdn-icons-png.flaticon.com/512/732/732200.png"
        if provider == 'gmail': logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7e/Gmail_icon_%282020%29.svg/512px-Gmail_icon_%282020%29.svg.png"
        elif provider == 'hotmail': logo_url = "https://i.ibb.co.com/x8LVnqMr/image-removebg-preview.png"
        elif provider == 'zoho': logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Zoho_Corporation_logo.svg/512px-Zoho_Corporation_logo.svg.png"
        elif provider == 'yandex': logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/8/80/Yandex_Mail_icon.svg/512px-Yandex_Mail_icon.svg.png"
        
        message_text = f"📬 **Secure Encrypted Mail Viewer**\n\n👤 **From:** `{sender}`\n📌 **Subject:** `{subject}`\n━━━━━━━━━━━━━━━━━━━\n\n{safe_body[:3000]}\n\n⚠️ *Data Auto-Destructs in 10 mins.*"
        
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
                    else: response_text = f"📭 **Live Inbox ({email_address})**\nScanning complete. No FB OTP found yet."
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
                                lbl, code = detect_otp_type(subject, clean_html_tags(raw_html))
                                if lbl: 
                                    cached_emails.append((subject, from_, raw_html))
                                    response_text += f"🔹 **[{lbl}]** Code: `{code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                                    fb_found, otp_found = True, True
                                    break
                        if fb_found: break
                    if not fb_found: response_text = f"📭 **Live Inbox ({email_address})**\nNo specific FB OTP found for this alias."
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
                            lbl, code = detect_otp_type(subject, clean_html_tags(raw_body))
                            if lbl:
                                cached_emails.append((subject, from_sender, raw_body))
                                response_text += f"🔹 **[{lbl}]** Code: `{code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                                fb_found, otp_found = True, True
                                break
                        if not fb_found: response_text = f"📭 **Live Inbox ({email_address})**\nNo FB OTP matched."
                else: response_text = "❌ **Outlook Server Error:** Gateway unavailable."
            except: response_text = "❌ **Outlook API Timeout:** Server took too long to respond."

        if bulk_email_to_delete:
            global_del = get_global_auto_delete()
            if otp_found and global_del:
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("DELETE FROM bulk_accounts WHERE email=%s AND owner_id=%s", (bulk_email_to_delete, chat_id))
                    conn.commit()
                response_text += f"\n✅ *Global Auto-Delete ON: Account removed from Database.*"
            elif otp_found and not global_del: response_text += f"\nℹ️ *Global Auto-Delete OFF: Account preserved in Database.*"
            elif not otp_found: response_text += f"\nℹ️ *Account kept in queue (No OTP found).* "

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
# 🚀 MAIN EXECUTION (Render Ready)
# ==========================================
def start_bot():
    while True:
        try:
            bot.remove_webhook()
            bot.infinity_polling(skip_pending=True, interval=1, timeout=20)
        except Exception as e:
            logging.error(f"Polling crashed: {e}. Restarting in 5 seconds...")
            time.sleep(5)

if __name__ == "__main__":
    init_db()  # First, initialize the database
    
    # Run the Telegram Bot in a separate background thread with auto-reconnect loop
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    # Start the Flask Web Service on the main thread (required by Render)
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))
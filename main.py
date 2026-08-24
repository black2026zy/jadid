import os
import sys
import json
import uuid
import time
import asyncio
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Request, HTTPException, Depends, Form, Cookie, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import httpx

# ── Telegram Bot Imports ──────────────────────────────────────────────────────
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ── Logging Setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Configuration & Environment Variables ──────────────────────────────────────
PORT = int(os.environ.get("PORT", 8000))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123456")
RAILWAY_PUBLIC_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

DEFAULT_PROTOCOL = "vless-ws"
DEFAULT_FINGERPRINT = "chrome"
DEFAULT_PORT = 443

LINKS_FILE = "data_links.json"
SESSIONS: Dict[str, datetime] = {}
LINKS: Dict[str, dict] = {}
LINKS_LOCK = asyncio.Lock()

app = FastAPI(title="X4G Dashboard & Telegram Bot")

# ── Helper Functions ──────────────────────────────────────────────────────────
def get_host() -> str:
    if RAILWAY_PUBLIC_DOMAIN:
        return RAILWAY_PUBLIC_DOMAIN.strip()
    return f"localhost:{PORT}"

def generate_uuid() -> str:
    return str(uuid.uuid4())

async def load_state():
    global LINKS
    if os.path.exists(LINKS_FILE):
        try:
            with open(LINKS_FILE, "r", encoding="utf-8") as f:
                LINKS = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            LINKS = {}

async def save_state():
    try:
        with open(LINKS_FILE, "w", encoding="utf-8") as f:
            json.dump(LINKS, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")

def vless_link_for_link(link_data: dict, uid: str, host: str) -> str:
    label = link_data.get("label", "VLESS")
    fp = link_data.get("fingerprint", DEFAULT_FINGERPRINT)
    port = link_data.get("port", DEFAULT_PORT)
    return f"vless://{uid}@{host}:{port}?type=ws&security=tls&fp={fp}&path=%2F#{label}"

def is_authenticated(session_id: Optional[str]) -> bool:
    if not session_id or session_id not in SESSIONS:
        return False
    if datetime.now() > SESSIONS[session_id]:
        del SESSIONS[session_id]
        return False
    return True

# ── Telegram Bot Logic ───────────────────────────────────────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = (
        f"မင်္ဂလာပါ {user.first_name}!\n\n"
        "X4G VPN Server မှ ကြိုဆိုပါတယ်။\n"
        "အောက်ပါ ခလုတ်ကို နှိပ်၍ သင့်အတွက် VLESS Config Key ထုတ်ယူနိုင်ပါတယ်။"
    )
    keyboard = [
        [InlineKeyboardButton("🔑 Get VLESS Config", callback_data="get_config")],
        [InlineKeyboardButton("ℹ️ Help / အကူအညီ", callback_data="help_info")]
    ]
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def bot_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "get_config":
        await query.edit_message_text("⏳ Config ခေတ္တ ထုတ်ယူနေပါသည်...")
        user_name = query.from_user.username or query.from_user.first_name
        
        uid = generate_uuid()
        label = f"TG-{user_name[:20]}"
        host = get_host()
        
        async with LINKS_LOCK:
            LINKS[uid] = {
                "label": label,
                "limit_bytes": 0,
                "used_bytes": 0,
                "created_at": datetime.now().isoformat(),
                "active": True,
                "expires_at": (datetime.now() + timedelta(days=30)).isoformat(),
                "note": f"Created via Telegram Bot by @{user_name}",
                "protocol": DEFAULT_PROTOCOL,
                "fingerprint": DEFAULT_FINGERPRINT,
                "port": DEFAULT_PORT,
                "ip_limit": 2,
            }
        await save_state()
        
        vless_link = vless_link_for_link(LINKS[uid], uid, host)
        sub_url = f"https://{host}/sub/{uid}"
        
        message_text = (
            "✅ **သင့် VLESS Key ရရှိပါပြီ!**\n\n"
            f"**Name:** `{label}`\n\n"
            f"**Subscription URL:**\n`{sub_url}`\n\n"
            f"**VLESS Link:**\n`{vless_link}`\n\n"
            "💡 *အထက်ပါ Link ကို Copy ယူပြီး v2rayNG / NekoBox / Sing-box / V2Box တို့တွင် Import ပြုလုပ်ပါ။*"
        )
        await query.edit_message_text(message_text, parse_mode="Markdown")

    elif query.data == "help_info":
        help_text = (
            "📖 **အသုံးပြုပုံ**\n\n"
            "1. 'Get VLESS Config' ကို နှိပ်ပါ။\n"
            "2. ထွက်လာသော `vless://` Link သို့မဟုတ် Subscription URL ကို ကူးယူပါ။\n"
            "3. VPN Application ထဲသို့ ထည့်သွင်း အသုံးပြုပါ။"
        )
        keyboard = [[InlineKeyboardButton("⬅️ နောက်သို့", callback_data="back_home")]]
        await query.edit_message_text(help_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "back_home":
        user = query.from_user
        welcome_text = f"မင်္ဂလာပါ {user.first_name}!\n\nအောက်ပါ ခလုတ်ကို နှိပ်၍ Config Key ရယူနိုင်ပါတယ်။"
        keyboard = [
            [InlineKeyboardButton("🔑 Get VLESS Config", callback_data="get_config")],
            [InlineKeyboardButton("ℹ️ Help / အကူအညီ", callback_data="help_info")]
        ]
        await query.edit_message_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def start_telegram_bot():
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("⚠️ TELEGRAM_BOT_TOKEN မရှိပါ။ Telegram Bot အလုပ်လုပ်မည်မဟုတ်ပါ။")
        return

    try:
        tg_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
        tg_app.add_handler(CommandHandler("start", start_command))
        tg_app.add_handler(CallbackQueryHandler(bot_button_handler))

        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling()
        logger.info("🤖 Telegram Bot အောင်မြင်စွာ တက်လာပါပြီ!")
    except Exception as e:
        logger.error(f"❌ Telegram Bot Error: {e}")

# ── FastAPI Startup ───────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    await load_state()
    logger.info(f"Server started on port {PORT}")
    asyncio.create_task(start_telegram_bot())

# ── Web UI Routes (Login & Dashboard) ─────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(session_id: Optional[str] = Cookie(None)):
    if not is_authenticated(session_id):
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    host = get_host()
    rows_html = ""
    for uid, link in LINKS.items():
        vless = vless_link_for_link(link, uid, host)
        sub = f"https://{host}/sub/{uid}"
        rows_html += f"""
        <tr>
            <td style="padding: 10px; border-bottom: 1px solid #333;">{link.get('label')}</td>
            <td style="padding: 10px; border-bottom: 1px solid #333;"><input type="text" value="{sub}" readonly style="width: 90%; background: #222; color: #fff; border: 1px solid #444; padding: 4px;"></td>
            <td style="padding: 10px; border-bottom: 1px solid #333;"><input type="text" value="{vless}" readonly style="width: 90%; background: #222; color: #fff; border: 1px solid #444; padding: 4px;"></td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>X4G Dashboard</title>
        <style>
            body {{ font-family: sans-serif; background: #121212; color: #e0e0e0; margin: 0; padding: 20px; }}
            .card {{ background: #1e1e1e; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
            table {{ width: 100%; border-collapse: collapse; text-align: left; }}
            th {{ background: #2c2c2c; padding: 10px; }}
            a.btn {{ display: inline-block; padding: 8px 16px; background: #ff4757; color: white; text-decoration: none; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <div class="card" style="display: flex; justify-content: space-between; align-items: center;">
            <h2>🚀 X4G Server Admin Dashboard</h2>
            <a href="/logout" class="btn">Logout</a>
        </div>
        <div class="card">
            <h3>Active Keys ({len(LINKS)})</h3>
            <table>
                <thead>
                    <tr>
                        <th>Label</th>
                        <th>Subscription URL</th>
                        <th>VLESS Link</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html if rows_html else '<tr><td colspan="3" style="padding: 15px; text-align: center;">No keys created yet.</td></tr>'}
                </tbody>
            </table>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Login - X4G Dashboard</title>
        <style>
            body { font-family: sans-serif; background: #121212; color: white; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            .login-box { background: #1e1e1e; padding: 30px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); width: 300px; text-align: center; }
            input[type="password"] { width: 90%; padding: 10px; margin: 15px 0; background: #2c2c2c; border: 1px solid #444; color: white; border-radius: 4px; }
            button { width: 100%; padding: 10px; background: #007bff; border: none; color: white; font-weight: bold; border-radius: 4px; cursor: pointer; }
        </style>
    </head>
    <body>
        <div class="login-box">
            <h2>Admin Login</h2>
            <form action="/login" method="post">
                <input type="password" name="password" placeholder="Enter Password" required>
                <button type="submit">Login</button>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.post("/login")
async def login_submit(password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        session_id = secrets.token_hex(16)
        SESSIONS[session_id] = datetime.now() + timedelta(hours=24)
        response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
        response.set_cookie(key="session_id", value=session_id, httponly=True)
        return response
    return HTMLResponse("<h3>Wrong Password! <a href='/login'>Try again</a></h3>", status_code=400)

@app.get("/logout")
async def logout(session_id: Optional[str] = Cookie(None)):
    if session_id in SESSIONS:
        del SESSIONS[session_id]
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("session_id")
    return response

# ── Subscription API Endpoint ────────────────────────────────────────────────
@app.get("/sub/{uid}")
async def get_subscription(uid: str):
    if uid in LINKS and LINKS[uid].get("active"):
        host = get_host()
        link = vless_link_for_link(LINKS[uid], uid, host)
        return HTMLResponse(content=link, media_type="text/plain")
    raise HTTPException(status_code=404, detail="Key not found or disabled")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)

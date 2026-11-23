"""
Telegram Video Provider Bot - All-in-One (Termux Fixed)
--------------------------------------------------------
• Video link download (yt-dlp)
• Video file_id save (SQLite + JSON)
• Admin commands (/stats, /broadcast, /delete)
• Termux compatible (Permission Error Fixed)
"""

import os
import re
import json
import sqlite3
import logging
import asyncio
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import yt_dlp

# ============================================================
# CONFIG (Hardcoded as requested)
# ============================================================
BOT_TOKEN = "9233115100:AAFoMGI7n-c4vb0m-vMncmTQ4s9QNKnVb88"
ADMIN_ID = 8959111543

# Termux Permission Fix: Download folder ko Home directory mein rakha hai
DOWNLOAD_DIR = Path.home() / "video_bot_downloads"
DB_PATH = Path("videos.db")
METADATA_FILE = Path("file_metadata.json")
MAX_FILE_SIZE_MB = 200  # Telegram Bot API limit

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("VideoBot")

# ============================================================
# JSON METADATA HELPERS (Hosting compatibility ke liye)
# ============================================================
def load_file_metadata() -> dict:
    if METADATA_FILE.exists():
        try:
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_file_metadata(data: dict) -> None:
    try:
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    except IOError as e:
        logger.error(f"save_file_metadata error: {e}")

def get_file_metadata(file_id: str):
    return load_file_metadata().get(str(file_id))

def delete_file_metadata(file_id: str) -> bool:
    data = load_file_metadata()
    if str(file_id) in data:
        del data[str(file_id)]
        save_file_metadata(data)
        return True
    return False


# ============================================================
# SQLITE HELPERS
# ============================================================
def init_db() -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL,
                title TEXT,
                uploader_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.commit()

def add_video(file_id: str, title: str, uploader_id: int) -> int:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(
            "INSERT INTO videos (file_id, title, uploader_id) VALUES (?, ?, ?)",
            (file_id, title, uploader_id),
        )
        con.commit()
        return cur.lastrowid

def get_video(vid_id: int):
    with sqlite3.connect(DB_PATH) as con:
        return con.execute(
            "SELECT id, file_id, title FROM videos WHERE id = ?", (vid_id,)
        ).fetchone()

def list_videos(limit: int = 30):
    with sqlite3.connect(DB_PATH) as con:
        return con.execute(
            "SELECT id, title FROM videos ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

def count_videos() -> int:
    with sqlite3.connect(DB_PATH) as con:
        return con.execute("SELECT COUNT(*) FROM videos").fetchone()[0]

def delete_video(vid_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("DELETE FROM videos WHERE id = ?", (vid_id,))
        con.commit()
        return cur.rowcount > 0

def register_user(user_id: int, username, first_name) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
            (user_id, username or "", first_name or ""),
        )
        con.commit()

def count_users() -> int:
    with sqlite3.connect(DB_PATH) as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]

def all_user_ids():
    with sqlite3.connect(DB_PATH) as con:
        return [r[0] for r in con.execute("SELECT user_id FROM users").fetchall()]


# ============================================================
# HELPERS
# ============================================================
URL_REGEX = re.compile(r"^https?://", re.IGNORECASE)

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def download_video(url: str) -> Path | None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(DOWNLOAD_DIR / "%(title).80s-%(id)s.%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": MAX_FILE_SIZE_MB * 1024 * 1024,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            p = Path(ydl.prepare_filename(info))
            return p if p.exists() else None
    except Exception as e:
        logger.error(f"yt-dlp error: {e}")
        return None


# ============================================================
# HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.first_name)
    await update.message.reply_text(
        f"👋 Namaste *{user.first_name}*!\n\n"
        "Main ek *Video Provider Bot* hoon. 🎬\n\n"
        "• Video *link* bhejein → download karke bhejunga\n"
        "• *Video file* bhejein → save kar lunga\n\n"
        "🔗 *Commands:*\n"
        "/list – Saved videos\n"
        "/get <id> – Video paayein\n"
        "/stats – Statistics (admin)\n"
        "/broadcast – Message (admin)\n"
        "/delete <id> – Delete (admin)",
        parse_mode="Markdown",
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.first_name)
    text = (update.message.text or "").strip()

    if not URL_REGEX.match(text):
        await update.message.reply_text("❌ Valid link bhejein (`http://` ya `https://`).", parse_mode="Markdown")
        return

    msg = await update.message.reply_text("⏳ *Download ho raha hai...*", parse_mode="Markdown")
    loop = asyncio.get_running_loop()
    file_path = await loop.run_in_executor(None, download_video, text)

    if not file_path or not file_path.exists():
        await msg.edit_text("❌ Download fail. Link check karein.")
        return

    size_mb = file_path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        await msg.edit_text(f"❌ File {size_mb:.1f}MB — limit {MAX_FILE_SIZE_MB}MB.")
        file_path.unlink(missing_ok=True)
        return

    try:
        with open(file_path, "rb") as vf:
            sent = await update.message.reply_video(
                video=vf,
                caption=f"🎬 *{file_path.stem[:120]}*",
                parse_mode="Markdown",
                supports_streaming=True,
                read_timeout=300,
                write_timeout=300,
            )
        vid_id = add_video(sent.video.file_id, file_path.stem, user.id)
        await msg.delete()
        await update.message.reply_text(f"✅ Saved with ID: `{vid_id}`", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"send video error: {e}")
        await msg.edit_text("❌ Video bhejne mein error.")
    finally:
        file_path.unlink(missing_ok=True)

async def handle_video_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.first_name)
    video = update.message.video
    if not video:
        return
    title = video.file_name or f"Video_{video.file_unique_id}"
    vid_id = add_video(video.file_id, title, user.id)
    await update.message.reply_text(
        f"✅ *Saved!*\n🆔 ID: `{vid_id}`\n📁 {title}\n\n`/get {vid_id}`",
        parse_mode="Markdown",
    )

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = list_videos(50)
    if not rows:
        await update.message.reply_text("📭 Koi video nahi.")
        return
    total = count_videos()
    lines = [f"📚 *Saved Videos* ({len(rows)} of {total})\n"]
    for vid_id, title in rows:
        lines.append(f"`{vid_id}` → {(title or 'Untitled')[:50]}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/get <id>`", parse_mode="Markdown")
        return
    try:
        vid_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID number hona chahiye.")
        return
    row = get_video(vid_id)
    if not row:
        await update.message.reply_text("❌ Video nahi mila.")
        return
    _, file_id, title = row
    try:
        await update.message.reply_video(
            video=file_id,
            caption=f"🎬 *{(title or '')[:120]}*",
            parse_mode="Markdown",
            supports_streaming=True,
        )
    except Exception as e:
        logger.error(f"get error: {e}")
        await update.message.reply_text("❌ Bhejne mein error.")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    await update.message.reply_text(
        f"📊 *Stats*\n👥 Users: `{count_users()}`\n🎬 Videos: `{count_videos()}`",
        parse_mode="Markdown",
    )

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: `/broadcast <msg>`", parse_mode="Markdown")
        return
    msg_text = " ".join(context.args)
    users = all_user_ids()
    status = await update.message.reply_text(f"📤 Sending to {len(users)}...")
    sent = failed = 0
    for uid in users:
        try:
            await context.bot.send_message(uid, msg_text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status.edit_text(f"✅ Done! Sent: {sent} | Failed: {failed}")

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: `/delete <id>`", parse_mode="Markdown")
        return
    try:
        vid_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID number hona chahiye.")
        return
    if delete_video(vid_id):
        await update.message.reply_text(f"🗑️ Deleted `{vid_id}`.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ `{vid_id}` nahi mila.", parse_mode="Markdown")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception:", exc_info=context.error)


# ============================================================
# MAIN
# ============================================================
def main() -> None:
    init_db()
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("get", get_cmd))
    
    # Admin Commands
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    
    # Messages
    app.add_handler(MessageHandler(filters.VIDEO, handle_video_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Error Handler
    app.add_error_handler(error_handler)

    logger.info("🚀 Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
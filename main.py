import os
import re
import json
import time
import uuid
import base64
import shutil
import zipfile
import tempfile
import logging
import sqlite3
import asyncio
import stat
from pathlib import PurePosixPath
from datetime import datetime, timezone
from threading import Thread
from urllib.parse import quote

import requests
from flask import Flask, jsonify, request, abort
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, TypeHandler, ApplicationHandlerStop
)

# ========================= CONFIG =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8959111543"))
FORCE_SUBSCRIBE = os.getenv("FORCE_SUBSCRIBE", "true").lower() == "true"
CHANNEL_ID = int(os.getenv("FORCE_SUB_CHANNEL_ID", "-1003636772569"))
CHANNEL_LINK = os.getenv("FORCE_SUB_CHANNEL_LINK", "https://t.me/MATRIX_TRADER777").strip()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "nxariyan2222-png").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "MATRIX-HOST12").strip().removesuffix(".git")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip()
GITHUB_API = "https://api.github.com"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"

RENDER_API_KEY = os.getenv("RENDER_API_KEY", "").strip()
RENDER_OWNER_ID = os.getenv("RENDER_OWNER_ID", "").strip()
RENDER_REGION = os.getenv("RENDER_REGION", "oregon").strip()
RENDER_PLAN = os.getenv("RENDER_PLAN", "free").strip()
RENDER_API = "https://api.render.com/v1"

PORT = int(os.getenv("PORT", "10000"))
DATA_DIR = os.getenv("DATA_DIR", "/tmp/matrix_hosting").strip()
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
MAX_ZIP_EXPANDED_MB = int(os.getenv("MAX_ZIP_EXPANDED_MB", "100"))
MAX_ZIP_FILES = int(os.getenv("MAX_ZIP_FILES", "200"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip() or uuid.uuid4().hex
webhook_application = None
webhook_loop = None

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if FORCE_SUBSCRIBE and not CHANNEL_ID:
    raise RuntimeError("FORCE_SUB_CHANNEL_ID is required when FORCE_SUBSCRIBE=true")

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "matrix_hosting.sqlite3")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("matrix-hosting")

# ========================= DATABASE =========================
def db_connect():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=15)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def qexec(con, sql, params=()):
    if DATABASE_URL:
        sql = sql.replace("?", "%s")
    return con.execute(sql, params)


def init_db():
    with db_connect() as con:
        if DATABASE_URL:
            qexec(con, """CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT,
                joined_at TEXT NOT NULL, last_seen TEXT NOT NULL, banned INTEGER NOT NULL DEFAULT 0)""")
            qexec(con, """CREATE TABLE IF NOT EXISTS uploads (
                id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, file_id TEXT NOT NULL,
                file_name TEXT NOT NULL, file_size BIGINT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL, reviewed_at TEXT, service_id TEXT, service_url TEXT,
                project_name TEXT, repo_branch TEXT, error TEXT)""")
            qexec(con, """CREATE TABLE IF NOT EXISTS projects (
                id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, upload_id BIGINT NOT NULL,
                name TEXT NOT NULL, service_id TEXT NOT NULL, service_url TEXT,
                repo_branch TEXT NOT NULL, entrypoint TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'created',
                created_at TEXT NOT NULL)""")
        else:
            qexec(con, """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                joined_at TEXT NOT NULL, last_seen TEXT NOT NULL, banned INTEGER NOT NULL DEFAULT 0)""")
            qexec(con, """CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, file_id TEXT NOT NULL,
                file_name TEXT NOT NULL, file_size INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL, reviewed_at TEXT, service_id TEXT, service_url TEXT,
                project_name TEXT, repo_branch TEXT, error TEXT)""")
            qexec(con, """CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, upload_id INTEGER NOT NULL,
                name TEXT NOT NULL, service_id TEXT NOT NULL, service_url TEXT,
                repo_branch TEXT NOT NULL, entrypoint TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'created',
                created_at TEXT NOT NULL)""")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def save_user(user):
    now = now_iso()
    with db_connect() as con:
        if DATABASE_URL:
            qexec(con, """INSERT INTO users(user_id,username,first_name,joined_at,last_seen)
                VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                username=EXCLUDED.username, first_name=EXCLUDED.first_name, last_seen=EXCLUDED.last_seen""",
                (user.id, user.username or "", user.first_name or "", now, now))
        else:
            qexec(con, """INSERT INTO users(user_id,username,first_name,joined_at,last_seen)
                VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username, first_name=excluded.first_name, last_seen=excluded.last_seen""",
                (user.id, user.username or "", user.first_name or "", now, now))


def is_banned(uid):
    with db_connect() as con:
        row = qexec(con, "SELECT banned FROM users WHERE user_id=?", (uid,)).fetchone()
    return bool(row and row["banned"])


def insert_id(con, sql, params=()):
    if DATABASE_URL:
        row = qexec(con, sql + " RETURNING id", params).fetchone()
        return row["id"]
    return qexec(con, sql, params).lastrowid


def get_upload(upload_id):
    with db_connect() as con:
        return qexec(con, "SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()


def get_project(project_id):
    with db_connect() as con:
        return qexec(con, "SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()


def user_projects(uid):
    with db_connect() as con:
        return qexec(con, "SELECT * FROM projects WHERE user_id=? AND status!='deleted' ORDER BY id DESC", (uid,)).fetchall()


def all_projects():
    with db_connect() as con:
        return qexec(con, "SELECT * FROM projects WHERE status!='deleted' ORDER BY id DESC LIMIT 50").fetchall()

# ========================= MEMBERSHIP GATE =========================
async def is_channel_member(bot, user_id):
    if not FORCE_SUBSCRIBE or user_id == ADMIN_ID:
        return True
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        status = getattr(member, "status", "")
        if status in ("creator", "administrator", "member"):
            return True
        return status == "restricted" and bool(getattr(member, "is_member", False))
    except Exception as exc:
        log.warning("Membership check failed for %s: %s", user_id, exc)
        return False


def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join MATRIX Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ Verify Subscription", callback_data="verify_sub")]
    ])


async def membership_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    if user.id == ADMIN_ID:
        return
    if is_banned(user.id):
        if update.effective_message:
            await update.effective_message.reply_text("⛔ Your access has been disabled by Admin.")
        elif update.callback_query:
            await update.callback_query.answer("Access disabled", show_alert=True)
        raise ApplicationHandlerStop
    if update.callback_query and update.callback_query.data == "verify_sub":
        return
    if not await is_channel_member(context.bot, user.id):
        if update.effective_message:
            await update.effective_message.reply_text(
                "🔒 **MATRIX HOSTING ACCESS LOCKED**\n\nJoin our Telegram channel, then press Verify Subscription.\nIf you leave the channel, access is blocked on your next request.",
                reply_markup=join_keyboard(), parse_mode="Markdown")
        elif update.callback_query:
            await update.callback_query.answer("Please join the required channel first.", show_alert=True)
        raise ApplicationHandlerStop

# ========================= GITHUB API =========================
def github_headers():
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is missing")
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MATRIX-Python-Hosting"
    }


def github_request(method, path, **kwargs):
    response = requests.request(method, GITHUB_API + path, headers=github_headers(), timeout=45, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"GitHub API {response.status_code}: {response.text[:700]}")
    return response.json() if response.content else {}


def delete_project_branch(branch):
    response = requests.delete(
        GITHUB_API + f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/refs/heads/{quote(branch, safe='')}",
        headers=github_headers(), timeout=45)
    if response.status_code not in (204, 404):
        raise RuntimeError(f"GitHub branch delete failed: {response.status_code} {response.text[:500]}")


def push_project_branch(branch, files):
    """Create an isolated orphan branch containing only this user's project files."""
    blobs = []
    for rel_path, content in files.items():
        blob = github_request("POST", f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/blobs", json={
            "content": base64.b64encode(content).decode("ascii"), "encoding": "base64"
        })
        blobs.append({"path": rel_path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = github_request("POST", f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/trees", json={"tree": blobs})
    commit = github_request("POST", f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/commits", json={
        "message": f"MATRIX hosting project {branch}", "tree": tree["sha"]
    })
    ref_path = f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/refs/heads/{branch}"
    check = requests.get(GITHUB_API + ref_path, headers=github_headers(), timeout=30)
    if check.status_code == 404:
        github_request("POST", f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/refs", json={
            "ref": f"refs/heads/{branch}", "sha": commit["sha"]
        })
    elif check.ok:
        github_request("PATCH", ref_path, json={"sha": commit["sha"], "force": True})
    else:
        raise RuntimeError(f"GitHub ref check failed: {check.status_code} {check.text[:500]}")

# ========================= ZIP VALIDATION =========================
def safe_project_files(source_path, original_name):
    files = {}
    entrypoint = None
    if original_name.lower().endswith(".py"):
        name = os.path.basename(original_name)
        if not name or name in (".", ".."):
            raise ValueError("Invalid Python filename")
        with open(source_path, "rb") as f:
            files[name] = f.read()
        entrypoint = name
    elif original_name.lower().endswith(".zip"):
        total_size = 0
        with zipfile.ZipFile(source_path) as archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
            if not infos or len(infos) > MAX_ZIP_FILES:
                raise ValueError(f"ZIP must contain 1–{MAX_ZIP_FILES} files")
            for info in infos:
                total_size += info.file_size
                if total_size > MAX_ZIP_EXPANDED_MB * 1024 * 1024:
                    raise ValueError("ZIP expanded size exceeds configured limit")
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise ValueError("ZIP symlinks are not allowed")
                raw = info.filename.replace("\\", "/")
                path = PurePosixPath(raw)
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    raise ValueError("Unsafe path detected inside ZIP")
                if any(part in (".git", ".github", "__pycache__") for part in path.parts):
                    continue
                if path.name.startswith(".env") or path.name.lower() in ("render.yaml", "dockerfile"):
                    continue
                if not all(re.fullmatch(r"[A-Za-z0-9._ -]{1,120}", p) for p in path.parts):
                    raise ValueError("ZIP contains unsupported filename characters")
                files[str(path)] = archive.read(info)
        py_files = sorted(p for p in files if p.lower().endswith(".py"))
        if not py_files:
            raise ValueError("ZIP must contain at least one .py file")
        entrypoint = "main.py" if "main.py" in files else py_files[0]
    else:
        raise ValueError("Only .py and .zip files are supported")

    if "requirements.txt" not in files:
        files["requirements.txt"] = b""
    # A private runner keeps the uploaded process separate from the controller service.
    files["matrix_runner.py"] = RUNNER_SOURCE.encode("utf-8")
    return files, entrypoint


RUNNER_SOURCE = r'''import os, sys, time, json, signal, subprocess, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ENTRY = os.environ.get("MATRIX_ENTRYPOINT", "main.py")
PORT = int(os.environ.get("PORT", "10000"))
ROOT = os.path.dirname(os.path.abspath(__file__))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            body = json.dumps({"status": "healthy", "project": os.path.basename(ROOT)}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *args):
        print("[health]", *args)

def serve():
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

threading.Thread(target=serve, daemon=True).start()
script = os.path.abspath(os.path.join(ROOT, ENTRY))
if not script.startswith(ROOT + os.sep) or not os.path.isfile(script):
    raise SystemExit("Invalid MATRIX_ENTRYPOINT")
restarts = 0
while True:
    print("[MATRIX] Starting user process:", ENTRY, flush=True)
    proc = subprocess.Popen([sys.executable, "-u", script], cwd=ROOT, env=os.environ.copy())
    code = proc.wait()
    print("[MATRIX] User process exited with code", code, flush=True)
    if os.environ.get("MATRIX_AUTORESTART", "true").lower() != "true" or restarts >= 5:
        break
    restarts += 1
    time.sleep(min(5 * restarts, 30))
'''

# ========================= RENDER API =========================
def render_headers():
    if not RENDER_API_KEY or not RENDER_OWNER_ID:
        raise RuntimeError("RENDER_API_KEY and RENDER_OWNER_ID are required")
    return {"Authorization": f"Bearer {RENDER_API_KEY}", "Accept": "application/json", "Content-Type": "application/json"}


def render_request(method, path, **kwargs):
    response = requests.request(method, RENDER_API + path, headers=render_headers(), timeout=60, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"Render API {response.status_code}: {response.text[:900]}")
    return response.json() if response.content else {}


def create_render_service(project_name, branch, entrypoint):
    slug = re.sub(r"[^a-z0-9-]+", "-", project_name.lower()).strip("-")[:22] or "python"
    service_name = f"matrix-{slug}-{branch[-6:]}"[:60]
    payload = {
        "type": "web_service",
        "name": service_name,
        "ownerId": RENDER_OWNER_ID,
        "repo": GITHUB_REPO_URL,
        "branch": branch,
        "autoDeployTrigger": "off",
        "envVars": [
            {"key": "MATRIX_ENTRYPOINT", "value": entrypoint},
            {"key": "MATRIX_AUTORESTART", "value": "true"}
        ],
        "serviceDetails": {
            "runtime": "python",
            "plan": RENDER_PLAN,
            "region": RENDER_REGION,
            "healthCheckPath": "/health",
            "envSpecificDetails": {
                "buildCommand": "pip install -r requirements.txt",
                "startCommand": "python matrix_runner.py"
            }
        }
    }
    result = render_request("POST", "/services", json=payload)
    service = result.get("service", result)
    service_id = service.get("id")
    if not service_id:
        raise RuntimeError("Render did not return a service ID: " + json.dumps(result)[:600])
    url = service.get("serviceDetails", {}).get("url") or service.get("url") or ""
    if not url:
        url = f"https://{service_name}.onrender.com"
    return service_id, url, result


def deploy_upload_local(source_path, original_name, project_name, project_id):
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is missing")
    if not RENDER_API_KEY or not RENDER_OWNER_ID:
        raise RuntimeError("RENDER_API_KEY or RENDER_OWNER_ID is missing")
    files, entrypoint = safe_project_files(source_path, original_name)
    branch = f"matrix-project-{project_id}-{uuid.uuid4().hex[:6]}"
    push_project_branch(branch, files)
    service_id, service_url, raw = create_render_service(project_name, branch, entrypoint)
    return {"service_id": service_id, "service_url": service_url, "branch": branch, "entrypoint": entrypoint, "raw": raw}

# ========================= FLASK HEALTH =========================
web = Flask(__name__)

@web.get("/")
def home():
    return jsonify({"name": "MATRIX PYTHON HOSTING", "status": "online", "admin_approval": True})

@web.get("/health")
def health():
    return jsonify({"status": "healthy", "service": "matrix-hosting-controller"}), 200


@web.post("/telegram/<secret>")
def telegram_webhook(secret):
    import hmac
    if not hmac.compare_digest(secret, WEBHOOK_SECRET):
        abort(404)
    if not hmac.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), WEBHOOK_SECRET):
        abort(403)
    if webhook_application is None or webhook_loop is None:
        return jsonify({"ok": False, "error": "bot_not_ready"}), 503
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "invalid_update"}), 400
    try:
        telegram_update = Update.de_json(payload, webhook_application.bot)
        future = asyncio.run_coroutine_threadsafe(webhook_application.update_queue.put(telegram_update), webhook_loop)
        future.result(timeout=8)
        return jsonify({"ok": True}), 200
    except Exception as exc:
        log.exception("Webhook update enqueue failed: %s", exc)
        return jsonify({"ok": False}), 500


def run_web():
    web.run(host="0.0.0.0", port=PORT, use_reloader=False)

# ========================= TELEGRAM UI =========================
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["📤 Upload Python File", "📦 My Bots"], ["📊 My Status", "❓ Help"]],
    resize_keyboard=True
)


def project_keyboard(project_id, admin=False):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Start", callback_data=f"p:start:{project_id}"),
         InlineKeyboardButton("⏹ Stop", callback_data=f"p:stop:{project_id}")],
        [InlineKeyboardButton("🔄 Restart", callback_data=f"p:restart:{project_id}"),
         InlineKeyboardButton("📋 Logs", callback_data=f"p:logs:{project_id}")],
        [InlineKeyboardButton("🗑 Delete", callback_data=f"p:delete:{project_id}")]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    await update.effective_message.reply_text(
        "🚀 **WELCOME TO MATRIX PYTHON HOSTING**\n\nSend a `.py` file or `.zip` Python project. Every upload goes to Admin for approval. After approval it is deployed to your own Render service.\n\nUse My Bots to manage only your own deployments.",
        reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")


async def verify_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if await is_channel_member(context.bot, q.from_user.id):
        await q.answer("Subscription verified!", show_alert=True)
        await q.edit_message_text("✅ Subscription verified. You can now use MATRIX HOSTING.")
        await context.bot.send_message(q.from_user.id, "Welcome back! Your access is restored.", reply_markup=MAIN_KEYBOARD)
    else:
        await q.answer("You are not a member yet. Join the channel first.", show_alert=True)


async def upload_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("📤 Send your `.py` file or a `.zip` containing at least one Python file. The Admin must approve each upload before deployment.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)
    doc = update.effective_message.document
    filename = os.path.basename(doc.file_name or "upload")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".py", ".zip"):
        await update.effective_message.reply_text("❌ Only `.py` and `.zip` files are accepted.", parse_mode="Markdown")
        return
    if doc.file_size and doc.file_size > MAX_UPLOAD_MB * 1024 * 1024:
        await update.effective_message.reply_text(f"❌ File too large. Maximum upload size: {MAX_UPLOAD_MB} MB.")
        return
    with db_connect() as con:
        upload_id = insert_id(con, "INSERT INTO uploads(user_id,file_id,file_name,file_size,status,created_at) VALUES(?,?,?,?,?,?)",
                              (user.id, doc.file_id, filename, int(doc.file_size or 0), "pending", now_iso()))
    caption = (f"NEW HOSTING APPROVAL #{upload_id}\n\n"
               f"User: {user.full_name}\nUsername: @{user.username or '-'}\nTelegram ID: {user.id}\n"
               f"File: {filename}\nSize: {round((doc.file_size or 0)/1024/1024, 2)} MB\n\n"
               "Review this file before approving. Approved code runs in a separate Render service.")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve & Deploy", callback_data=f"approve_upload:{upload_id}"),
                                InlineKeyboardButton("❌ Reject", callback_data=f"reject_upload:{upload_id}")]])
    try:
        await context.bot.send_document(ADMIN_ID, document=doc.file_id, caption=caption, reply_markup=kb)
        await update.effective_message.reply_text(f"✅ Upload received. Request #{upload_id} is waiting for Admin approval.")
    except Exception as exc:
        log.exception("Could not notify admin")
        with db_connect() as con:
            qexec(con, "UPDATE uploads SET status='failed', error=? WHERE id=?", (str(exc)[:500], upload_id))
        await update.effective_message.reply_text("Upload saved, but Admin notification failed. Please try again later.")


async def approve_upload_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, upload_id: int):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("Admin only", show_alert=True)
        return
    row = get_upload(upload_id)
    if not row or row["status"] != "pending":
        await q.answer("This request is no longer pending.", show_alert=True)
        return
    with db_connect() as con:
        qexec(con, "UPDATE uploads SET status='deploying', reviewed_at=? WHERE id=?", (now_iso(), upload_id))
    await q.answer("Deployment started")
    try:
        await q.edit_message_caption(caption=f"⏳ Upload #{upload_id}: downloading and deploying to GitHub + Render...")
    except Exception:
        pass
    temp_dir = tempfile.mkdtemp(prefix="matrix-upload-")
    try:
        tg_file = await context.bot.get_file(row["file_id"])
        source_path = os.path.join(temp_dir, row["file_name"])
        await tg_file.download_to_drive(custom_path=source_path)
        safe_name = os.path.splitext(os.path.basename(row["file_name"]))[0]
        project_name = re.sub(r"[^A-Za-z0-9 _-]", "", safe_name).strip()[:40] or f"project-{upload_id}"
        result = await asyncio.to_thread(deploy_upload_local, source_path, row["file_name"], project_name, upload_id)
        with db_connect() as con:
            project_id = insert_id(con, "INSERT INTO projects(user_id,upload_id,name,service_id,service_url,repo_branch,entrypoint,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                   (row["user_id"], upload_id, project_name, result["service_id"], result["service_url"], result["branch"], result["entrypoint"], "created", now_iso()))
            qexec(con, "UPDATE uploads SET status='approved',service_id=?,service_url=?,project_name=?,repo_branch=? WHERE id=?",
                  (result["service_id"], result["service_url"], project_name, result["branch"], upload_id))
        try:
            await context.bot.send_message(row["user_id"],
                f"🎉 Your Python project has been approved and sent to Render!\n\nProject ID: {project_id}\nName: {project_name}\nService: {result['service_id']}\nURL: {result['service_url']}\n\nUse /mybots to manage it. First build may take a few minutes.")
        except Exception as notify_exc:
            log.warning("Deployment succeeded but user notification failed: %s", notify_exc)
        try:
            await q.edit_message_caption(caption=f"APPROVED & DEPLOYMENT CREATED\nRequest: #{upload_id}\nProject: {project_name}\nProject ID: {project_id}\nService: {result['service_id']}\nURL: {result['service_url']}")
        except Exception:
            pass
    except Exception as exc:
        log.exception("Deployment failed for upload %s", upload_id)
        with db_connect() as con:
            qexec(con, "UPDATE uploads SET status='failed',error=? WHERE id=?", (str(exc)[:1500], upload_id))
        await context.bot.send_message(ADMIN_ID, f"❌ Deployment failed for request #{upload_id}:\n`{str(exc)[:2500]}`", parse_mode="Markdown")
        try:
            await context.bot.send_message(row["user_id"], f"❌ Your deployment could not be created. Admin has been notified.\nReason: {str(exc)[:700]}")
        except Exception:
            pass
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def reject_upload_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, upload_id: int):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("Admin only", show_alert=True)
        return
    row = get_upload(upload_id)
    if not row or row["status"] != "pending":
        await q.answer("Request is not pending", show_alert=True)
        return
    with db_connect() as con:
        qexec(con, "UPDATE uploads SET status='rejected',reviewed_at=? WHERE id=?", (now_iso(), upload_id))
    await q.answer("Rejected")
    try:
        await q.edit_message_caption(caption=f"❌ Upload request #{upload_id} rejected by Admin.")
    except Exception:
        pass
    try:
        await context.bot.send_message(row["user_id"], f"❌ Your hosting upload request #{upload_id} was rejected by Admin.")
    except Exception as notify_exc:
        log.warning("Could not notify rejected user: %s", notify_exc)

# ========================= PROJECT MANAGEMENT =========================
def render_action(service_id, action):
    service_path = f"/services/{quote(service_id, safe='')}"
    if action == "delete":
        response = requests.delete(RENDER_API + service_path, headers=render_headers(), timeout=60)
        if response.status_code >= 400:
            raise RuntimeError(f"Render API {response.status_code}: {response.text[:700]}")
        return "Deleted"
    endpoints = {"start": "resume", "stop": "suspend", "restart": "restart"}
    if action not in endpoints:
        raise ValueError("Unsupported action")
    try:
        return render_request("POST", f"{service_path}/{endpoints[action]}")
    except RuntimeError:
        # A sleeping (but not explicitly suspended) Free service may need a restart to wake.
        if action == "start":
            return render_request("POST", f"{service_path}/restart")
        raise


def render_status(service_id):
    return render_request("GET", f"/services/{quote(service_id, safe='')}")


def render_logs(service_id):
    params = {"ownerId": RENDER_OWNER_ID, "resource": service_id, "direction": "backward", "limit": 30}
    response = requests.get(RENDER_API + "/logs", headers=render_headers(), params=params, timeout=45)
    if response.status_code >= 400:
        raise RuntimeError(f"Render logs API {response.status_code}: {response.text[:500]}")
    data = response.json()
    if isinstance(data, dict):
        items = data.get("logs") or data.get("items") or data.get("data") or []
    else:
        items = data
    lines = []
    for item in items[-30:]:
        if isinstance(item, dict):
            lines.append(f"{item.get('timestamp','')} {item.get('message','')}")
        else:
            lines.append(str(item))
    return "\n".join(lines) or "No recent logs returned by Render."


async def send_project_list(update, uid, admin=False):
    rows = all_projects() if admin else user_projects(uid)
    if not rows:
        await update.effective_message.reply_text("No hosted Python bots found yet. Send a .py or .zip file to request hosting.")
        return
    for row in rows[:30]:
        if not admin and row["user_id"] != uid:
            continue
        caption = (f"📦 {row['name']}\nProject ID: {row['id']}\nOwner: {row['user_id']}\n"
                   f"Status: {row['status']}\nService: {row['service_id']}\n{row['service_url'] or ''}")
        await update.effective_message.reply_text(caption, reply_markup=project_keyboard(row["id"], admin))


async def mybots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_project_list(update, update.effective_user.id, admin=False)


async def admin_allbots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await send_project_list(update, ADMIN_ID, admin=True)


async def project_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, project_id: int):
    q = update.callback_query
    uid = q.from_user.id
    row = get_project(project_id)
    if not row or row["status"] == "deleted":
        await q.answer("Project not found", show_alert=True)
        return
    if uid != ADMIN_ID and row["user_id"] != uid:
        await q.answer("This project belongs to another user.", show_alert=True)
        return
    await q.answer("Processing...")
    try:
        if action == "logs":
            logs = await asyncio.to_thread(render_logs, row["service_id"])
            import html
            await context.bot.send_message(uid, f"📋 Logs for {row['name']}:\n\n<pre>{html.escape(logs[:3500])}</pre>", parse_mode="HTML")
        elif action == "status":
            data = await asyncio.to_thread(render_status, row["service_id"])
            service = data.get("service", data)
            await context.bot.send_message(uid, f"📊 {row['name']}\nService ID: {row['service_id']}\nState: {service.get('suspended','unknown')}\nURL: {row['service_url']}")
        else:
            await asyncio.to_thread(render_action, row["service_id"], action)
            if action == "delete":
                branch_cleanup = ""
                try:
                    await asyncio.to_thread(delete_project_branch, row["repo_branch"])
                    branch_cleanup = " GitHub project branch also removed."
                except Exception as cleanup_exc:
                    log.warning("Project branch cleanup failed: %s", cleanup_exc)
                    branch_cleanup = " GitHub branch cleanup needs manual review."
                with db_connect() as con:
                    qexec(con, "UPDATE projects SET status='deleted' WHERE id=?", (project_id,))
                await q.edit_message_text(f"🗑 Project {project_id} deleted from Render.{branch_cleanup}")
            else:
                with db_connect() as con:
                    qexec(con, "UPDATE projects SET status=? WHERE id=?", ("stopped" if action == "stop" else "running", project_id))
                await q.edit_message_text(f"✅ {action.title()} request sent to Render for {row['name']}.")
    except Exception as exc:
        log.exception("Project action failed")
        await context.bot.send_message(uid, f"❌ Render action failed: {str(exc)[:1000]}")

# ========================= ENV VAR MANAGEMENT =========================
RESERVED_ENV = {"PORT", "MATRIX_ENTRYPOINT", "MATRIX_AUTORESTART", "GITHUB_TOKEN", "RENDER_API_KEY", "BOT_TOKEN"}

async def setenv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.effective_message.reply_text("Usage: /setenv PROJECT_ID KEY VALUE\nExample: /setenv 12 BOT_TOKEN 123456:ABC")
        return
    try:
        project_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Invalid project ID")
        return
    key = context.args[1].upper()
    value = " ".join(context.args[2:])
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,49}", key) or key in RESERVED_ENV:
        await update.effective_message.reply_text("Invalid or reserved environment variable name.")
        return
    row = get_project(project_id)
    if not row or (update.effective_user.id != ADMIN_ID and row["user_id"] != update.effective_user.id):
        await update.effective_message.reply_text("Project not found or not yours.")
        return
    try:
        path = f"/services/{quote(row['service_id'], safe='')}/env-vars/{quote(key, safe='')}"
        await asyncio.to_thread(render_request, "PUT", path, json={"value": value})
        await asyncio.to_thread(render_request, "POST", f"/services/{quote(row['service_id'], safe='')}/deploys", json={})
        await update.effective_message.reply_text(f"✅ `{key}` saved in Render and redeploy requested. Value is hidden.", parse_mode="Markdown")
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Could not update variable: {str(exc)[:700]}")

# ========================= ADMIN COMMANDS =========================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Pending Uploads", callback_data="admin:pending"), InlineKeyboardButton("📦 All Bots", callback_data="admin:bots")],
        [InlineKeyboardButton("📊 Statistics", callback_data="admin:stats")]
    ])
    await update.effective_message.reply_text("👑 **MATRIX SUPER ADMIN PANEL**", reply_markup=kb, parse_mode="Markdown")


async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    with db_connect() as con:
        rows = qexec(con, "SELECT * FROM uploads WHERE status='pending' ORDER BY id DESC LIMIT 30").fetchall()
    if not rows:
        await update.effective_message.reply_text("No pending uploads.")
        return
    for row in rows:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve & Deploy", callback_data=f"approve_upload:{row['id']}"),
                                   InlineKeyboardButton("❌ Reject", callback_data=f"reject_upload:{row['id']}")]])
        await update.effective_message.reply_text(
            f"📥 Request #{row['id']}\nUser: {row['user_id']}\nFile: {row['file_name']}\nStatus: {row['status']}", reply_markup=kb)


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    with db_connect() as con:
        users = qexec(con, "SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        pending = qexec(con, "SELECT COUNT(*) AS n FROM uploads WHERE status='pending'").fetchone()["n"]
        projects = qexec(con, "SELECT COUNT(*) AS n FROM projects WHERE status!='deleted'").fetchone()["n"]
        running = qexec(con, "SELECT COUNT(*) AS n FROM projects WHERE status='running'").fetchone()["n"]
    await update.effective_message.reply_text(f"📊 MATRIX HOSTING STATS\nUsers: {users}\nPending uploads: {pending}\nProjects: {projects}\nLast requested running: {running}")


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Admin usage: /ban USER_ID")
        return
    with db_connect() as con:
        qexec(con, "UPDATE users SET banned=1 WHERE user_id=?", (int(context.args[0]),))
    await update.effective_message.reply_text("User banned. Membership/command access is blocked.")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Admin usage: /unban USER_ID")
        return
    with db_connect() as con:
        qexec(con, "UPDATE users SET banned=0 WHERE user_id=?", (int(context.args[0]),))
    await update.effective_message.reply_text("User unbanned. Channel membership is still required.")


async def delenv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2 or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /delenv PROJECT_ID KEY")
        return
    project_id = int(context.args[0])
    key = context.args[1].upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,49}", key) or key in RESERVED_ENV:
        await update.effective_message.reply_text("Invalid or reserved environment variable name.")
        return
    row = get_project(project_id)
    if not row or (update.effective_user.id != ADMIN_ID and row["user_id"] != update.effective_user.id):
        await update.effective_message.reply_text("Project not found or not yours.")
        return
    try:
        path = f"/services/{quote(row['service_id'], safe='')}/env-vars/{quote(key, safe='')}"
        response = requests.delete(RENDER_API + path, headers=render_headers(), timeout=45)
        if response.status_code >= 400:
            raise RuntimeError(f"Render API {response.status_code}: {response.text[:500]}")
        await asyncio.to_thread(render_request, "POST", f"/services/{quote(row['service_id'], safe='')}/deploys", json={})
        await update.effective_message.reply_text(f"✅ {key} deleted and redeploy requested.")
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Could not delete variable: {str(exc)[:700]}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("MATRIX PYTHON HOSTING\n\nSend .py or .zip to request hosting. Admin approval is required for every file.\n"
            "/mybots - manage your bots\n/status PROJECT_ID - service status\n/setenv PROJECT_ID KEY VALUE - set a secret env variable\n"
            "/upload - upload instructions\n/help - help")
    if update.effective_user.id == ADMIN_ID:
        text += "\n\nAdmin: /admin, /pending, /allbots, /stats, /ban ID, /unban ID"
    await update.effective_message.reply_text(text)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /status PROJECT_ID")
        return
    project_id = int(context.args[0])
    row = get_project(project_id)
    if not row or (update.effective_user.id != ADMIN_ID and row["user_id"] != update.effective_user.id):
        await update.effective_message.reply_text("Project not found or not yours.")
        return
    try:
        data = await asyncio.to_thread(render_status, row["service_id"])
        service = data.get("service", data)
        await update.effective_message.reply_text(f"📊 {row['name']}\nService: {row['service_id']}\nSuspended: {service.get('suspended','unknown')}\nURL: {row['service_url']}")
    except Exception as exc:
        await update.effective_message.reply_text(f"Could not fetch status: {str(exc)[:700]}")


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    if text == "📤 Upload Python File":
        await upload_instructions(update, context)
    elif text == "📦 My Bots":
        await mybots(update, context)
    elif text == "📊 My Status":
        await update.effective_message.reply_text("Use /mybots to view your hosted bots, then choose Status or use /status PROJECT_ID.")
    elif text == "❓ Help":
        await help_command(update, context)


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    if data == "verify_sub":
        await verify_sub_callback(update, context)
        return
    if data.startswith("approve_upload:"):
        await approve_upload_callback(update, context, int(data.split(":", 1)[1]))
        return
    if data.startswith("reject_upload:"):
        await reject_upload_callback(update, context, int(data.split(":", 1)[1]))
        return
    if data == "admin:pending":
        if q.from_user.id != ADMIN_ID:
            await q.answer("Admin only", show_alert=True); return
        await q.answer()
        await admin_pending(update, context)
        return
    if data == "admin:bots":
        if q.from_user.id != ADMIN_ID:
            await q.answer("Admin only", show_alert=True); return
        await q.answer()
        await send_project_list(update, ADMIN_ID, admin=True)
        return
    if data == "admin:stats":
        if q.from_user.id != ADMIN_ID:
            await q.answer("Admin only", show_alert=True); return
        await q.answer()
        await admin_stats(update, context)
        return
    if data.startswith("p:"):
        try:
            _, action, pid = data.split(":", 2)
            await project_callback(update, context, action, int(pid))
        except Exception as exc:
            log.exception("Invalid project callback: %s", exc)
            await q.answer("Invalid action", show_alert=True)
        return
    await q.answer("Unknown action", show_alert=True)

# ========================= MAIN =========================
async def run_webhook_mode(application):
    global webhook_application, webhook_loop
    await application.initialize()
    await application.start()
    webhook_application = application
    webhook_loop = asyncio.get_running_loop()
    Thread(target=run_web, daemon=True).start()
    webhook_url = f"{RENDER_EXTERNAL_URL}/telegram/{WEBHOOK_SECRET}"
    await application.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )
    log.info("MATRIX Telegram webhook enabled at %s/telegram/[secret]", RENDER_EXTERNAL_URL)
    try:
        await asyncio.Event().wait()
    finally:
        try:
            await application.bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            pass
        await application.stop()
        await application.shutdown()


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    # Membership gate runs before every command, document, text and callback update.
    app.add_handler(TypeHandler(Update, membership_gate), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("upload", upload_instructions))
    app.add_handler(CommandHandler("mybots", mybots))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("setenv", setenv_command))
    app.add_handler(CommandHandler("delenv", delenv_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("pending", admin_pending))
    app.add_handler(CommandHandler("allbots", admin_allbots))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_handler(CallbackQueryHandler(callback_router))
    log.info("MATRIX Python Hosting bot starting")
    if RENDER_EXTERNAL_URL:
        # Telegram webhooks let incoming updates wake a sleeping Render Free web service.
        asyncio.run(run_webhook_mode(app))
    else:
        Thread(target=run_web, daemon=True).start()
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

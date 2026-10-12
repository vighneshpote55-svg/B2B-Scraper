#!/usr/bin/env python3
"""Standalone Telegram Bot for LeadMap Pro / B2B-Scraper.

Enables authorized users to:
- Trigger Google Maps scraping jobs (/scrape).
- Check real-time progress (/status JOB_ID).
- View recent jobs (/recent).
- Cancel running scrapes (/cancel JOB_ID).
- View lead intelligence metrics (/leads [JOB_ID]).
- Export sanitized CSV, CRM outreach CSV, and JSON directly to chat (/export [JOB_ID]).

Security & Architecture:
- Zero external pip dependencies — uses Python standard library for HTTPS, long-polling,
  multipart uploads, and state management.
- Strict numeric sender ID whitelist authorization (TELEGRAM_ALLOWED_USER_IDS).
- Deduplicates incoming Telegram updates (prevents repeated job starts on update replays).
- Sanitized user replies: never leaks bot tokens, Supabase keys, or internal stack traces.
"""
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Load .env
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

_env_path = os.path.join(REPO_ROOT, ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from scripts.scraper_engine import (
    DATA_DIR,
    cancel_job,
    check_scraper_health,
    export_leads,
    get_job_file_path,
    get_job_info,
    initiate_scrape_job,
    poll_and_process_job,
    validate_job_id,
    is_supabase_configured,
    fetch_recent_scrape_jobs_from_supabase,
    update_scrape_job_record
)

TELEGRAM_API_BASE = "https://api.telegram.org"
STATE_FILE = os.path.join(DATA_DIR, "telegram_state.json")
PROCESSED_UPDATES_FILE = os.path.join(DATA_DIR, "telegram_processed_updates.json")
STATE_LOCK = threading.Lock()
MAX_PROCESSED_UPDATES = 2000

# Input validation limits
MAX_KEYWORD_LENGTH = 200
MAX_LOCATION_LENGTH = 100
MIN_DEPTH = 1
MAX_DEPTH = 50
DEFAULT_DEPTH = 5


def get_bot_token() -> str:
    """Retrieve Telegram bot token from environment without logging or exposing secrets."""
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def get_allowed_user_ids() -> set:
    """Retrieve set of authorized Telegram numeric user IDs."""
    raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    if not raw:
        return set()
    allowed = set()
    for item in raw.split(","):
        item = item.strip()
        if item.isdigit():
            allowed.add(int(item))
    return allowed


def is_user_authorized(user_id) -> bool:
    """Verify if a Telegram sender numeric ID is whitelisted.

    Enforces integer sender ID validation (rejects strings, non-numerics, or usernames).
    """
    if not isinstance(user_id, int):
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            return False

    allowed = get_allowed_user_ids()
    if not allowed:
        return False
    return user_id in allowed


def sanitize_error_message(err: Exception) -> str:
    """Sanitize internal errors to prevent leaking tokens, database credentials, or stack traces."""
    err_str = str(err)
    token = get_bot_token()
    if token and token in err_str:
        err_str = err_str.replace(token, "[REDACTED_BOT_TOKEN]")

    sb_key = os.environ.get("SUPABASE_KEY", "")
    if sb_key and sb_key in err_str:
        err_str = err_str.replace(sb_key, "[REDACTED_KEY]")

    # Check for path disclosure
    if REPO_ROOT in err_str:
        err_str = err_str.replace(REPO_ROOT, "/app")

    # Generic fallback for unhandled technical exceptions
    if any(k in err_str.lower() for k in ("traceback", "syntaxerror", "operationalerror", "postgres")):
        return "An internal service error occurred. Please try again later."

    return err_str


# ── State and Update Deduplication ────────────────────────────────────────────

def _load_state() -> dict:
    """Load persistent chat state."""
    with STATE_LOCK:
        if os.path.isfile(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"chats": {}}


def _save_state(state: dict):
    """Save persistent chat state."""
    with STATE_LOCK:
        tmp_path = STATE_FILE + f".tmp_{os.getpid()}"
        try:
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, STATE_FILE)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


def set_user_active_job(chat_id: int, user_id: int, job_id: str, query: str):
    """Record active job for chat."""
    state = _load_state()
    chat_key = str(chat_id)
    chat_info = state["chats"].setdefault(chat_key, {})
    chat_info["active_job_id"] = job_id
    chat_info["last_job_id"] = job_id
    chat_info["last_query"] = query
    chat_info["user_id"] = user_id
    chat_info["updated_at"] = time.time()
    _save_state(state)


def get_user_last_job(chat_id: int) -> str:
    """Retrieve last known job_id for a chat."""
    state = _load_state()
    chat_info = state.get("chats", {}).get(str(chat_id), {})
    return chat_info.get("active_job_id") or chat_info.get("last_job_id") or ""


def _load_processed_updates() -> set:
    """Load processed update_ids set from disk."""
    with STATE_LOCK:
        if os.path.isfile(PROCESSED_UPDATES_FILE):
            try:
                with open(PROCESSED_UPDATES_FILE) as f:
                    data = json.load(f)
                    return set(data.get("processed_ids", []))
            except Exception:
                pass
        return set()


def _save_processed_updates(update_ids: set):
    """Save processed update_ids set to disk (bounded)."""
    with STATE_LOCK:
        id_list = list(update_ids)[-MAX_PROCESSED_UPDATES:]
        tmp_path = PROCESSED_UPDATES_FILE + f".tmp_{os.getpid()}"
        try:
            with open(tmp_path, "w") as f:
                json.dump({"processed_ids": id_list}, f)
            os.replace(tmp_path, PROCESSED_UPDATES_FILE)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


PROCESSED_UPDATES_CACHE = _load_processed_updates()


def is_update_duplicate(update_id: int) -> bool:
    """Check if update_id has already been processed."""
    if update_id is None:
        return False
    return update_id in PROCESSED_UPDATES_CACHE


def mark_update_processed(update_id: int):
    """Record update_id as processed."""
    if update_id is None:
        return
    PROCESSED_UPDATES_CACHE.add(update_id)
    if len(PROCESSED_UPDATES_CACHE) % 10 == 0:
        _save_processed_updates(PROCESSED_UPDATES_CACHE)


# ── Telegram HTTP API Client ──────────────────────────────────────────────────

class TelegramClient:
    """Zero-dependency HTTP client for Telegram Bot API using Python urllib."""

    def __init__(self, token: str = None, api_base: str = TELEGRAM_API_BASE):
        self.token = token or get_bot_token()
        self.api_base = api_base.rstrip("/")

    @property
    def base_url(self) -> str:
        return f"{self.api_base}/bot{self.token}"

    def api_request(self, method: str, data: dict = None, timeout: int = 40) -> dict:
        """Execute a POST request to Telegram Bot API."""
        if not self.token:
            return {"ok": False, "description": "TELEGRAM_BOT_TOKEN is not configured"}

        url = f"{self.base_url}/{method}"
        headers = {"Content-Type": "application/json"}
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            try:
                return json.loads(err_body)
            except Exception:
                return {"ok": False, "error_code": e.code, "description": sanitize_error_message(Exception(err_body))}
        except Exception as e:
            return {"ok": False, "description": sanitize_error_message(e)}

    def get_me(self) -> dict:
        """Test bot credentials."""
        return self.api_request("getMe")

    def get_updates(self, offset: int = None, timeout: int = 25) -> list:
        """Fetch incoming updates via long-polling."""
        payload = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        res = self.api_request("getUpdates", payload, timeout=timeout + 5)
        if res.get("ok"):
            return res.get("result", [])
        return []

    def send_message(self, chat_id: int, text: str, reply_to_message_id: int = None) -> dict:
        """Send a text message to chat."""
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        return self.api_request("sendMessage", payload)

    def send_document(self, chat_id: int, file_bytes: bytes, filename: str, caption: str = "") -> dict:
        """Upload and send a document file via multipart/form-data."""
        if not self.token:
            return {"ok": False, "description": "TELEGRAM_BOT_TOKEN is not configured"}

        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
        crlf = b"\r\n"

        parts = []
        # chat_id field
        parts.append(f"--{boundary}".encode("utf-8") + crlf)
        parts.append(b'Content-Disposition: form-data; name="chat_id"' + crlf + crlf)
        parts.append(str(chat_id).encode("utf-8") + crlf)

        # caption field
        if caption:
            parts.append(f"--{boundary}".encode("utf-8") + crlf)
            parts.append(b'Content-Disposition: form-data; name="caption"' + crlf + crlf)
            parts.append(caption.encode("utf-8") + crlf)

        # document field
        parts.append(f"--{boundary}".encode("utf-8") + crlf)
        parts.append(
            f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode("utf-8") + crlf
        )
        parts.append(b"Content-Type: application/octet-stream" + crlf + crlf)
        parts.append(file_bytes + crlf)
        parts.append(f"--{boundary}--".encode("utf-8") + crlf)

        body = b"".join(parts)
        url = f"{self.base_url}/sendDocument"
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body))
        }

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            try:
                return json.loads(err_body)
            except Exception:
                return {"ok": False, "description": sanitize_error_message(Exception(err_body))}
        except Exception as e:
            return {"ok": False, "description": sanitize_error_message(e)}


# ── Command Parsing & Input Validation ────────────────────────────────────────

def parse_scrape_command(text: str) -> tuple:
    """Parse and validate /scrape parameters with bounds checking.

    Supports formats:
    - /scrape "dentists" "Austin, TX" 5
    - /scrape dentists in Austin, TX
    - /scrape cafes in Miami --depth 10

    Returns (keyword, city, depth, error_msg).
    """
    raw = (text or "").strip()
    if raw.startswith("/scrape"):
        raw = raw[len("/scrape"):].strip()

    if not raw:
        return None, None, DEFAULT_DEPTH, "Please provide a keyword. Example: /scrape dentists in Austin, TX"

    depth = DEFAULT_DEPTH
    # Check for --depth flag
    depth_match = re.search(r"--depth\s+(\d+)", raw)
    if depth_match:
        try:
            depth = max(MIN_DEPTH, min(MAX_DEPTH, int(depth_match.group(1))))
        except ValueError:
            pass
        raw = re.sub(r"--depth\s+\d+", "", raw).strip()

    # Quoted string pattern: "keyword" "city" [depth]
    quoted = re.findall(r'"([^"]*)"', raw)
    if len(quoted) >= 2:
        keyword = quoted[0].strip()
        city = quoted[1].strip()
        remaining = re.sub(r'"[^"]*"', '', raw).strip().split()
        if remaining and remaining[0].isdigit():
            depth = max(MIN_DEPTH, min(MAX_DEPTH, int(remaining[0])))
    elif len(quoted) == 1:
        keyword = quoted[0].strip()
        city = re.sub(r'"[^"]*"', '', raw).strip()
    elif " in " in raw:
        parts = raw.split(" in ", 1)
        keyword = parts[0].strip()
        tail = parts[1].strip()
        tail_words = tail.split()
        if tail_words and tail_words[-1].isdigit():
            depth = max(MIN_DEPTH, min(MAX_DEPTH, int(tail_words[-1])))
            city = " ".join(tail_words[:-1]).strip()
        else:
            city = tail
    else:
        words = raw.split()
        if words and words[-1].isdigit():
            depth = max(MIN_DEPTH, min(MAX_DEPTH, int(words[-1])))
            keyword = " ".join(words[:-1]).strip()
            city = ""
        else:
            keyword = raw
            city = ""

    # Bounds & length validation
    if len(keyword) > MAX_KEYWORD_LENGTH:
        return None, None, depth, f"Keyword is too long (maximum {MAX_KEYWORD_LENGTH} characters)."
    if len(city) > MAX_LOCATION_LENGTH:
        return None, None, depth, f"Location is too long (maximum {MAX_LOCATION_LENGTH} characters)."
    if not keyword:
        return None, None, depth, "Keyword cannot be empty."

    return keyword, city, depth, ""


# ── Telegram Handler Dispatcher ───────────────────────────────────────────────

class TelegramBotHandler:
    """Handles dispatching, validating, and executing Telegram bot commands."""

    def __init__(self, client: TelegramClient):
        self.client = client

    def handle_update(self, update: dict):
        """Process a single Telegram update message safely."""
        try:
            # 1. Duplicate update check
            update_id = update.get("update_id")
            if update_id is not None:
                if is_update_duplicate(update_id):
                    print(f"[IDEMPOTENT] Duplicate update_id {update_id} discarded.", file=sys.stderr)
                    return
                mark_update_processed(update_id)

            message = update.get("message")
            if not message:
                return

            chat_id = message.get("chat", {}).get("id")
            user = message.get("from", {})
            user_id = user.get("id")
            text = (message.get("text") or "").strip()

            if not chat_id or user_id is None or not text:
                return

            # 2. Authorization Guard (numeric ID enforcement)
            if not is_user_authorized(user_id):
                print(f"[AUTH DENIED] Unauthorized user ID {user_id} attempted access.", file=sys.stderr)
                self.client.send_message(
                    chat_id,
                    f"⛔ Access Denied\nYour Telegram User ID ({user_id}) is not authorized to use LeadMap Pro.\n"
                    "Please configure TELEGRAM_ALLOWED_USER_IDS in .env."
                )
                return

            # 3. Command Dispatch
            tokens = text.split()
            cmd = tokens[0].lower()
            if "@" in cmd:  # Strip bot handle if present (e.g. /scrape@MyBot)
                cmd = cmd.split("@")[0]

            if cmd in ("/start", "/help"):
                self._handle_help(chat_id)
            elif cmd == "/scrape":
                self._handle_scrape(chat_id, user_id, update_id, text)
            elif cmd == "/status":
                self._handle_status(chat_id, tokens)
            elif cmd in ("/recent", "/history"):
                self._handle_recent(chat_id)
            elif cmd in ("/cancel", "/stop"):
                self._handle_cancel(chat_id, tokens)
            elif cmd == "/leads":
                self._handle_leads(chat_id, tokens)
            elif cmd == "/export":
                self._handle_export(chat_id, tokens)
            else:
                self.client.send_message(
                    chat_id,
                    f"Unknown command: '{cmd}'. Send /help to view available commands."
                )
        except Exception as e:
            print(f"[Telegram Handler Error] {sanitize_error_message(e)}", file=sys.stderr)

    def _handle_help(self, chat_id: int):
        help_text = (
            "🗺️ LeadMap Pro — Google Maps Lead Intelligence Bot\n\n"
            "Commands:\n"
            "• /scrape <query> — Start a new Google Maps scraping job\n"
            "    Examples:\n"
            '    /scrape dentists in Austin, TX\n'
            '    /scrape "cafes" "Miami, FL" 5\n'
            '    /scrape gyms in Denver --depth 10\n\n'
            "• /status <JOB_ID> — Check status and progress of a scrape job\n"
            "• /recent — View the 5 most recent scrape jobs\n"
            "• /cancel <JOB_ID> — Safely cancel a running scrape job\n"
            "• /leads [JOB_ID] — View lead metrics (HOT 🔥, WARM ⚡, COLD ❄️, emails, phones)\n"
            "• /export [JOB_ID] [csv|crm|json] — Download sanitized lead file directly in chat\n"
            "• /help — Show this help message"
        )
        self.client.send_message(chat_id, help_text)

    def _handle_scrape(self, chat_id: int, user_id: int, update_id: int, text: str):
        keyword, city, depth, err = parse_scrape_command(text)
        if err or not keyword:
            self.client.send_message(chat_id, f"⚠️ {err or 'Missing search query.'}")
            return

        # Pre-flight check: ensure scraper service is actually alive
        if not check_scraper_health():
            self.client.send_message(
                chat_id,
                "❌ Scraper service is unreachable. Please verify that the local Docker container is running."
            )
            return

        params = {
            "keyword": keyword,
            "city": city,
            "depth": depth,
            "email": True,
            "socials": True,
            "exclude_seen": True,
            "max_time": 600,
            "source": "telegram",
            "telegram_user_id": str(user_id),
            "telegram_chat_id": str(chat_id),
            "telegram_update_id": str(update_id) if update_id is not None else None
        }

        self.client.send_message(
            chat_id,
            f"🚀 Initiating scrape:\n"
            f"• Keyword: {keyword}\n"
            f"• City: {city or '(auto-detect)'}\n"
            f"• Depth: {depth}\n"
            f"Resolving coordinates and contacting scraper service..."
        )

        try:
            success, job_id, resolved = initiate_scrape_job(params)
        except Exception as e:
            self.client.send_message(chat_id, f"❌ Failed to initiate job: {sanitize_error_message(e)}")
            return

        if not success:
            self.client.send_message(chat_id, f"❌ Failed to start scrape: {sanitize_error_message(Exception(job_id))}")
            return

        set_user_active_job(chat_id, user_id, job_id, f"{keyword} in {city}".strip())
        self.client.send_message(
            chat_id,
            f"✅ Job started successfully!\n"
            f"• Job ID: {job_id}\n"
            f"• Query: {resolved.get('full_query')}\n"
            f"Send /status {job_id} to monitor progress."
        )

        # Spawn background processing worker
        def background_worker():
            try:
                res = poll_and_process_job(job_id, resolved)
                status = res.get("status")
                if status == "ok":
                    metrics = res.get("metrics", {})
                    total = metrics.get("total", 0)
                    hot = metrics.get("hot", 0)
                    warm = metrics.get("warm", 0)
                    cold = metrics.get("cold", 0)
                    emails = metrics.get("with_email", 0)
                    phones = metrics.get("with_phone", 0)
                    synced = metrics.get("supabase_synced", 0)

                    msg = (
                        f"🎉 Scrape Completed!\n"
                        f"• Job ID: {job_id}\n"
                        f"• Total Qualified Leads: {total}\n"
                        f"• Quality: {hot} HOT 🔥 | {warm} WARM ⚡ | {cold} COLD ❄️\n"
                        f"• Contacts: {emails} with Email ✉️ | {phones} with Phone 📞\n"
                    )
                    if synced:
                        msg += f"• Supabase Sync: {synced} leads saved to cloud ☁️\n"
                    msg += f"\nSend /export {job_id} to download your CSV leads!"
                    self.client.send_message(chat_id, msg)
                elif status == "cancelled":
                    self.client.send_message(chat_id, f"⏹️ Job {job_id} was cancelled by user.")
                else:
                    err_msg = res.get("error", "Processing error")
                    self.client.send_message(chat_id, f"❌ Job {job_id} failed: {sanitize_error_message(Exception(err_msg))}")
            except Exception as ex:
                self.client.send_message(chat_id, f"❌ Job {job_id} worker error: {sanitize_error_message(ex)}")

        t = threading.Thread(target=background_worker, daemon=True)
        t.start()

    def _resolve_job_id(self, chat_id: int, tokens: list) -> str:
        """Resolve job_id from command arguments or fallback to last known chat job."""
        if len(tokens) >= 2 and validate_job_id(tokens[1]):
            return tokens[1].strip()
        return get_user_last_job(chat_id)

    def _handle_status(self, chat_id: int, tokens: list):
        if len(tokens) < 2:
            last_job = get_user_last_job(chat_id)
            if not last_job:
                self.client.send_message(chat_id, "⚠️ Please specify a job ID. Example: /status <JOB_ID>")
                return
            job_id = last_job
        else:
            job_id = tokens[1].strip()

        if not validate_job_id(job_id):
            self.client.send_message(chat_id, f"⚠️ Invalid Job ID format: '{job_id}'.")
            return

        info = get_job_info(job_id)
        if not info:
            self.client.send_message(chat_id, f"⚠️ Job '{job_id}' not found.")
            return

        status = info.get("status", "unknown")
        pct = info.get("progress_percent", 0)
        stage = info.get("stage", "Processing")
        msg = (
            f"📊 Job Status: {job_id}\n"
            f"• Status: {status.upper()}\n"
            f"• Progress: {pct}%\n"
            f"• Current Stage: {stage}\n"
        )
        if status == "ok":
            msg += f"\nSend /leads {job_id} or /export {job_id} to inspect results."
        self.client.send_message(chat_id, msg)

    def _handle_recent(self, chat_id: int):
        # 1. Try fetching from Supabase scrape_jobs if configured
        recent_jobs = []
        if is_supabase_configured():
            try:
                recent_jobs = fetch_recent_scrape_jobs_from_supabase(limit=5)
            except Exception:
                pass

        if recent_jobs:
            msg = "📜 Recent Scrape Jobs (Supabase):\n\n"
            for idx, j in enumerate(recent_jobs[:5], 1):
                jid = j.get("scraper_job_id") or j.get("id", "")
                q = j.get("query") or "Search"
                status = j.get("status", "ok")
                cnt = j.get("total_leads", 0)
                msg += f"{idx}. {q}\n   ID: {jid}\n   Status: {status.upper()} | Leads: {cnt}\n\n"
            msg += "Use /status <JOB_ID> or /export <JOB_ID> to inspect any job."
            self.client.send_message(chat_id, msg)
            return

        # 2. Fallback to local files
        files = []
        if os.path.isdir(DATA_DIR):
            for fname in os.listdir(DATA_DIR):
                if fname.startswith("leads_") and fname.endswith(".json"):
                    fpath = os.path.join(DATA_DIR, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                        files.append((mtime, fpath, fname))
                    except OSError:
                        pass
        files.sort(reverse=True)
        if not files:
            self.client.send_message(chat_id, "ℹ️ No scrape history found.")
            return

        msg = "📜 Recent Scrape Jobs (Local):\n\n"
        for idx, (mtime, fpath, fname) in enumerate(files[:5], 1):
            try:
                with open(fpath) as f:
                    d = json.load(f)
                    jid = d.get("job_id") or fname.replace("leads_", "").replace(".json", "")
                    params = d.get("params") or {}
                    q = params.get("full_query") or params.get("keyword") or "Search"
                    cnt = len(d.get("leads", []))
                    timestr = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
                    msg += f"{idx}. {q}\n   ID: {jid}\n   Date: {timestr} | Leads: {cnt}\n\n"
            except Exception:
                pass
        msg += "Use /status <JOB_ID> or /export <JOB_ID> to inspect any job."
        self.client.send_message(chat_id, msg)

    def _handle_cancel(self, chat_id: int, tokens: list):
        if len(tokens) < 2:
            self.client.send_message(chat_id, "⚠️ Please specify the Job ID to cancel: /cancel <JOB_ID>")
            return

        job_id = tokens[1].strip()
        if not validate_job_id(job_id):
            self.client.send_message(chat_id, f"⚠️ Invalid Job ID format: '{job_id}'.")
            return

        stopped = cancel_job(job_id)
        if stopped:
            if is_supabase_configured():
                try:
                    update_scrape_job_record(job_id, {"status": "cancelled", "stage": "Cancelled by user."})
                except Exception:
                    pass
            self.client.send_message(chat_id, f"⏹️ Cancellation requested for job {job_id}.")
        else:
            self.client.send_message(chat_id, f"⚠️ Could not cancel job {job_id}. Is it already completed or inactive?")

    def _handle_leads(self, chat_id: int, tokens: list):
        job_id = self._resolve_job_id(chat_id, tokens)
        if not job_id:
            self.client.send_message(chat_id, "⚠️ No job ID specified and no recent job found. Example: /leads <JOB_ID>")
            return

        info = get_job_info(job_id)
        leads = info.get("leads", [])
        if not leads:
            self.client.send_message(chat_id, f"⚠️ No leads found for job '{job_id}'. Is it still processing?")
            return

        metrics = info.get("metrics") or {}
        hot = metrics.get("hot") or sum(1 for l in leads if l.get("lead_tier") == "HOT")
        warm = metrics.get("warm") or sum(1 for l in leads if l.get("lead_tier") == "WARM")
        cold = metrics.get("cold") or sum(1 for l in leads if l.get("lead_tier") == "COLD")
        emails = metrics.get("with_email") or sum(1 for l in leads if l.get("emails"))
        phones = metrics.get("with_phone") or sum(1 for l in leads if (l.get("clean_phone") or l.get("phone")))

        msg = (
            f"📋 Lead Intelligence Summary ({job_id})\n"
            f"• Total Leads: {len(leads)}\n"
            f"• Quality: {hot} HOT 🔥 | {warm} WARM ⚡ | {cold} COLD ❄️\n"
            f"• Contact Channels: {emails} Emails | {phones} Phone Numbers\n\n"
            f"Top Samples:\n"
        )
        for i, l in enumerate(leads[:3], 1):
            tier = l.get("lead_tier", "COLD")
            score = l.get("lead_score", 0)
            title = l.get("title", "Unknown")
            phone = l.get("clean_phone") or l.get("phone") or "—"
            email = l.get("emails") or "—"
            msg += f"{i}. [{tier} {score}] {title}\n   📞 {phone} | ✉️ {email}\n"

        msg += f"\nSend /export {job_id} to download the full lead file."
        self.client.send_message(chat_id, msg)

    def _handle_export(self, chat_id: int, tokens: list):
        job_id = ""
        fmt = "csv"

        if len(tokens) >= 2:
            if validate_job_id(tokens[1]):
                job_id = tokens[1]
                if len(tokens) >= 3:
                    fmt = tokens[2].lower()
            elif tokens[1].lower() in ("csv", "crm", "json", "outreach"):
                fmt = tokens[1].lower()

        if not job_id:
            job_id = get_user_last_job(chat_id)

        if not job_id:
            self.client.send_message(chat_id, "⚠️ No job ID specified and no recent scrape found. Example: /export <JOB_ID>")
            return

        info = get_job_info(job_id)
        leads = info.get("leads", [])
        if not leads:
            self.client.send_message(chat_id, f"⚠️ No leads found to export for job '{job_id}'.")
            return

        content_bytes, suffix, mime_type = export_leads(leads, fmt=fmt)
        filename = f"leads_{job_id[:8]}{suffix}"

        self.client.send_message(chat_id, f"📦 Generating {filename} ({len(leads)} leads)...")
        res = self.client.send_document(
            chat_id=chat_id,
            file_bytes=content_bytes,
            filename=filename,
            caption=f"🗺️ LeadMap Pro Export ({len(leads)} leads) • Format: {fmt.upper()}"
        )
        if not res.get("ok"):
            err = res.get("description", "Failed to upload file")
            self.client.send_message(chat_id, f"❌ File delivery error: {sanitize_error_message(Exception(err))}")


# ── Long-Polling Run Loop ─────────────────────────────────────────────────────

def run_bot_polling(stop_event: threading.Event = None):
    """Run Telegram Bot in continuous long-polling mode."""
    token = get_bot_token()
    if not token:
        print("❌ Error: TELEGRAM_BOT_TOKEN is not configured in environment.", file=sys.stderr)
        return

    allowed = get_allowed_user_ids()
    if not allowed:
        print("⚠️ Security Notice: TELEGRAM_ALLOWED_USER_IDS is empty. All incoming messages will be rejected.", file=sys.stderr)
    else:
        print(f"🔒 Bot authorized strictly for Telegram numeric user IDs: {allowed}")

    client = TelegramClient(token)
    me = client.get_me()
    if not me.get("ok"):
        print(f"❌ Failed to connect to Telegram Bot API: {me.get('description')}", file=sys.stderr)
        return

    bot_info = me.get("result", {})
    username = bot_info.get("username", "unknown")
    print(f"🤖 Connected as @{username} (ID: {bot_info.get('id')})")
    print("▶ Starting long-polling update loop (Ctrl+C to stop)...")

    handler = TelegramBotHandler(client)
    offset = None

    while not (stop_event and stop_event.is_set()):
        try:
            updates = client.get_updates(offset=offset, timeout=20)
            for u in updates:
                update_id = u.get("update_id")
                if update_id is not None:
                    offset = update_id + 1
                try:
                    handler.handle_update(u)
                except Exception as ex:
                    print(f"[ERROR handling update] {sanitize_error_message(ex)}", file=sys.stderr)
        except Exception as e:
            print(f"[Polling loop error] {sanitize_error_message(e)}", file=sys.stderr)
            time.sleep(3)


if __name__ == "__main__":
    run_bot_polling()

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


def set_pending_clarification(chat_id: int, user_id: int, pending: dict):
    """Store conversational query clarification state for multi-turn Telegram dialog."""
    state = _load_state()
    chat_key = str(chat_id)
    chat_info = state["chats"].setdefault(chat_key, {})
    chat_info["pending_clarification"] = pending
    chat_info["pending_clarification_at"] = time.time()
    chat_info["user_id"] = user_id
    _save_state(state)


def get_pending_clarification(chat_id: int, max_age_seconds: int = 300) -> dict:
    """Retrieve active clarification state if within TTL (default: 5 minutes)."""
    state = _load_state()
    chat_info = state.get("chats", {}).get(str(chat_id), {})
    pending = chat_info.get("pending_clarification")
    ts = chat_info.get("pending_clarification_at", 0)
    if pending and (time.time() - ts <= max_age_seconds):
        return pending
    return None


def clear_pending_clarification(chat_id: int):
    """Clear active clarification state for a chat."""
    state = _load_state()
    chat_info = state.get("chats", {}).get(str(chat_id), {})
    if "pending_clarification" in chat_info or "pending_clarification_at" in chat_info:
        chat_info.pop("pending_clarification", None)
        chat_info.pop("pending_clarification_at", None)
        _save_state(state)


# Note on multi-instance concurrency:
# Local file persistence with atomic rename (fsync + os.replace) ensures crash recovery,
# safe restart loading, and single-process in-flight deduplication.
# If LeadMap Pro bot is deployed across multiple concurrent bot processes,
# cross-process atomic reservations must be backed by database-level unique constraints
# (such as PostgreSQL / Supabase idx_scrape_jobs_telegram_update).

IN_FLIGHT_UPDATES = set()
IN_FLIGHT_LOCK = threading.Lock()


def _load_processed_updates() -> set:
    """Load processed update_ids set from disk safely handling missing or corrupted state."""
    with STATE_LOCK:
        if os.path.isfile(PROCESSED_UPDATES_FILE):
            try:
                with open(PROCESSED_UPDATES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "processed_ids" in data:
                        return set(data["processed_ids"])
                    elif isinstance(data, list):
                        return set(data)
            except Exception as e:
                print(f"[Warning] Corrupted or unreadable processed updates file: {e}", file=sys.stderr)
        return set()


def _save_processed_updates(update_ids: set):
    """Save processed update_ids set to disk immediately and atomically via fsync + os.replace."""
    with STATE_LOCK:
        id_list = list(update_ids)[-MAX_PROCESSED_UPDATES:]
        tmp_path = PROCESSED_UPDATES_FILE + f".tmp_{os.getpid()}_{time.time_ns()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"processed_ids": id_list}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, PROCESSED_UPDATES_FILE)
        except Exception as e:
            print(f"[Warning] Failed to persist processed updates: {e}", file=sys.stderr)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


PROCESSED_UPDATES_CACHE = _load_processed_updates()


def is_update_duplicate(update_id: int) -> bool:
    """Check if update_id has already been processed or is currently in-flight."""
    if update_id is None:
        return False
    with IN_FLIGHT_LOCK:
        return (update_id in PROCESSED_UPDATES_CACHE) or (update_id in IN_FLIGHT_UPDATES)


def begin_update_processing(update_id: int) -> bool:
    """Register update_id as in-flight within this process.
    
    Returns True if allowed to proceed, or False if duplicate/already in-flight.
    """
    if update_id is None:
        return True
    with IN_FLIGHT_LOCK:
        if (update_id in PROCESSED_UPDATES_CACHE) or (update_id in IN_FLIGHT_UPDATES):
            return False
        IN_FLIGHT_UPDATES.add(update_id)
        return True


def finish_update_processing(update_id: int):
    """Discharge update_id from in-flight and persist immediately to disk."""
    if update_id is None:
        return
    with IN_FLIGHT_LOCK:
        IN_FLIGHT_UPDATES.discard(update_id)
        PROCESSED_UPDATES_CACHE.add(update_id)
        _save_processed_updates(PROCESSED_UPDATES_CACHE)


def mark_update_processed(update_id: int):
    """Record update_id as processed and persist immediately."""
    if update_id is None:
        return
    with IN_FLIGHT_LOCK:
        PROCESSED_UPDATES_CACHE.add(update_id)
        _save_processed_updates(PROCESSED_UPDATES_CACHE)


# ── Telegram HTTP API Client ──────────────────────────────────────────────────

class TelegramClient:
    """Minimal zero-dependency Telegram API HTTP client."""

    def __init__(self, token: str, api_base: str = TELEGRAM_API_BASE):
        self.token = token
        self.base_url = f"{api_base}/bot{token}"

    def get_me(self, retries: int = 3) -> dict:
        """Call getMe endpoint to verify token with auto-retry on transient connection drops."""
        url = f"{self.base_url}/getMe"
        req = urllib.request.Request(url, headers={"User-Agent": "LeadMap-Pro-Bot/1.0"})
        last_err = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                return {"ok": False, "description": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}"}
            except (ConnectionResetError, urllib.error.URLError, OSError) as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(1.5)
                    continue
                return {"ok": False, "description": sanitize_error_message(e)}
            except Exception as e:
                return {"ok": False, "description": sanitize_error_message(e)}
        return {"ok": False, "description": sanitize_error_message(last_err or Exception("Connection failed"))}

    def get_updates(self, offset: int = None, timeout: int = 20) -> list:
        """Poll for new updates using long-polling."""
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        q = urllib.parse.urlencode(params)
        url = f"{self.base_url}/getUpdates?{q}"
        req = urllib.request.Request(url, headers={"User-Agent": "LeadMap-Pro-Bot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("ok"):
                    return data.get("result", [])
                return []
        except (TimeoutError, urllib.error.URLError, OSError):
            return []
        except Exception as e:
            print(f"[get_updates warning] {sanitize_error_message(e)}", file=sys.stderr)
            return []

    def send_message(self, chat_id: int, text: str, reply_to_message_id: int = None) -> dict:
        """Send a plain text message to a chat."""
        url = f"{self.base_url}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "LeadMap-Pro-Bot/1.0"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            try:
                return json.loads(err_body)
            except Exception:
                return {"ok": False, "description": sanitize_error_message(Exception(err_body))}
        except Exception as e:
            return {"ok": False, "description": sanitize_error_message(e)}

    def send_document(self, chat_id: int, file_bytes: bytes, filename: str, caption: str = "") -> dict:
        """Send a document using standard-library multipart/form-data encoding."""
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
    depth_match = re.search(r"--depth\s+(\d+)", raw)
    if depth_match:
        try:
            depth = max(MIN_DEPTH, min(MAX_DEPTH, int(depth_match.group(1))))
        except ValueError:
            pass
        raw = re.sub(r"--depth\s+\d+", "", raw).strip()

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


def _validate_and_build_nlp(kw: str, loc: str, count: int) -> dict:
    """Helper to validate extracted NLP fields and assemble result dict."""
    kw = (kw or "").strip().strip('"').strip("'")
    loc = (loc or "").strip().strip('"').strip("'")

    if kw and kw.isdigit():
        count = int(kw)
        kw = None

    count = max(MIN_DEPTH, min(MAX_DEPTH, count))

    if kw and kw.lower().startswith("for "):
        kw = kw[4:].strip()

    if not kw:
        return {
            "status": "needs_clarification",
            "missing_field": "keyword",
            "keyword": None,
            "city": loc,
            "count": count,
            "prompt": f"What business category are you looking for in {loc}?"
        }
    if not loc:
        return {
            "status": "needs_clarification",
            "missing_field": "city",
            "keyword": kw,
            "city": None,
            "count": count,
            "prompt": f"Which city or location should I search for '{kw}' in?"
        }

    if len(kw) > MAX_KEYWORD_LENGTH:
        return {"status": "error", "error": f"Keyword too long (maximum {MAX_KEYWORD_LENGTH} characters)."}
    if len(loc) > MAX_LOCATION_LENGTH:
        return {"status": "error", "error": f"Location too long (maximum {MAX_LOCATION_LENGTH} characters)."}

    return {
        "status": "complete",
        "keyword": kw,
        "city": loc,
        "count": count,
        "missing_field": None,
        "prompt": None,
        "error": None
    }


def parse_natural_language_query(text: str) -> dict:
    """Parse natural language query to extract target count, business category, and location.

    Supports queries such as:
    - "10 IT companies in Pune"
    - "Find 50 dentists in Mumbai"
    - "20 digital marketing agencies in Bangalore"
    - "cafes in Delhi" (defaults to 5)
    - "dentists in Mumbai --depth 20"
    - "find dentists" (flags missing location for interactive clarification)
    - "50 in Pune" (flags missing category for interactive clarification)

    Returns dict with keys:
    - status: 'complete', 'needs_clarification', 'unrecognized', or 'error'
    - keyword: str or None
    - city: str or None
    - count: int
    - missing_field: 'city', 'keyword', or None
    - prompt: str or None
    - error: str or None
    """
    raw = (text or "").strip()
    if not raw:
        return {"status": "unrecognized", "error": "Empty message"}

    # Strip conversational greetings/prefixes
    cleaned = re.sub(r'^(?:hi|hello|hey|please|can you|could you)\s+', '', raw, flags=re.IGNORECASE).strip()

    depth = None
    depth_match = re.search(r'--depth\s+(\d+)', cleaned)
    if depth_match:
        try:
            depth = max(MIN_DEPTH, min(MAX_DEPTH, int(depth_match.group(1))))
        except ValueError:
            pass
        cleaned = re.sub(r'--depth\s+\d+', '', cleaned).strip()

    # Pattern 1: Action verb + optional count + category + "in" + location
    m1 = re.match(
        r'^(?:find|search|scrape|get|collect|fetch|extract)\s+(?:for\s+)?(?:(\d+)\s+)?(.+?)\s+in\s+([^,]+(?:,\s*[A-Za-z\s]+)?)$',
        cleaned,
        re.IGNORECASE
    )
    if m1:
        c_str, kw, loc = m1.groups()
        if depth is None and c_str:
            depth = int(c_str)
        return _validate_and_build_nlp(kw, loc, depth or DEFAULT_DEPTH)

    # Pattern 2: Count first + category + "in" + location
    m2 = re.match(r'^(\d+)\s+(.+?)\s+in\s+(.+)$', cleaned, re.IGNORECASE)
    if m2:
        c_str, kw, loc = m2.groups()
        if depth is None and c_str:
            depth = int(c_str)
        return _validate_and_build_nlp(kw, loc, depth or DEFAULT_DEPTH)

    # Pattern 3: Category + "in" + location + optional count at end
    m3 = re.match(r'^(.+?)\s+in\s+(.+?)(?:\s+(\d+))?$', cleaned, re.IGNORECASE)
    if m3:
        kw, loc, c_str = m3.groups()
        if depth is None and c_str:
            depth = int(c_str)
        return _validate_and_build_nlp(kw, loc, depth or DEFAULT_DEPTH)

    # Pattern 4: Missing location (e.g. "Find 50 dentists", "10 IT companies", "scrape dentists")
    m_no_loc = re.match(
        r'^(?:(?:find|search|scrape|get|collect|fetch|extract)\s+(?:for\s+)?)?(?:(\d+)\s+)?([a-zA-Z0-9\s\-_&/]+)$',
        cleaned,
        re.IGNORECASE
    )
    if m_no_loc:
        c_str, kw = m_no_loc.groups()
        kw = kw.strip()
        if kw.lower() not in ("start", "help", "recent", "history", "cancel", "stop", "leads", "export", "status", "whatsapp"):
            if depth is None and c_str:
                depth = int(c_str)
            return {
                "status": "needs_clarification",
                "missing_field": "city",
                "keyword": kw,
                "city": None,
                "count": max(MIN_DEPTH, min(MAX_DEPTH, depth or DEFAULT_DEPTH)),
                "prompt": f"Which city or location would you like to search for '{kw}' in?"
            }

    # Pattern 5: Missing keyword (e.g. "50 in Pune", "in Mumbai")
    m_no_kw = re.match(r'^(?:(\d+)\s+)?in\s+(.+)$', cleaned, re.IGNORECASE)
    if m_no_kw:
        c_str, loc = m_no_kw.groups()
        loc = loc.strip()
        if depth is None and c_str:
            depth = int(c_str)
        return {
            "status": "needs_clarification",
            "missing_field": "keyword",
            "keyword": None,
            "city": loc,
            "count": max(MIN_DEPTH, min(MAX_DEPTH, depth or DEFAULT_DEPTH)),
            "prompt": f"What business category or service are you looking for in {loc}?"
        }

    return {"status": "unrecognized", "error": "Could not recognize search query format."}



# ── Telegram Handler Dispatcher ───────────────────────────────────────────────

class TelegramBotHandler:
    """Handles dispatching, validating, and executing Telegram bot commands."""

    def __init__(self, client: TelegramClient):
        self.client = client

    def handle_update(self, update: dict):
        """Process a single Telegram update message safely."""
        update_id = update.get("update_id")
        if update_id is not None:
            if not begin_update_processing(update_id):
                print(f"[IDEMPOTENT] Duplicate update_id {update_id} discarded.", file=sys.stderr)
                return

        try:
            message = update.get("message")
            if not message:
                return

            chat = message.get("chat", {})
            chat_id = chat.get("id")
            chat_type = chat.get("type", "private")
            user = message.get("from", {})
            user_id = user.get("id")
            text = (message.get("text") or "").strip()

            if not chat_id or user_id is None or not text:
                return

            # 1. Authorization Guard (numeric ID enforcement)
            if not is_user_authorized(user_id):
                print(f"[AUTH DENIED] Unauthorized user ID {user_id} attempted access.", file=sys.stderr)
                self.client.send_message(
                    chat_id,
                    f"⛔ Access Denied\nYour Telegram User ID ({user_id}) is not authorized to use LeadMap Pro.\n"
                    "Please configure TELEGRAM_ALLOWED_USER_IDS in .env."
                )
                return

            # 2. Private-Chat-Only Enforcement (reject groups and supergroups)
            if chat_type != "private":
                print(f"[CHAT TYPE REJECTED] Non-private chat {chat_id} (type: {chat_type}) rejected.", file=sys.stderr)
                self.client.send_message(
                    chat_id,
                    "🔒 Access Restricted: LeadMap Pro commands can only be executed in private 1-on-1 chats to safeguard proprietary business leads and data."
                )
                return

            # 3. Command & Message Dispatch
            tokens = text.split()
            cmd = tokens[0].lower()
            if "@" in cmd:
                cmd = cmd.split("@")[0]

            if cmd.startswith("/"):
                # Clear any pending clarification when an explicit slash command is run
                clear_pending_clarification(chat_id)
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
                elif cmd in ("/whatsapp", "/wa"):
                    target_job = tokens[1] if len(tokens) > 1 and validate_job_id(tokens[1]) else ""
                    self._handle_export(chat_id, ["/export", target_job, "whatsapp"])
                else:
                    self.client.send_message(
                        chat_id,
                        f"Unknown command: '{cmd}'. Send /help to view available commands or ask in natural language."
                    )
            else:
                self._handle_natural_language_or_clarification(chat_id, user_id, update_id, text)
        except Exception as e:
            print(f"[Telegram Handler Error] {sanitize_error_message(e)}", file=sys.stderr)
        finally:
            if update_id is not None:
                finish_update_processing(update_id)

    def _handle_help(self, chat_id: int):
        help_text = (
            "🗺️ LeadMap Pro — Google Maps Lead Intelligence Bot\n\n"
            "💬 Natural Language Search:\n"
            "Send your request naturally! Examples:\n"
            "• 10 IT companies in Pune\n"
            "• Find 50 dentists in Mumbai\n"
            "• 20 digital marketing agencies in Bangalore\n"
            "• cafes in Delhi\n\n"
            "Commands:\n"
            "• /scrape <query> — Start a new scraping job manually\n"
            "• /status <JOB_ID> — Check status and progress of a scrape job\n"
            "• /recent — View the 5 most recent scrape jobs\n"
            "• /cancel <JOB_ID> — Safely cancel a running scrape job\n"
            "• /leads [JOB_ID] — View lead metrics (HOT 🔥, WARM ⚡, COLD ❄️, emails, phones)\n"
            "• /export [JOB_ID] [csv|crm|json] — Download sanitized lead file directly in chat\n"
            "• /whatsapp [JOB_ID] — Download WhatsApp portal ready outreach CSV\n"
            "• /help — Show this help message"
        )
        self.client.send_message(chat_id, help_text)

    def _handle_natural_language_or_clarification(self, chat_id: int, user_id: int, update_id: int, text: str):
        """Handle conversational clarifications or natural-language lead queries."""
        # Check if user has an active pending clarification
        pending = get_pending_clarification(chat_id)
        if pending:
            missing = pending.get("missing_field")
            count = pending.get("count", DEFAULT_DEPTH)
            if missing == "city":
                provided_city = text.strip()
                if provided_city.lower().startswith("in "):
                    provided_city = provided_city[3:].strip()
                keyword = pending.get("keyword")
                clear_pending_clarification(chat_id)
                self._start_scrape_workflow(chat_id, user_id, update_id, keyword, provided_city, count)
                return
            elif missing == "keyword":
                provided_kw = text.strip()
                city = pending.get("city")
                clear_pending_clarification(chat_id)
                self._start_scrape_workflow(chat_id, user_id, update_id, provided_kw, city, count)
                return

        # No pending clarification; parse as a fresh natural-language query
        parsed = parse_natural_language_query(text)
        status = parsed.get("status")

        if status == "complete":
            kw = parsed.get("keyword")
            city = parsed.get("city")
            count = parsed.get("count", DEFAULT_DEPTH)
            self._start_scrape_workflow(chat_id, user_id, update_id, kw, city, count)
        elif status == "needs_clarification":
            set_pending_clarification(chat_id, user_id, parsed)
            prompt = parsed.get("prompt") or "Could you please provide more details for this search?"
            self.client.send_message(chat_id, f"📍 {prompt}")
        elif status == "error":
            self.client.send_message(chat_id, f"⚠️ {parsed.get('error')}")
        else:
            self.client.send_message(
                chat_id,
                "🤖 I didn't recognize that search request.\n\n"
                "You can ask me naturally, for example:\n"
                "• 10 IT companies in Pune\n"
                "• Find 50 dentists in Mumbai\n"
                "• 20 digital marketing agencies in Bangalore\n\n"
                "Or send /help to view all available commands."
            )

    def _handle_scrape(self, chat_id: int, user_id: int, update_id: int, text: str):
        keyword, city, depth, err = parse_scrape_command(text)
        if err or not keyword:
            self.client.send_message(chat_id, f"⚠️ {err or 'Missing search query.'}")
            return
        self._start_scrape_workflow(chat_id, user_id, update_id, keyword, city, depth)

    def _start_scrape_workflow(self, chat_id: int, user_id: int, update_id: int, keyword: str, city: str, depth: int):
        """Execute the scrape lifecycle and report verified database receipts."""
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
            f"• Query: {keyword}\n"
            f"• Location: {city or '(auto-detect)'}\n"
            f"• Target Count: {depth}\n"
            f"Resolving coordinates and contacting scraper service..."
        )

        try:
            success, job_id, resolved = initiate_scrape_job(params)
        except Exception as e:
            self.client.send_message(chat_id, f"❌ Failed to initiate job: {sanitize_error_message(e)}")
            return

        if not success:
            if resolved.get("duplicate"):
                existing_id = resolved.get("job_id", "")
                self.client.send_message(
                    chat_id,
                    f"ℹ️ Duplicate update: this job was already reserved or started (Job ID: {existing_id})."
                )
                return
            elif resolved.get("db_error"):
                self.client.send_message(
                    chat_id,
                    f"❌ Database error reserving job: {sanitize_error_message(Exception(job_id))}"
                )
                return
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

        def background_worker():
            try:
                res = poll_and_process_job(job_id, resolved)
                status = res.get("status")
                if status == "ok":
                    metrics = res.get("metrics", {})
                    raw_found = metrics.get("raw_extracted", 0)
                    total_unique = metrics.get("total", 0)
                    skipped_dup = metrics.get("skipped_previous", 0)
                    hot = metrics.get("hot", 0)
                    warm = metrics.get("warm", 0)
                    cold = metrics.get("cold", 0)
                    emails = metrics.get("with_email", 0)
                    phones = metrics.get("with_phone", 0)
                    synced = metrics.get("supabase_synced", 0)

                    # Verified receipt reporting
                    db_inserts = synced if synced else total_unique
                    persist_status = "Local Disk ✓" + (f" | Supabase ({synced} synced) ☁️" if synced else " | Local DB only")

                    msg = (
                        f"🎉 Scrape Completed!\n"
                        f"• Job ID: {job_id}\n"
                        f"• Actual Results Found: {raw_found}\n"
                        f"• Unique Records: {total_unique}\n"
                        f"• New Database Inserts: {db_inserts}\n"
                        f"• Duplicates Skipped: {skipped_dup}\n"
                        f"• Quality: {hot} HOT 🔥 | {warm} WARM ⚡ | {cold} COLD ❄️\n"
                        f"• Contacts: {emails} Email ✉️ | {phones} Phone 📞\n"
                        f"• Persistence: {persist_status}\n"
                    )
                    msg += f"\nSend /export {job_id} or /whatsapp {job_id} to download your leads!"
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

        # Report actual states and clearly label any intermediate progress as estimated
        if status in ("working", "running", "pending") and pct < 100:
            progress_display = f"~{pct}% (Estimated)"
        elif status == "ok" or pct >= 100:
            progress_display = "100% (Complete)"
        else:
            progress_display = f"{pct}%"

        msg = (
            f"📊 Job Status: {job_id}\n"
            f"• Status: {status.upper()}\n"
            f"• Progress: {progress_display}\n"
            f"• Current Stage: {stage}\n"
        )
        if status == "ok":
            msg += f"\nSend /leads {job_id} or /export {job_id} to inspect results."
        self.client.send_message(chat_id, msg)

    def _handle_recent(self, chat_id: int):
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
            elif tokens[1].lower() in ("csv", "crm", "json", "outreach", "whatsapp", "wa"):
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
    raw_allowed = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    if not allowed:
        if raw_allowed:
            print(f"⚠️ Security Notice: TELEGRAM_ALLOWED_USER_IDS='{raw_allowed}' contains no numeric user IDs.", file=sys.stderr)
            print("   Note: This must be your personal numeric Telegram user ID (from @userinfobot), NOT the bot username.", file=sys.stderr)
            print("   All incoming messages will be rejected until a numeric ID is configured in .env.", file=sys.stderr)
        else:
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

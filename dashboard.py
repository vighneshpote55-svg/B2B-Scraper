#!/usr/bin/env python3
"""Interactive Web Dashboard Server for Google Maps Scraper Kit.

Zero-dependency local server (uses Python standard library).
Provides:
- Web dashboard serving (HTML5, Vanilla CSS, modern JS).
- Proxy & control of local gmaps-scraper container (Docker).
- Geocoding, live job tracking, automatic lead cleansing & scoring.
- Multi-format exports (Standard CSV, CRM / Cold Email Outreach CSV, JSON).
"""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# Load .env
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

SCRAPER_BASE = os.environ.get("SCRAPER_BASE_URL", "http://localhost:8085")
SCRAPER_KEY = os.environ.get("SCRAPER_API_KEY", "")
HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("DASHBOARD_PORT", "3000"))
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
PASSWORD_RESETS_FILE = os.path.join(DATA_DIR, "password_resets.json")
EMAIL_OUTBOX_FILE = os.path.join(DATA_DIR, "email_outbox.log")
SESSION_TTL = int(os.environ.get("SESSION_TTL_SECONDS", 7 * 86400))  # 7 days session lifetime
RESET_TOKEN_TTL = int(os.environ.get("RESET_TOKEN_TTL_SECONDS", 900))  # 15 minutes reset token lifetime
MAX_REQUEST_BODY_SIZE = int(os.environ.get("MAX_REQUEST_BODY_SIZE", 10 * 1024 * 1024))  # 10 MB payload limit
MAX_FAILED_LOGIN_ATTEMPTS = int(os.environ.get("MAX_FAILED_LOGIN_ATTEMPTS", 5))  # per account
MAX_FAILED_LOGIN_PER_IP = int(os.environ.get("MAX_FAILED_LOGIN_PER_IP", 50))  # per IP
LOGIN_LOCKOUT_WINDOW = int(os.environ.get("LOGIN_LOCKOUT_WINDOW_SECONDS", 300))  # 5 minutes
LOGIN_ATTEMPTS = {}
LOGIN_ATTEMPTS_LOCK = threading.Lock()
RESETS_LOCK = threading.Lock()
os.makedirs(DATA_DIR, exist_ok=True)

DEFAULT_ALLOWED_ORIGINS = {
    f"http://localhost:{PORT}",
    f"http://127.0.0.1:{PORT}",
    "http://localhost",
    "http://127.0.0.1",
}
_custom_origins = os.environ.get("ALLOWED_ORIGINS", "")
if _custom_origins:
    ALLOWED_ORIGINS = DEFAULT_ALLOWED_ORIGINS.union({o.strip() for o in _custom_origins.split(",") if o.strip()})
else:
    ALLOWED_ORIGINS = DEFAULT_ALLOWED_ORIGINS

JOB_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

def _validate_job_id(job_id: str) -> bool:
    """Validate that job_id is alphanumeric with underscores/hyphens and max 64 chars."""
    if not job_id or not isinstance(job_id, str):
        return False
    return bool(JOB_ID_REGEX.match(job_id.strip()))

def _get_job_file_path(job_id: str):
    """Safely resolve job JSON file path, preventing directory traversal."""
    if not _validate_job_id(job_id):
        return None
    data_dir_abs = os.path.abspath(DATA_DIR)
    safe_path = os.path.abspath(os.path.join(data_dir_abs, f"leads_{job_id.strip()}.json"))
    if not safe_path.startswith(data_dir_abs + os.sep):
        return None
    return safe_path

USER_LOCK = threading.Lock()

def _atomic_json_write(filepath, data):
    """Safely write JSON data to disk using temporary file and atomic rename."""
    dir_name = os.path.dirname(filepath)
    os.makedirs(dir_name, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_", suffix=".json", text=True)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise

def _check_login_rate_limit(client_ip, email):
    """Check if client IP or email has exceeded max failed login attempts."""
    now = time.time()
    with LOGIN_ATTEMPTS_LOCK:
        # Check email/account level lockout (prevent brute forcing victim account)
        if email:
            email_key = f"email:{email.lower()}"
            record = LOGIN_ATTEMPTS.get(email_key)
            if record:
                attempts = [t for t in record if now - t < LOGIN_LOCKOUT_WINDOW]
                LOGIN_ATTEMPTS[email_key] = attempts
                if len(attempts) >= MAX_FAILED_LOGIN_ATTEMPTS:
                    oldest = min(attempts)
                    retry_after = int(LOGIN_LOCKOUT_WINDOW - (now - oldest)) + 1
                    return True, max(1, retry_after)

        # Check global IP level lockout (prevent distributed credential stuffing from single IP)
        if client_ip:
            ip_key = f"ip:{client_ip}"
            record = LOGIN_ATTEMPTS.get(ip_key)
            if record:
                attempts = [t for t in record if now - t < LOGIN_LOCKOUT_WINDOW]
                LOGIN_ATTEMPTS[ip_key] = attempts
                if len(attempts) >= MAX_FAILED_LOGIN_PER_IP:
                    oldest = min(attempts)
                    retry_after = int(LOGIN_LOCKOUT_WINDOW - (now - oldest)) + 1
                    return True, max(1, retry_after)

    return False, 0

def _record_login_failure(client_ip, email):
    """Record a failed login attempt for IP and email."""
    now = time.time()
    with LOGIN_ATTEMPTS_LOCK:
        if email:
            LOGIN_ATTEMPTS.setdefault(f"email:{email.lower()}", []).append(now)
        if client_ip:
            LOGIN_ATTEMPTS.setdefault(f"ip:{client_ip}", []).append(now)

def _reset_login_rate_limit(client_ip, email):
    """Clear failed login attempts upon successful authentication."""
    with LOGIN_ATTEMPTS_LOCK:
        if email:
            LOGIN_ATTEMPTS.pop(f"email:{email.lower()}", None)
        if client_ip:
            LOGIN_ATTEMPTS.pop(f"ip:{client_ip}", None)

def _hash_password(password, salt=None, iterations=210000):
    """Hash password using PBKDF2-HMAC-SHA256 with 210,000 iterations and 16-byte random salt."""
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), iterations).hex()
    return f"pbkdf2:sha256:{iterations}:{salt}:{hashed}"

def _verify_password(password, stored_password_hash):
    """Verify password in constant time using hmac.compare_digest; supports modern and legacy hashes."""
    try:
        parts = stored_password_hash.split(":")
        if len(parts) == 5 and parts[0] == "pbkdf2" and parts[1] == "sha256":
            iterations = int(parts[2])
            salt = parts[3]
            expected_hash = parts[4]
            check = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), iterations).hex()
            return hmac.compare_digest(check, expected_hash)
        elif len(parts) == 2:
            salt, expected_hash = parts
            check = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000).hex()
            return hmac.compare_digest(check, expected_hash)
        return False
    except Exception:
        return False

def _load_resets():
    """Load password reset records with thread safety."""
    with RESETS_LOCK:
        if not os.path.isfile(PASSWORD_RESETS_FILE):
            return {}
        try:
            with open(PASSWORD_RESETS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}

def _save_resets(resets):
    """Persist password reset records atomically."""
    with RESETS_LOCK:
        _atomic_json_write(PASSWORD_RESETS_FILE, resets)

def _create_password_reset_token(email):
    """Generate secure single-use token, persist to resets file, and log out-of-band."""
    token = secrets.token_hex(32)
    now = time.time()
    resets = _load_resets()
    # Prune expired tokens
    resets = {k: v for k, v in resets.items() if v.get("expires_at", 0) > now}
    resets[token] = {
        "email": email.lower(),
        "created_at": now,
        "expires_at": now + RESET_TOKEN_TTL,
        "used": False
    }
    _save_resets(resets)

    # Log to out-of-band delivery channel (never returned in HTTP response)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now))
    log_entry = (
        f"[{timestamp}] PASSWORD RESET REQUEST\n"
        f"  Recipient: {email}\n"
        f"  Token: {token}\n"
        f"  Expires: in {RESET_TOKEN_TTL // 60} minutes\n"
        f"  Reset Endpoint: POST /api/auth/reset-password\n"
        f"{'-' * 60}\n"
    )
    try:
        with open(EMAIL_OUTBOX_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry)
        print(f"[AUTH NOTIFICATION] Password reset token generated for {email}. Logged to {EMAIL_OUTBOX_FILE}")
    except Exception as e:
        print(f"[AUTH WARNING] Failed to write reset token to outbox log: {e}")

    return token

def _validate_and_consume_reset_token(token, new_password):
    """Validate token, update user password, mark token used, and revoke sessions."""
    if not token or not isinstance(token, str):
        return False, "Reset token is required."

    token = token.strip()
    resets = _load_resets()
    reset_info = resets.get(token)
    if not reset_info:
        return False, "Invalid or expired password reset token."

    now = time.time()
    if reset_info.get("used"):
        return False, "This password reset token has already been used."

    if now > reset_info.get("expires_at", 0):
        return False, "This password reset token has expired. Please request a new one."

    email = reset_info.get("email", "").lower()
    users = _load_users()
    user = users.get(email)
    if not user and is_supabase_configured():
        user = get_user_from_supabase_by_email(email)

    if not user:
        return False, "Account associated with reset token no longer exists."

    if not new_password or len(new_password) < 8:
        return False, "New password must be at least 8 characters long."

    # Update password with hardened 210,000 iterations PBKDF2
    user["password_hash"] = _hash_password(new_password)
    user["updated_at"] = now
    users[email] = user
    _save_users(users)
    if is_supabase_configured():
        try:
            save_user_to_supabase(user)
        except Exception as sbe:
            print(f"[Supabase password update error] {sbe}")

    reset_info["used"] = True
    reset_info["used_at"] = now
    resets[token] = reset_info
    _save_resets(resets)

    # Invalidate all active sessions for this user (prevent ongoing access with old credentials)
    sessions = _load_sessions()
    sessions = {k: v for k, v in sessions.items() if v.get("email", "").lower() != email}
    _save_sessions(sessions)

    return True, "Password updated successfully. You can now sign in with your new password."

def _safe_ts(val):
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0

def _load_users():
    with USER_LOCK:
        users = {}
        if os.path.isfile(USERS_FILE):
            try:
                with open(USERS_FILE, "r") as f:
                    users = json.load(f)
            except Exception:
                users = {}

        if is_supabase_configured():
            try:
                sb_users = fetch_all_users_from_supabase()
                if sb_users:
                    # Merge Supabase users with local cache
                    for em, u in sb_users.items():
                        if em not in users or _safe_ts(u.get("created_at")) > _safe_ts(users[em].get("created_at")):
                            users[em] = u
            except Exception as e:
                print(f"[Supabase load users error] {e}")

        return users

def _save_users(users):
    with USER_LOCK:
        _atomic_json_write(USERS_FILE, users)

def _load_sessions():
    with USER_LOCK:
        if not os.path.isfile(SESSIONS_FILE):
            return {}
        try:
            with open(SESSIONS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}

def _save_sessions(sessions):
    with USER_LOCK:
        _atomic_json_write(SESSIONS_FILE, sessions)

# Include cleanser module
_repo_root = os.path.abspath(os.path.dirname(__file__))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

try:
    from scripts.cleanser import (
        clean_lead,
        deduplicate_leads,
        extract_domain,
        enrich_socials,
        load_historical_leads,
        filter_previously_seen_leads,
        sanitize_csv_cell,
        sanitize_csv_row
    )
except ImportError:
    clean_lead = lambda x: x
    deduplicate_leads = lambda x: x
    extract_domain = lambda x: ""
    enrich_socials = lambda x: x
    load_historical_leads = lambda *args, **kwargs: []
    filter_previously_seen_leads = lambda leads, hist: (leads, [])
    sanitize_csv_cell = lambda x: x
    sanitize_csv_row = lambda x: x

try:
    from scripts.supabase_client import (
        is_configured as is_supabase_configured,
        test_connection as test_supabase_connection,
        sync_leads as sync_supabase_leads,
        get_config as get_supabase_config,
        disconnect as disconnect_supabase,
        save_scrape_job_to_supabase,
        fetch_history_from_supabase,
        fetch_leads_for_job_from_supabase,
        sync_all_history_to_supabase,
        save_user_to_supabase,
        get_user_from_supabase_by_email,
        fetch_all_users_from_supabase,
        register_user_in_supabase_auth
    )
except ImportError:
    is_supabase_configured = lambda: False
    test_supabase_connection = lambda *args, **kwargs: (False, "Supabase client not loaded")
    sync_supabase_leads = lambda *args, **kwargs: {"success": False, "error": "Supabase client unavailable"}
    get_supabase_config = lambda: ("", "", "leads")
    disconnect_supabase = lambda: True
    save_scrape_job_to_supabase = lambda *args, **kwargs: {"success": False, "error": "Supabase client unavailable"}
    fetch_history_from_supabase = lambda *args, **kwargs: []
    fetch_leads_for_job_from_supabase = lambda *args, **kwargs: []
    sync_all_history_to_supabase = lambda *args, **kwargs: {"jobs_synced": 0, "leads_synced": 0}
    save_user_to_supabase = lambda *args, **kwargs: {"success": False}
    get_user_from_supabase_by_email = lambda *args, **kwargs: None
    fetch_all_users_from_supabase = lambda *args, **kwargs: {}
    register_user_in_supabase_auth = lambda *args, **kwargs: {"success": False}

UA = "google-maps-scraper-dashboard/1.0"
LEAD_FIELDS = ["lead_tier", "lead_score", "title", "phone", "emails", "website", "category", "address", "review_rating", "review_count", "instagram", "facebook", "linkedin"]

# In-memory store for active job progress and cache
JOB_CACHE = {}
JOB_LOCK = threading.Lock()


def scraper_req(method, path, body=None, timeout=30):
    """Perform HTTP request against local gmaps-scraper Docker service."""
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if SCRAPER_KEY:
        headers["X-API-Key"] = SCRAPER_KEY
    data = json.dumps(body).encode() if body is not None else None
    url = SCRAPER_BASE.rstrip("/") + path
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def geocode_place(place):
    """Geocode city or address using Nominatim (free, 1 req/sec)."""
    q = urllib.parse.urlencode({"format": "json", "limit": 1, "q": place})
    url = f"https://nominatim.openstreetmap.org/search?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            time.sleep(1)
            if data:
                return str(data[0]["lat"]), str(data[0]["lon"]), data[0].get("display_name", place)
    except Exception as e:
        print(f"[geocode error] {e}", file=sys.stderr)
    return None, None, place


def check_scraper_health():
    """Verify if the scraper container is reachable."""
    try:
        status, _ = scraper_req("GET", "/api/v1/jobs", timeout=3)
        return status == 200
    except Exception:
        return False


def background_job_processor(job_id, params):
    """Background worker that polls job until complete, cleanses, enriches, and stores leads."""
    with JOB_LOCK:
        JOB_CACHE[job_id] = {
            "id": job_id,
            "status": "working",
            "progress_percent": 15,
            "stage": "Scraping Google Maps listings...",
            "started_at": time.time(),
            "leads": [],
            "params": params,
            "error": None
        }

    # Poll scraper service
    max_wait = params.get("max_time", 600)
    start_time = time.time()
    completed = False

    while time.time() - start_time < max_wait:
        with JOB_LOCK:
            if JOB_CACHE.get(job_id, {}).get("cancel_requested"):
                JOB_CACHE[job_id]["status"] = "cancelled"
                JOB_CACHE[job_id]["stage"] = "Extraction cancelled by user."
                return

        try:
            _, raw = scraper_req("GET", f"/api/v1/jobs/{job_id}", timeout=10)
            res = json.loads(raw.decode())
            status = res.get("Status")

            elapsed = int(time.time() - start_time)
            # Estimate progress based on elapsed time (up to 85% during wait)
            progress = min(85, 15 + int(elapsed * 2))

            with JOB_LOCK:
                if job_id in JOB_CACHE:
                    JOB_CACHE[job_id]["status"] = status
                    JOB_CACHE[job_id]["progress_percent"] = progress
                    JOB_CACHE[job_id]["stage"] = f"Scraping listings on Google Maps ({elapsed}s elapsed)..."

            if status == "ok":
                completed = True
                break
            elif status == "failed":
                with JOB_LOCK:
                    JOB_CACHE[job_id]["status"] = "failed"
                    JOB_CACHE[job_id]["error"] = "Scraper service returned failed status. IP may be temporarily throttled."
                return
        except Exception as e:
            print(f"[poll error] {e}")

        time.sleep(4)

    if not completed:
        with JOB_LOCK:
            JOB_CACHE[job_id]["status"] = "failed"
            JOB_CACHE[job_id]["error"] = "Job timed out waiting for scraper completion."
        return

    # Download CSV
    with JOB_LOCK:
        JOB_CACHE[job_id]["stage"] = "Downloading and parsing listings..."
        JOB_CACHE[job_id]["progress_percent"] = 90

    try:
        _, raw_csv = scraper_req("GET", f"/api/v1/jobs/{job_id}/download", timeout=60)
        csv_text = raw_csv.decode("utf-8", "replace")
        rows = list(csv.DictReader(io.StringIO(csv_text)))

        # Cleanse and score
        with JOB_LOCK:
            JOB_CACHE[job_id]["stage"] = "Cleansing emails, formatting phones, and calculating lead scores..."
            JOB_CACHE[job_id]["progress_percent"] = 95

        cleaned_leads = []
        for r in rows:
            lead = {
                "title": r.get("title", ""),
                "phone": r.get("phone", ""),
                "emails": r.get("emails", ""),
                "website": r.get("website", ""),
                "category": r.get("category", ""),
                "address": r.get("address", ""),
                "review_rating": r.get("review_rating", ""),
                "review_count": r.get("review_count", ""),
                "latitude": r.get("latitude", ""),
                "longitude": r.get("longitude", ""),
                "place_id": r.get("place_id", ""),
                "cid": r.get("cid", ""),
                "instagram": "",
                "facebook": "",
                "linkedin": ""
            }
            cleaned_lead = clean_lead(lead)
            cleaned_leads.append(cleaned_lead)

        # Deduplicate within current run
        cleaned_leads = deduplicate_leads(cleaned_leads)

        # Cross-job historical deduplication
        exclude_seen = params.get("exclude_seen", True)
        skipped_previous = []
        if exclude_seen:
            historical_leads = load_historical_leads(DATA_DIR, exclude_job_id=job_id)
            new_leads, skipped_previous = filter_previously_seen_leads(cleaned_leads, historical_leads)
            cleaned_leads = new_leads
        else:
            historical_leads = load_historical_leads(DATA_DIR, exclude_job_id=job_id)
            _, skipped_previous = filter_previously_seen_leads(cleaned_leads, historical_leads)

        # Enrich social profiles (Instagram / Facebook / LinkedIn) from websites
        if params.get("socials", True) and cleaned_leads:
            with JOB_LOCK:
                JOB_CACHE[job_id]["stage"] = "Scanning websites for Instagram, Facebook, and LinkedIn profiles..."
                JOB_CACHE[job_id]["progress_percent"] = 96
            cleaned_leads = enrich_socials(cleaned_leads)

        # Save to data directory
        data_file = os.path.join(DATA_DIR, f"leads_{job_id}.json")
        with open(data_file, "w") as f:
            json.dump({
                "job_id": job_id,
                "params": params,
                "completed_at": time.time(),
                "leads": cleaned_leads,
                "skipped_previous_count": len(skipped_previous),
                "metrics": {
                    "total": len(cleaned_leads),
                    "skipped_previous": len(skipped_previous),
                    "raw_extracted": len(cleaned_leads) + len(skipped_previous)
                }
            }, f, indent=2)

        # Count metrics
        hot_cnt = sum(1 for l in cleaned_leads if l.get("lead_tier") == "HOT")
        warm_cnt = sum(1 for l in cleaned_leads if l.get("lead_tier") == "WARM")
        cold_cnt = sum(1 for l in cleaned_leads if l.get("lead_tier") == "COLD")
        email_cnt = sum(1 for l in cleaned_leads if l.get("emails"))
        phone_cnt = sum(1 for l in cleaned_leads if l.get("clean_phone"))

        # Automatic Supabase sync if configured and working
        supabase_synced = 0
        if is_supabase_configured() and os.environ.get("SUPABASE_AUTO_SYNC", "true").lower() == "true":
            try:
                with JOB_LOCK:
                    JOB_CACHE[job_id]["stage"] = "Syncing qualified leads and history to Supabase Cloud..."
                sync_res = sync_supabase_leads(cleaned_leads, job_id=job_id)
                if sync_res.get("success"):
                    supabase_synced = sync_res.get("count", 0)
                    print(f"[Supabase] Automatically synced {supabase_synced} leads to table '{get_supabase_config()[2]}'")
                else:
                    print(f"[Supabase] Cloud leads sync note: {sync_res.get('error')}")

                # Save history job record to Supabase
                save_scrape_job_to_supabase({
                    "id": job_id,
                    "keyword": params.get("keyword", ""),
                    "city": params.get("city", ""),
                    "depth": params.get("depth", 5),
                    "status": "ok",
                    "stage": "Completed",
                    "started_at": int(start_time),
                    "completed_at": int(time.time()),
                    "leads": cleaned_leads,
                    "metrics": {
                        "total": len(cleaned_leads),
                        "hot": hot_cnt,
                        "warm": warm_cnt,
                        "cold": cold_cnt,
                        "with_email": email_cnt,
                        "with_phone": phone_cnt,
                        "skipped_previous": len(skipped_previous)
                    },
                    "params": params
                })
            except Exception as se:
                print(f"[Supabase] Cloud sync warning: {se}. Leads safely saved to Local DB.")

        stage_desc = f"Complete! {len(cleaned_leads)} new leads ready"
        if skipped_previous:
            stage_desc += f" ({len(skipped_previous)} previously scraped leads skipped)"
        if not cleaned_leads and skipped_previous:
            stage_desc = f"All {len(skipped_previous)} leads were already collected in past searches! Increase Scroll Depth to crawl deeper for new leads."
        if supabase_synced:
            stage_desc += f" ({supabase_synced} synced to Supabase)"

        with JOB_LOCK:
            JOB_CACHE[job_id].update({
                "status": "ok",
                "progress_percent": 100,
                "stage": stage_desc,
                "leads": cleaned_leads,
                "skipped_previous_count": len(skipped_previous),
                "metrics": {
                    "total": len(cleaned_leads),
                    "hot": hot_cnt,
                    "warm": warm_cnt,
                    "cold": cold_cnt,
                    "with_email": email_cnt,
                    "with_phone": phone_cnt,
                    "supabase_synced": supabase_synced,
                    "skipped_previous": len(skipped_previous),
                    "raw_extracted": len(cleaned_leads) + len(skipped_previous)
                }
            })

    except Exception as e:
        with JOB_LOCK:
            JOB_CACHE[job_id]["status"] = "failed"
            JOB_CACHE[job_id]["error"] = f"Processing error: {str(e)}"


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    """Custom HTTP request handler with REST API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def _get_cors_origin(self):
        """Return origin if present in configured ALLOWED_ORIGINS, else None."""
        origin = self.headers.get("Origin", "").strip().rstrip("/")
        if not origin:
            return None
        normalized_allowed = {o.rstrip("/") for o in ALLOWED_ORIGINS}
        if origin in normalized_allowed:
            return origin
        return None

    def _send_json(self, data, status_code=200):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        cors_origin = self._get_cors_origin()
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Session-Token")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(204)
        cors_origin = self._get_cors_origin()
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Session-Token")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.end_headers()

    def _read_json_body(self, max_size=MAX_REQUEST_BODY_SIZE):
        """Safely read and parse request JSON body with size limits and error checks."""
        content_length_header = self.headers.get("Content-Length")
        if not content_length_header:
            return {}
        try:
            content_length = int(content_length_header.strip())
        except ValueError:
            self._send_json({"error": "Invalid Content-Length header"}, 400)
            return None

        if content_length < 0:
            self._send_json({"error": "Negative Content-Length header"}, 400)
            return None

        if content_length > max_size:
            self._send_json({"error": f"Request payload too large (maximum {max_size} bytes allowed)"}, 413)
            return None

        if content_length == 0:
            return {}

        try:
            raw_bytes = self.rfile.read(content_length)
            if len(raw_bytes) != content_length:
                self._send_json({"error": "Request body truncated or incomplete"}, 400)
                return None
            return json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON payload"}, 400)
            return None
        except Exception as e:
            self._send_json({"error": f"Error reading request: {str(e)}"}, 400)
            return None

    def _get_authenticated_user(self):
        """Extract and validate bearer token or session header with expiration check."""
        auth_header = self.headers.get("Authorization", "").strip()
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif self.headers.get("X-Session-Token"):
            token = self.headers.get("X-Session-Token").strip()

        if not token:
            return None

        sessions = _load_sessions()
        session_info = sessions.get(token)
        if not session_info:
            return None

        now = time.time()
        expires_at = session_info.get("expires_at")
        created_at = session_info.get("created_at", now)
        if expires_at is not None:
            if now > expires_at:
                return None
        else:
            if now - created_at > SESSION_TTL:
                return None

        email = session_info.get("email", "")
        if not email:
            return None

        users = _load_users()
        user = users.get(email)
        return user

    def _require_auth(self):
        """Require valid active session; return 401 Unauthorized if missing/expired/revoked."""
        user = self._get_authenticated_user()
        if not user:
            self._send_json({"error": "Authentication required. Please sign in."}, 401)
            return None
        return user

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # Static assets or index
        if path == "/" or not path.startswith("/api/"):
            return super().do_GET()

        # REST API Routes
        if path == "/api/auth/me":
            user = self._get_authenticated_user()
            if not user:
                self._send_json({"authenticated": False, "user": None}, 401)
                return

            self._send_json({
                "authenticated": True,
                "user": {
                    "id": user.get("id"),
                    "name": user.get("name"),
                    "email": user.get("email"),
                    "created_at": user.get("created_at")
                }
            })
            return

        if path == "/api/status":
            is_healthy = check_scraper_health()
            self._send_json({
                "scraper_up": is_healthy,
                "scraper_base": SCRAPER_BASE,
                "dashboard_port": PORT,
                "active_jobs_count": len([j for j in JOB_CACHE.values() if j.get("status") == "working"])
            })
            return

        if path == "/api/supabase/status":
            url, key, table = get_supabase_config()
            configured = is_supabase_configured()
            ok = False
            msg = "Using built-in Local Database."
            if configured:
                ok, msg = test_supabase_connection(url, key, table)

            # Check if caller is authenticated
            user = self._get_authenticated_user()
            if not user:
                # Public response: high-level connectivity indicators only, zero secrets or URLs
                self._send_json({
                    "configured": configured,
                    "connected": ok,
                    "default_backend": "supabase" if ok else "local",
                    "auto_sync": os.environ.get("SUPABASE_AUTO_SYNC", "true").lower() == "true",
                    "message": msg if not ok else "Connected to Supabase Cloud"
                })
                return

            # Authenticated response: configuration details with masked key preview
            masked_key = (key[:6] + "..." + key[-4:]) if len(key) > 10 else ("***" if key else "")
            self._send_json({
                "configured": configured,
                "connected": ok,
                "default_backend": "supabase" if ok else "local",
                "url": url,
                "key_preview": masked_key,
                "table": table,
                "auto_sync": os.environ.get("SUPABASE_AUTO_SYNC", "true").lower() == "true",
                "message": msg
            })
            return

        if path in ("/api/jobs", "/api/history"):
            if not self._require_auth():
                return
            # List past and current jobs
            jobs = []
            with JOB_LOCK:
                for j in JOB_CACHE.values():
                    jobs.append({
                        "id": j.get("id"),
                        "status": j.get("status"),
                        "stage": j.get("stage"),
                        "progress_percent": j.get("progress_percent"),
                        "started_at": j.get("started_at"),
                        "keyword": j.get("params", {}).get("keyword", ""),
                        "city": j.get("params", {}).get("city", ""),
                        "metrics": j.get("metrics")
                    })

            # Check saved files in DATA_DIR
            try:
                for fn in os.listdir(DATA_DIR):
                    if fn.startswith("leads_") and fn.endswith(".json"):
                        jid = fn[6:-5]
                        if not any(x["id"] == jid for x in jobs):
                            fp = os.path.join(DATA_DIR, fn)
                            with open(fp) as f:
                                d = json.load(f)
                                leads = d.get("leads", [])
                                jobs.append({
                                    "id": jid,
                                    "status": "ok",
                                    "stage": "Loaded from archive",
                                    "progress_percent": 100,
                                    "started_at": d.get("completed_at", 0),
                                    "keyword": d.get("params", {}).get("keyword", ""),
                                    "city": d.get("params", {}).get("city", ""),
                                    "metrics": {
                                        "total": len(leads),
                                        "hot": sum(1 for l in leads if l.get("lead_tier") == "HOT"),
                                        "warm": sum(1 for l in leads if l.get("lead_tier") == "WARM"),
                                        "cold": sum(1 for l in leads if l.get("lead_tier") == "COLD"),
                                        "with_email": sum(1 for l in leads if l.get("emails")),
                                        "with_phone": sum(1 for l in leads if l.get("clean_phone"))
                                    }
                                })
            except Exception as e:
                print(f"[history error] {e}")

            jobs.sort(key=lambda x: _safe_ts(x.get("started_at")), reverse=True)

            # Enrich and merge with Supabase Cloud history
            if is_supabase_configured():
                try:
                    sb_jobs = fetch_history_from_supabase()
                    for sj in sb_jobs:
                        s_id = sj.get("id")
                        existing = next((x for x in jobs if x["id"] == s_id), None)
                        if existing:
                            existing["supabase_synced"] = True
                        else:
                            sj["supabase_synced"] = True
                            jobs.append(sj)
                except Exception as sbe:
                    print(f"[Supabase history check error] {sbe}")

            jobs.sort(key=lambda x: _safe_ts(x.get("started_at")), reverse=True)
            self._send_json({"jobs": jobs, "supabase_connected": is_supabase_configured()})
            return

        if path.startswith("/api/job/") and path.endswith("/status"):
            if not self._require_auth():
                return
            parts = path.split("/")
            if len(parts) < 4:
                self._send_json({"error": "Invalid job ID"}, 400)
                return
            job_id = parts[3]
            if not _validate_job_id(job_id):
                self._send_json({"error": "Invalid job ID format"}, 400)
                return
            with JOB_LOCK:
                job_info = JOB_CACHE.get(job_id)

            if not job_info:
                # Check on disk safely
                saved_file = _get_job_file_path(job_id)
                if saved_file and os.path.isfile(saved_file):
                    with open(saved_file) as f:
                        d = json.load(f)
                        leads = d.get("leads", [])
                        job_info = {
                            "id": job_id,
                            "status": "ok",
                            "stage": "Completed",
                            "progress_percent": 100,
                            "leads": leads,
                            "params": d.get("params", {}),
                            "metrics": {
                                "total": len(leads),
                                "hot": sum(1 for l in leads if l.get("lead_tier") == "HOT"),
                                "warm": sum(1 for l in leads if l.get("lead_tier") == "WARM"),
                                "cold": sum(1 for l in leads if l.get("lead_tier") == "COLD"),
                                "with_email": sum(1 for l in leads if l.get("emails")),
                                "with_phone": sum(1 for l in leads if l.get("clean_phone"))
                            }
                        }
                        with JOB_LOCK:
                            JOB_CACHE[job_id] = job_info
                elif is_supabase_configured():
                    # Check in Supabase Cloud
                    try:
                        sb_leads = fetch_leads_for_job_from_supabase(job_id)
                        if sb_leads:
                            job_info = {
                                "id": job_id,
                                "status": "ok",
                                "stage": "Loaded from Supabase Cloud",
                                "progress_percent": 100,
                                "leads": sb_leads,
                                "params": {},
                                "metrics": {
                                    "total": len(sb_leads),
                                    "hot": sum(1 for l in sb_leads if l.get("lead_tier") == "HOT"),
                                    "warm": sum(1 for l in sb_leads if l.get("lead_tier") == "WARM"),
                                    "cold": sum(1 for l in sb_leads if l.get("lead_tier") == "COLD"),
                                    "with_email": sum(1 for l in sb_leads if l.get("emails")),
                                    "with_phone": sum(1 for l in sb_leads if l.get("clean_phone"))
                                }
                            }
                            with JOB_LOCK:
                                JOB_CACHE[job_id] = job_info
                    except Exception as sbe:
                        print(f"[Supabase job status fetch error] {sbe}")

            if not job_info:
                self._send_json({"error": "Job not found"}, 404)
                return

            self._send_json(job_info)
            return

        if path.startswith("/api/job/") and path.endswith("/leads"):
            if not self._require_auth():
                return
            parts = path.split("/")
            if len(parts) < 4:
                self._send_json({"error": "Invalid job ID"}, 400)
                return
            job_id = parts[3]
            if not _validate_job_id(job_id):
                self._send_json({"error": "Invalid job ID format"}, 400)
                return
            leads = []
            with JOB_LOCK:
                if job_id in JOB_CACHE:
                    leads = JOB_CACHE[job_id].get("leads", [])

            if not leads:
                saved_file = _get_job_file_path(job_id)
                if saved_file and os.path.isfile(saved_file):
                    with open(saved_file) as f:
                        d = json.load(f)
                        leads = d.get("leads", [])
                elif is_supabase_configured():
                    try:
                        leads = fetch_leads_for_job_from_supabase(job_id)
                    except Exception as sbe:
                        print(f"[Supabase fetch leads error] {sbe}")

            self._send_json({"job_id": job_id, "leads": leads, "count": len(leads)})
            return

        if path == "/api/export":
            if not self._require_auth():
                return
            job_id = query.get("job_id", [""])[0].strip()
            if not _validate_job_id(job_id):
                self._send_json({"error": "Invalid or missing job ID"}, 400)
                return
            fmt = query.get("format", ["csv"])[0].lower()

            leads = []
            with JOB_LOCK:
                if job_id in JOB_CACHE:
                    leads = JOB_CACHE[job_id].get("leads", [])

            if not leads:
                saved_file = _get_job_file_path(job_id)
                if saved_file and os.path.isfile(saved_file):
                    with open(saved_file) as f:
                        d = json.load(f)
                        leads = d.get("leads", [])

            if not leads and is_supabase_configured():
                leads = fetch_leads_for_job_from_supabase(job_id)

            if not leads:
                self._send_json({"error": "No leads found for this job ID"}, 404)
                return

            filename_base = f"leads_{job_id[:8]}"

            if fmt == "json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{filename_base}.json"')
                self.end_headers()
                self.wfile.write(json.dumps(leads, indent=2, ensure_ascii=False).encode("utf-8"))
                return

            elif fmt in ("crm", "outreach", "instantly", "smartlead", "hubspot"):
                # Specially formatted for cold email and CRM tools
                output = io.StringIO()
                crm_fields = [
                    "Company Name", "Contact Email", "Phone Number", "Website",
                    "Domain", "Lead Tier", "Lead Score", "Category", "Full Address",
                    "Rating", "Review Count", "Instagram", "Facebook", "LinkedIn"
                ]
                writer = csv.DictWriter(output, fieldnames=crm_fields)
                writer.writeheader()
                for l in leads:
                    writer.writerow({
                        "Company Name": sanitize_csv_cell(l.get("title", "")),
                        "Contact Email": sanitize_csv_cell(l.get("emails", "")),
                        "Phone Number": sanitize_csv_cell(l.get("clean_phone") or l.get("phone", "")),
                        "Website": sanitize_csv_cell(l.get("website", "")),
                        "Domain": sanitize_csv_cell(l.get("domain", "")),
                        "Lead Tier": sanitize_csv_cell(l.get("lead_tier", "")),
                        "Lead Score": l.get("lead_score", 0),
                        "Category": sanitize_csv_cell(l.get("category", "")),
                        "Full Address": sanitize_csv_cell(l.get("address", "")),
                        "Rating": sanitize_csv_cell(l.get("review_rating", "")),
                        "Review Count": sanitize_csv_cell(l.get("review_count", "")),
                        "Instagram": sanitize_csv_cell(l.get("instagram", "")),
                        "Facebook": sanitize_csv_cell(l.get("facebook", "")),
                        "LinkedIn": sanitize_csv_cell(l.get("linkedin", ""))
                    })
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{filename_base}_crm_outreach.csv"')
                self.end_headers()
                self.wfile.write(output.getvalue().encode("utf-8"))
                return

            else:
                # Standard Clean CSV
                output = io.StringIO()
                fields = ["lead_tier", "lead_score", "title", "phone", "emails", "website", "category", "address", "review_rating", "review_count", "instagram", "facebook", "linkedin"]
                writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                for l in leads:
                    writer.writerow(sanitize_csv_row(l))
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{filename_base}_clean.csv"')
                self.end_headers()
                self.wfile.write(output.getvalue().encode("utf-8"))
                return

        self._send_json({"error": "Endpoint not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/auth/signup":
            params = self._read_json_body()
            if params is None:
                return

            email = params.get("email", "").strip().lower()
            password = params.get("password", "").strip()
            name = params.get("name", "").strip() or email.split("@")[0].capitalize()

            if not email or "@" not in email:
                self._send_json({"error": "Please enter a valid work email address."}, 400)
                return
            if not password or len(password) < 8:
                self._send_json({"error": "Password must be at least 8 characters long."}, 400)
                return

            users = _load_users()
            if email in users:
                self._send_json({"error": "An account with this email already exists. Please sign in."}, 409)
                return

            user_id = secrets.token_hex(8)
            password_hash = _hash_password(password)
            user_obj = {
                "id": user_id,
                "name": name,
                "email": email,
                "password_hash": password_hash,
                "created_at": time.time()
            }
            users[email] = user_obj
            _save_users(users)

            if is_supabase_configured():
                try:
                    auth_res = register_user_in_supabase_auth(email, password, name)
                    if auth_res.get("success"):
                        print(f"✔ Registered user {email} in Supabase Authentication ({auth_res.get('source', 'auth')})")
                    else:
                        print(f"[Supabase auth notice] {auth_res.get('error')}")
                except Exception as ae:
                    print(f"[Supabase auth error] {ae}")

                try:
                    save_user_to_supabase(user_obj)
                except Exception as sbe:
                    print(f"[Supabase sync user error] {sbe}")

            token = secrets.token_hex(24)
            sessions = _load_sessions()
            sessions[token] = {
                "email": email,
                "user_id": user_id,
                "created_at": time.time(),
                "expires_at": time.time() + SESSION_TTL
            }
            _save_sessions(sessions)

            self._send_json({
                "success": True,
                "message": "Account created successfully!",
                "token": token,
                "user": {
                    "id": user_id,
                    "name": name,
                    "email": email
                }
            })
            return

        if path == "/api/auth/login":
            params = self._read_json_body()
            if params is None:
                return

            email = params.get("email", "").strip().lower()
            password = params.get("password", "").strip()

            if not email or not password:
                self._send_json({"error": "Please provide both email and password."}, 400)
                return

            client_ip = self.client_address[0] if hasattr(self, 'client_address') and self.client_address else "127.0.0.1"

            # Check rate limiting
            is_limited, retry_after = _check_login_rate_limit(client_ip, email)
            if is_limited:
                self.send_response(429)
                self.send_header("Retry-After", str(retry_after))
                cors_origin = self._get_cors_origin()
                if cors_origin:
                    self.send_header("Access-Control-Allow-Origin", cors_origin)
                    self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": f"Too many failed login attempts. Please try again in {retry_after} seconds."
                }).encode("utf-8"))
                return

            users = _load_users()
            user = users.get(email)
            if not user or not _verify_password(password, user.get("password_hash", "")):
                _record_login_failure(client_ip, email)
                self._send_json({"error": "Invalid email or password."}, 401)
                return

            # Clear failed attempts on successful login
            _reset_login_rate_limit(client_ip, email)

            # Transparently upgrade legacy hash to PBKDF2 210,000 iterations
            if not user.get("password_hash", "").startswith("pbkdf2:sha256:"):
                user["password_hash"] = _hash_password(password)
                users[email] = user
                _save_users(users)

            token = secrets.token_hex(24)
            sessions = _load_sessions()
            sessions[token] = {
                "email": email,
                "user_id": user.get("id"),
                "created_at": time.time(),
                "expires_at": time.time() + SESSION_TTL
            }
            _save_sessions(sessions)

            # Record login timestamp in database if Supabase is active
            user["last_login_at"] = time.time()
            users[email] = user
            _save_users(users)
            if is_supabase_configured():
                try:
                    save_user_to_supabase(user)
                except Exception as sbe:
                    print(f"[Supabase sync login error] {sbe}")

            self._send_json({
                "success": True,
                "message": f"Welcome back, {user.get('name')}!",
                "token": token,
                "user": {
                    "id": user.get("id"),
                    "name": user.get("name"),
                    "email": user.get("email")
                }
            })
            return

        if path == "/api/auth/forgot-password":
            params = self._read_json_body()
            if params is None:
                return

            email = params.get("email", "").strip().lower()

            if not email or "@" not in email:
                self._send_json({"error": "Please enter a valid work email address."}, 400)
                return

            users = _load_users()
            user = users.get(email)
            if not user and is_supabase_configured():
                user = get_user_from_supabase_by_email(email)

            if user:
                _create_password_reset_token(email)

            # Always return identical generic confirmation to prevent user enumeration
            # Notice: The reset token is NEVER leaked in the HTTP response
            self._send_json({
                "success": True,
                "message": "If an account exists with this email address, password reset instructions have been sent."
            })
            return

        if path == "/api/auth/reset-password":
            params = self._read_json_body()
            if params is None:
                return

            token = params.get("token", "").strip()
            new_password = params.get("new_password", "").strip()

            if not token:
                self._send_json({"error": "Reset token is required."}, 400)
                return

            if not new_password or len(new_password) < 8:
                self._send_json({"error": "New password must be at least 8 characters long."}, 400)
                return

            ok, msg = _validate_and_consume_reset_token(token, new_password)
            if not ok:
                self._send_json({"error": msg}, 400)
                return

            self._send_json({"success": True, "message": msg})
            return

        if path == "/api/auth/google":
            # Disabled until verified server-side OAuth2/OIDC token verification is configured
            self._send_json({
                "error": "Google Sign-In is disabled until verified server-side OAuth2/OIDC token verification is configured.",
                "code": "OAUTH_DISABLED"
            }, 501)
            return

        if path == "/api/auth/logout":
            auth_header = self.headers.get("Authorization", "").strip()
            token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
            if not token and self.headers.get("X-Session-Token"):
                token = self.headers.get("X-Session-Token").strip()
            sessions = _load_sessions()
            if token and token in sessions:
                del sessions[token]
            # Prune any expired sessions
            now = time.time()
            sessions = {k: v for k, v in sessions.items() if v.get("expires_at", v.get("created_at", 0) + SESSION_TTL) > now}
            _save_sessions(sessions)
            self._send_json({"success": True, "message": "Logged out successfully."})
            return

        if path == "/api/scrape/start":
            if not self._require_auth():
                return

            params = self._read_json_body()
            if params is None:
                return

            keyword = params.get("keyword", "").strip()
            city = params.get("city", "").strip()
            depth = int(params.get("depth", 5))
            extract_email = bool(params.get("email", True))
            find_socials = bool(params.get("socials", True))
            exclude_seen = bool(params.get("exclude_seen", True))
            lat = params.get("lat")
            lon = params.get("lon")

            if not keyword:
                self._send_json({"error": "Keyword is required"}, 400)
                return

            # Combine search terms
            full_query = f"{keyword} in {city}" if city and " in " not in keyword.lower() else keyword

            # Resolve coordinates if not provided
            place_for_geo = city if city else keyword
            resolved_city = city or place_for_geo

            if not (lat and lon):
                lat_str, lon_str, resolved_name = geocode_place(place_for_geo)
                if not (lat_str and lon_str):
                    self._send_json({"error": f"Could not determine geographic coordinates for '{place_for_geo}'. Please enter a recognizable city or coordinates."}, 422)
                    return
                lat, lon = lat_str, lon_str
                resolved_city = resolved_name

            # Check scraper health
            if not check_scraper_health():
                self._send_json({"error": f"Scraper service is not reachable at {SCRAPER_BASE}. Is Docker running?"}, 503)
                return

            # Submit job to local scraper API
            scraper_body = {
                "name": f"web-{int(time.time())}",
                "keywords": [full_query],
                "lang": "en",
                "zoom": 15,
                "lat": str(lat),
                "lon": str(lon),
                "fast_mode": False,
                "radius": 10000,
                "depth": depth,
                "email": extract_email,
                "max_time": 600
            }

            try:
                _, resp_raw = scraper_req("POST", "/api/v1/jobs", scraper_body)
                job_id = json.loads(resp_raw.decode()).get("id")
                if not job_id:
                    self._send_json({"error": "No job ID returned from scraper service"}, 500)
                    return
            except urllib.error.HTTPError as e:
                err_text = e.read().decode("utf-8", "replace")
                self._send_json({"error": f"Scraper service HTTP {e.code}: {err_text}"}, e.code)
                return
            except Exception as e:
                self._send_json({"error": f"Failed to start scrape: {str(e)}"}, 500)
                return

            # Launch background poll & processing thread
            job_params = {
                "keyword": keyword,
                "city": resolved_city,
                "full_query": full_query,
                "lat": lat,
                "lon": lon,
                "depth": depth,
                "email": extract_email,
                "socials": find_socials,
                "exclude_seen": exclude_seen,
                "max_time": 600
            }
            t = threading.Thread(target=background_job_processor, args=(job_id, job_params), daemon=True)
            t.start()

            self._send_json({
                "job_id": job_id,
                "keyword": keyword,
                "city": resolved_city,
                "status": "working",
                "message": "Scrape job started successfully"
            })
            return

        if path == "/api/scrape/stop":
            if not self._require_auth():
                return
            params = self._read_json_body()
            if params is None:
                return
            job_id = params.get("job_id", "")
            if not job_id or not _validate_job_id(job_id):
                self._send_json({"error": "Invalid or missing job_id"}, 400)
                return
            with JOB_LOCK:
                if job_id in JOB_CACHE:
                    JOB_CACHE[job_id]["cancel_requested"] = True
                    JOB_CACHE[job_id]["status"] = "cancelled"
                    JOB_CACHE[job_id]["stage"] = "Extraction cancelled by user."
            try:
                scraper_req("DELETE", f"/api/v1/jobs/{job_id}", timeout=5)
            except Exception:
                pass
            self._send_json({"success": True, "message": f"Job {job_id} cancelled."})
            return

        if path == "/api/docker/restart":
            if not self._require_auth():
                return
            # Attempt to restart or start docker container
            try:
                res = subprocess.run(["docker", "compose", "up", "-d"], cwd=os.path.dirname(__file__), capture_output=True, text=True, timeout=30)
                if res.returncode == 0:
                    time.sleep(2)
                    self._send_json({"success": True, "message": "Docker container started"})
                else:
                    self._send_json({"success": False, "error": res.stderr}, 500)
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)
            return

        if path == "/api/supabase/test":
            if not self._require_auth():
                return
            body = self._read_json_body()
            if body is None:
                return
            url = body.get("url", "").strip().rstrip("/")
            key = body.get("key", "").strip()
            table = body.get("table", "leads").strip() or "leads"

            ok, msg = test_supabase_connection(url, key, table)
            self._send_json({"success": ok, "message": msg, "verified": ok})
            return

        if path in ("/api/supabase/save", "/api/supabase/config"):
            if not self._require_auth():
                return
            body = self._read_json_body()
            if body is None:
                return
            url = body.get("url", "").strip().rstrip("/")
            key = body.get("key", "").strip()
            table = body.get("table", "leads").strip() or "leads"
            auto_sync = bool(body.get("auto_sync", True))

            # Only save to .env if connection test passes
            ok, msg = test_supabase_connection(url, key, table)
            if not ok:
                self._send_json({
                    "success": False,
                    "message": f"Connection not verified: {msg}",
                    "verified": False,
                    "saved_to_env": False
                }, 400)
                return

            if url: os.environ["SUPABASE_URL"] = url
            if key: os.environ["SUPABASE_KEY"] = key
            os.environ["SUPABASE_TABLE"] = table
            os.environ["SUPABASE_AUTO_SYNC"] = "true" if auto_sync else "false"

            # Update .env file
            env_file = os.path.join(os.path.dirname(__file__), ".env")
            lines = []
            if os.path.isfile(env_file):
                with open(env_file) as f:
                    for line in f:
                        if line.startswith("SUPABASE_URL=") and url:
                            lines.append(f"SUPABASE_URL={url}\n")
                        elif line.startswith("SUPABASE_KEY=") and key:
                            lines.append(f"SUPABASE_KEY={key}\n")
                        elif line.startswith("SUPABASE_TABLE="):
                            lines.append(f"SUPABASE_TABLE={table}\n")
                        elif line.startswith("SUPABASE_AUTO_SYNC="):
                            lines.append(f"SUPABASE_AUTO_SYNC={'true' if auto_sync else 'false'}\n")
                        else:
                            lines.append(line)
            with open(env_file, "w") as f:
                f.writelines(lines)

            self._send_json({
                "success": True,
                "message": "Database verified! Set as default backend in .env.",
                "verified": True,
                "saved_to_env": True
            })
            return

        if path == "/api/supabase/disconnect":
            if not self._require_auth():
                return
            disconnect_supabase()
            self._send_json({
                "success": True,
                "message": "Disconnected Supabase. Default backend reverted to Local Database.",
                "default_backend": "local"
            })
            return

        if path == "/api/supabase/sync":
            if not self._require_auth():
                return
            body = self._read_json_body()
            if body is None:
                return
            job_id = body.get("job_id", "")
            if job_id and not _validate_job_id(job_id):
                self._send_json({"error": "Invalid job_id format"}, 400)
                return
            custom_leads = body.get("leads")

            leads_to_sync = []
            if custom_leads and isinstance(custom_leads, list) and len(custom_leads) > 0:
                leads_to_sync = custom_leads
            else:
                with JOB_LOCK:
                    if job_id and job_id in JOB_CACHE:
                        leads_to_sync = JOB_CACHE[job_id].get("leads", [])
                    elif not job_id and JOB_CACHE:
                        latest_job = list(JOB_CACHE.values())[-1]
                        leads_to_sync = latest_job.get("leads", [])
                        job_id = latest_job.get("id", "")

                if not leads_to_sync and job_id:
                    saved_file = _get_job_file_path(job_id)
                    if saved_file and os.path.isfile(saved_file):
                        with open(saved_file) as f:
                            d = json.load(f)
                            leads_to_sync = d.get("leads", [])

            if not leads_to_sync:
                self._send_json({"success": False, "error": "No leads found to sync. Run an extraction first."}, 400)
                return

            res = sync_supabase_leads(leads_to_sync, job_id=job_id)
            self._send_json(res)
            return

        if path == "/api/history/sync-all":
            if not self._require_auth():
                return
            res = sync_all_history_to_supabase(DATA_DIR)
            self._send_json({
                "success": True,
                "message": f"Successfully synced {res.get('jobs_synced', 0)} scrape jobs and {res.get('leads_synced', 0)} leads to Supabase.",
                "jobs_synced": res.get("jobs_synced", 0),
                "leads_synced": res.get("leads_synced", 0),
                "errors": res.get("errors", [])
            })
            return

        self._send_json({"error": "Endpoint not found"}, 404)


def run_server():
    server = ThreadingHTTPServer((HOST, PORT), DashboardRequestHandler)
    print("=" * 65)
    print(f"🗺️  Google Maps Scraper Pro Dashboard running at:")
    print(f"    👉  http://{HOST}:{PORT}")
    print(f"    Host Binding: {HOST} (Single-user Local Isolation)")
    if HOST != "127.0.0.1":
        print(f"    ⚠️  Remote Host Binding Active! Ensure network access is secured.")
    print(f"    Connected Scraper Backend: {SCRAPER_BASE}")
    print("=" * 65)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard server...")
        server.shutdown()


if __name__ == "__main__":
    run_server()

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
import fcntl
import hashlib
import hmac
import io
import json
import logging
import os
import re
import resource
import secrets
import signal
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

SERVER_START_TIME = time.time()

class SecretRedactionFilter(logging.Filter):
    """Filter that scrubs credentials, tokens, and passwords from log records."""
    PATTERNS = [
        (re.compile(r'(Bearer\s+)[A-Za-z0-9_\-\.]{8,}', re.IGNORECASE), r'\1[REDACTED_TOKEN]'),
        (re.compile(r'(["\']?(?:password|token|secret|apiKey|api_key)["\']?\s*[:=]\s*["\'])([^"\']+)(["\'])', re.IGNORECASE), r'\1[REDACTED]\3'),
        (re.compile(r'(eyJ[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]*)'), r'[REDACTED_JWT]'),
        (re.compile(r'(reset_token=)[a-f0-9]{16,}', re.IGNORECASE), r'\1[REDACTED_TOKEN]'),
    ]

    def filter(self, record):
        if isinstance(record.msg, str):
            msg = record.msg
            for pattern, repl in self.PATTERNS:
                msg = pattern.sub(repl, msg)
            record.msg = msg
        return True

logger = logging.getLogger("leadmap")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    if os.environ.get("LOG_FORMAT", "").lower() == "json":
        class JsonLogFormatter(logging.Formatter):
            def format(self, record):
                log_record = {
                    "timestamp": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage()
                }
                return json.dumps(log_record)
        _handler.setFormatter(JsonLogFormatter())
    else:
        _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [leadmap] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    _handler.addFilter(SecretRedactionFilter())
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

def validate_environment():
    """Validate server configuration and prerequisites before startup.

    Returns (bool, str): (True, "OK") or (False, "Descriptive error message").
    """
    try:
        p = int(PORT)
        if not (1 <= p <= 65535):
            return False, f"Invalid DASHBOARD_PORT '{PORT}': Must be between 1 and 65535."
    except (ValueError, TypeError):
        return False, f"Invalid DASHBOARD_PORT '{PORT}': Must be an integer."

    try:
        mcj = int(MAX_CONCURRENT_JOBS)
        if mcj < 1:
            return False, f"Invalid MAX_CONCURRENT_JOBS '{MAX_CONCURRENT_JOBS}': Must be >= 1."
    except (ValueError, TypeError):
        return False, f"Invalid MAX_CONCURRENT_JOBS '{MAX_CONCURRENT_JOBS}': Must be an integer."

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        test_probe = os.path.join(DATA_DIR, ".write_test")
        with open(test_probe, "w") as f:
            f.write("probe")
        if os.path.exists(test_probe):
            os.remove(test_probe)
    except Exception as e:
        return False, f"DATA_DIR '{DATA_DIR}' is not writable: {e}"

    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or os.environ.get("SUPABASE_KEY", "")).strip()
    if sb_url and not sb_key:
        return False, "SUPABASE_URL is provided, but SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY is missing."

    return True, "OK"

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

# Phase 2: Reliability & Concurrency Controls
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", 2))
JOB_TIMEOUT_SECONDS = int(os.environ.get("JOB_TIMEOUT_SECONDS", 600))
JOB_RETENTION_DAYS = int(os.environ.get("JOB_RETENTION_DAYS", 30))
MAX_COMPLETED_JOBS = int(os.environ.get("MAX_COMPLETED_JOBS", 100))

class InterProcessAdmissionLock:
    """Inter-process POSIX file lock and reentrant thread lock for job admission."""
    def __init__(self, lock_file_path=None):
        self.thread_lock = threading.RLock()
        self.lock_file_path = lock_file_path
        self._fd = None
        self._reentrant_count = 0

    def __enter__(self):
        self.thread_lock.acquire()
        if self._reentrant_count == 0:
            target_path = self.lock_file_path or os.path.join(DATA_DIR, ".job_admission.lock")
            try:
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                self._fd = os.open(target_path, os.O_CREAT | os.O_RDWR, 0o600)
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            except Exception:
                # Fall back cleanly if flock is unsupported on host filesystem
                pass
        self._reentrant_count += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._reentrant_count -= 1
        if self._reentrant_count == 0 and self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None
        self.thread_lock.release()

JOB_ADMISSION_LOCK = InterProcessAdmissionLock()
PROCESS_LOCK = threading.Lock()
JOB_PROCESSES = {}  # job_id -> set of subprocess.Popen instances

SERVER_SHUTDOWN_EVENT = threading.Event()
_ACTIVE_SERVER_INSTANCE = None

def graceful_shutdown_signal_handler(signum, frame):
    """Handle SIGTERM / SIGINT by stopping jobs, cleaning child processes, and shutting down server."""
    sig_name = "SIGTERM" if signum == signal.SIGTERM else ("SIGINT" if signum == signal.SIGINT else str(signum))
    logger.info(f"Received {sig_name}, initiating graceful shutdown...")
    SERVER_SHUTDOWN_EVENT.set()

    # Terminate any active scraper child processes cleanly
    with PROCESS_LOCK:
        active_job_ids = list(JOB_PROCESSES.keys())
    for jid in active_job_ids:
        _terminate_job_processes(jid, grace_timeout=1.0, sig=signal.SIGTERM)

    # Trigger server shutdown if running
    global _ACTIVE_SERVER_INSTANCE
    if _ACTIVE_SERVER_INSTANCE is not None:
        def _async_stop():
            try:
                _ACTIVE_SERVER_INSTANCE.shutdown()
            except Exception:
                pass
        threading.Thread(target=_async_stop, daemon=True).start()

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

def _get_job_owner_id(job_id: str):
    """Retrieve owner user_id for a job from memory cache or persisted disk file."""
    if not job_id:
        return None
    with JOB_LOCK:
        if job_id in JOB_CACHE:
            uid = JOB_CACHE[job_id].get("user_id") or JOB_CACHE[job_id].get("params", {}).get("user_id")
            if uid:
                return uid
    saved_file = _get_job_file_path(job_id)
    if saved_file and os.path.isfile(saved_file):
        try:
            with open(saved_file, "r") as f:
                d = json.load(f)
                return d.get("user_id") or d.get("params", {}).get("user_id")
        except Exception:
            return None
    return None

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

def _register_job_process(job_id: str, proc):
    """Register an active child subprocess for a specific scraping job."""
    if not job_id or not proc:
        return
    with PROCESS_LOCK:
        if job_id not in JOB_PROCESSES:
            JOB_PROCESSES[job_id] = set()
        JOB_PROCESSES[job_id].add(proc)

def _unregister_job_process(job_id: str, proc):
    """Unregister a finished child subprocess for a specific scraping job."""
    if not job_id or not proc:
        return
    with PROCESS_LOCK:
        if job_id in JOB_PROCESSES:
            JOB_PROCESSES[job_id].discard(proc)
            if not JOB_PROCESSES[job_id]:
                JOB_PROCESSES.pop(job_id, None)

def _terminate_job_processes(job_id: str, grace_timeout: float = 5.0, sig=None):
    """Safely terminate child subprocesses belonging exclusively to job_id.

    Applies graceful SIGTERM first, waits up to grace_timeout seconds,
    then issues SIGKILL if the process fails to terminate.
    """
    if not job_id:
        return
    with PROCESS_LOCK:
        procs = list(JOB_PROCESSES.pop(job_id, set()))

    for p in procs:
        try:
            if hasattr(p, "poll") and p.poll() is None:
                p.terminate()
        except OSError:
            pass

    deadline = time.time() + grace_timeout
    for p in procs:
        try:
            rem = max(0.1, deadline - time.time())
            if hasattr(p, "wait"):
                p.wait(timeout=rem)
        except Exception:
            try:
                if hasattr(p, "poll") and p.poll() is None:
                    if hasattr(p, "kill"):
                        p.kill()
                    if hasattr(p, "wait"):
                        p.wait(timeout=1.0)
            except Exception:
                pass

def _create_durable_job(job_id: str, user_id: str, user_email: str, params: dict):
    """Initialize a durable job record on disk and in memory with 'working' status."""
    now = time.time()
    safe_params = {
        k: v for k, v in (params or {}).items()
        if not re.search(r'(token|secret|password|auth|credential|api_?key|^key$|_key$|^key_)', str(k), re.IGNORECASE)
    }
    record = {
        "id": job_id,
        "job_id": job_id,
        "user_id": user_id,
        "user_email": user_email,
        "status": "working",
        "stage": "Job admitted and initialized...",
        "progress_percent": 5,
        "created_at": now,
        "updated_at": now,
        "started_at": now,
        "completed_at": None,
        "params": safe_params,
        "leads": [],
        "metrics": {"total": 0},
        "error": None
    }
    with JOB_LOCK:
        JOB_CACHE[job_id] = dict(record)

    data_file = _get_job_file_path(job_id)
    if data_file:
        _atomic_json_write(data_file, record)
    return record

def _update_durable_job(job_id: str, updates: dict):
    """Atomically update job state in memory and persisted disk record."""
    now = time.time()
    data_file = _get_job_file_path(job_id)
    with JOB_LOCK:
        if job_id in JOB_CACHE:
            JOB_CACHE[job_id].update(updates)
            JOB_CACHE[job_id]["updated_at"] = now
            current_record = dict(JOB_CACHE[job_id])
        else:
            current_record = None

    if data_file and os.path.isfile(data_file):
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                disk_data = json.load(f)
            disk_data.update(updates)
            disk_data["updated_at"] = now
            _atomic_json_write(data_file, disk_data)
        except Exception as e:
            print(f"[durable job update error] {e}", file=sys.stderr)
    elif data_file and current_record:
        try:
            _atomic_json_write(data_file, current_record)
        except Exception as e:
            print(f"[durable job write error] {e}", file=sys.stderr)

def _get_active_job_count():
    """Return the number of currently active/working jobs across memory and disk."""
    active_ids = set()
    with JOB_LOCK:
        for jid, job in JOB_CACHE.items():
            if job.get("status") in ("working", "starting", "running", "stopping"):
                active_ids.add(jid)

    if os.path.isdir(DATA_DIR):
        try:
            for fn in os.listdir(DATA_DIR):
                if fn.startswith("leads_") and fn.endswith(".json"):
                    jid = fn[6:-5]
                    if jid not in active_ids:
                        fp = os.path.join(DATA_DIR, fn)
                        try:
                            with open(fp, "r", encoding="utf-8") as f:
                                d = json.load(f)
                                if d.get("status") in ("working", "starting", "running", "stopping"):
                                    active_ids.add(jid)
                        except Exception:
                            pass
        except Exception:
            pass
    return len(active_ids)

def recover_interrupted_jobs(data_dir=DATA_DIR):
    """Detect jobs left in an unfinished state by a previous process and mark as failed.

    Idempotent across repeated executions.
    """
    if not os.path.isdir(data_dir):
        return 0
    interrupted_count = 0
    now = time.time()
    try:
        filenames = os.listdir(data_dir)
    except OSError:
        return 0

    for fn in filenames:
        if fn.startswith("leads_") and fn.endswith(".json"):
            fp = os.path.join(data_dir, fn)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue

            status = d.get("status")
            if status in ("working", "starting", "running", "stopping"):
                d["status"] = "failed"
                d["stage"] = "Interrupted by system restart or shutdown."
                d["error"] = "Job was interrupted before completion."
                d["updated_at"] = now
                if not d.get("completed_at"):
                    d["completed_at"] = now
                try:
                    _atomic_json_write(fp, d)
                    interrupted_count += 1
                except Exception as e:
                    print(f"[recovery write error] {e}", file=sys.stderr)

                jid = d.get("job_id") or fn[6:-5]
                with JOB_LOCK:
                    if jid in JOB_CACHE:
                        JOB_CACHE[jid].update({
                            "status": "failed",
                            "stage": d["stage"],
                            "error": d["error"],
                            "updated_at": now,
                            "completed_at": now
                        })
    return interrupted_count

def cleanup_stale_jobs_and_temp_files(data_dir=DATA_DIR, retention_days=JOB_RETENTION_DAYS, max_jobs=MAX_COMPLETED_JOBS):
    """Prune completed/failed/cancelled jobs older than retention_days or beyond max_jobs.

    Guarantees active jobs are NEVER deleted.
    Cleans orphaned temporary files (.tmp_*) older than 1 hour.
    """
    if not os.path.isdir(data_dir):
        return {"pruned_jobs": 0, "pruned_temps": 0}

    now = time.time()
    retention_cutoff = now - (retention_days * 86400)
    temp_cutoff = now - 3600  # 1 hour

    pruned_jobs = 0
    pruned_temps = 0

    # 1. Clean orphaned temporary files
    try:
        for fn in os.listdir(data_dir):
            fp = os.path.join(data_dir, fn)
            if fn.startswith(".tmp_") or fn.startswith("tmp_"):
                try:
                    mtime = os.path.getmtime(fp)
                    if mtime < temp_cutoff:
                        os.remove(fp)
                        pruned_temps += 1
                except OSError:
                    pass
    except OSError:
        pass

    # 2. Inspect leads files
    try:
        filenames = os.listdir(data_dir)
    except OSError:
        return {"pruned_jobs": 0, "pruned_temps": pruned_temps}

    completed_jobs = []
    for fn in filenames:
        if fn.startswith("leads_") and fn.endswith(".json"):
            fp = os.path.join(data_dir, fn)
            jid = fn[6:-5]

            # Check if active in memory
            with JOB_LOCK:
                mem_job = JOB_CACHE.get(jid)
                if mem_job and mem_job.get("status") in ("working", "starting", "running", "stopping"):
                    continue

            try:
                with open(fp, "r", encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue

            status = d.get("status")
            if status in ("working", "starting", "running", "stopping"):
                continue

            completed_at = d.get("completed_at") or d.get("updated_at") or os.path.getmtime(fp)
            completed_jobs.append((fp, jid, completed_at))

    # 3. Prune jobs older than retention_cutoff
    remaining_jobs = []
    for fp, jid, comp_time in completed_jobs:
        if comp_time < retention_cutoff:
            try:
                os.remove(fp)
                with JOB_LOCK:
                    JOB_CACHE.pop(jid, None)
                pruned_jobs += 1
            except OSError:
                remaining_jobs.append((fp, jid, comp_time))
        else:
            remaining_jobs.append((fp, jid, comp_time))

    # 4. Prune excess if total completed jobs exceed max_jobs
    if len(remaining_jobs) > max_jobs:
        remaining_jobs.sort(key=lambda x: x[2])  # oldest first
        excess = len(remaining_jobs) - max_jobs
        for i in range(excess):
            fp, jid, _ = remaining_jobs[i]
            try:
                os.remove(fp)
                with JOB_LOCK:
                    JOB_CACHE.pop(jid, None)
                pruned_jobs += 1
            except OSError:
                pass

    return {"pruned_jobs": pruned_jobs, "pruned_temps": pruned_temps}

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

    # Invalidate all active sessions for this user atomically under SESSION_LOCK
    _revoke_sessions_for_user(email)

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

SESSION_LOCK = threading.RLock()

def _load_sessions_unlocked():
    if not os.path.isfile(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_sessions_unlocked(sessions):
    _atomic_json_write(SESSIONS_FILE, sessions)

def _load_sessions():
    with SESSION_LOCK:
        return _load_sessions_unlocked()

def _save_sessions(sessions):
    with SESSION_LOCK:
        _save_sessions_unlocked(sessions)

def _mutate_sessions(mutator_fn):
    """Transactionally load, mutate, and save sessions under SESSION_LOCK.

    Guarantees atomic read-modify-write semantics across concurrent threads.
    """
    with SESSION_LOCK:
        sessions = _load_sessions_unlocked()
        result = mutator_fn(sessions)
        _save_sessions_unlocked(sessions)
        return result

def _add_session(token: str, email: str, user_id: str):
    """Atomically insert a new session and prune expired sessions under SESSION_LOCK."""
    def _add(sessions):
        now = time.time()
        for k in list(sessions.keys()):
            exp = sessions[k].get("expires_at", sessions[k].get("created_at", 0) + SESSION_TTL)
            if exp <= now:
                sessions.pop(k, None)
        sessions[token] = {
            "email": email.strip().lower(),
            "user_id": user_id,
            "created_at": now,
            "expires_at": now + SESSION_TTL
        }
        return token
    return _mutate_sessions(_add)

def _revoke_session(token: str):
    """Atomically remove a session token and prune expired sessions under SESSION_LOCK."""
    def _revoke(sessions):
        revoked = False
        if token and token in sessions:
            sessions.pop(token, None)
            revoked = True
        now = time.time()
        for k in list(sessions.keys()):
            exp = sessions[k].get("expires_at", sessions[k].get("created_at", 0) + SESSION_TTL)
            if exp <= now:
                sessions.pop(k, None)
        return revoked
    return _mutate_sessions(_revoke)

def _revoke_sessions_for_user(email: str):
    """Atomically invalidate all active sessions for a specific user under SESSION_LOCK."""
    def _revoke_all(sessions):
        target = (email or "").strip().lower()
        count = 0
        now = time.time()
        for k in list(sessions.keys()):
            exp = sessions[k].get("expires_at", sessions[k].get("created_at", 0) + SESSION_TTL)
            if exp <= now:
                sessions.pop(k, None)
            elif sessions[k].get("email", "").strip().lower() == target:
                sessions.pop(k, None)
                count += 1
        return count
    return _mutate_sessions(_revoke_all)


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
    try:
        _update_durable_job(job_id, {
            "status": "working",
            "progress_percent": 15,
            "stage": "Scraping Google Maps listings...",
            "started_at": time.time(),
            "leads": [],
            "error": None
        })

        # Poll scraper service
        max_wait = params.get("max_time", JOB_TIMEOUT_SECONDS)
        start_time = time.time()
        completed = False

        while time.time() - start_time < max_wait:
            with JOB_LOCK:
                if JOB_CACHE.get(job_id, {}).get("cancel_requested"):
                    _update_durable_job(job_id, {
                        "status": "cancelled",
                        "stage": "Extraction cancelled by user.",
                        "completed_at": time.time(),
                        "error": "Cancelled by user"
                    })
                    return

            try:
                _, raw = scraper_req("GET", f"/api/v1/jobs/{job_id}", timeout=10)
                res = json.loads(raw.decode())
                status = res.get("Status")

                elapsed = int(time.time() - start_time)
                # Estimate progress based on elapsed time (up to 85% during wait)
                progress = min(85, 15 + int(elapsed * 2))

                _update_durable_job(job_id, {
                    "status": status if status in ("ok", "failed") else "working",
                    "progress_percent": progress,
                    "stage": f"Scraping listings on Google Maps ({elapsed}s elapsed)..."
                })

                if status == "ok":
                    completed = True
                    break
                elif status == "failed":
                    _update_durable_job(job_id, {
                        "status": "failed",
                        "stage": "Scraper service failed",
                        "error": "Scraper service returned failed status. IP may be temporarily throttled.",
                        "completed_at": time.time()
                    })
                    return
            except Exception as e:
                print(f"[poll error] {e}")

            time.sleep(4)

        if not completed:
            _update_durable_job(job_id, {
                "status": "failed",
                "stage": "Job timed out",
                "error": "Job timed out waiting for scraper completion.",
                "completed_at": time.time()
            })
            return

        # Download CSV
        _update_durable_job(job_id, {
            "stage": "Downloading and parsing listings...",
            "progress_percent": 90
        })

        try:
            _, raw_csv = scraper_req("GET", f"/api/v1/jobs/{job_id}/download", timeout=60)
            csv_text = raw_csv.decode("utf-8", "replace")
            rows = list(csv.DictReader(io.StringIO(csv_text)))
        except Exception as dl_err:
            _update_durable_job(job_id, {
                "status": "failed",
                "stage": "Download failed",
                "error": f"Failed to download scrape results: {str(dl_err)[:200]}",
                "completed_at": time.time()
            })
            return

        # Cleanse and score
        _update_durable_job(job_id, {
            "stage": "Cleansing emails, formatting phones, and calculating lead scores...",
            "progress_percent": 95
        })

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
        job_user_id = params.get("user_id")
        skipped_previous = []
        if exclude_seen:
            historical_leads = load_historical_leads(DATA_DIR, exclude_job_id=job_id, user_id=job_user_id)
            new_leads, skipped_previous = filter_previously_seen_leads(cleaned_leads, historical_leads)
            cleaned_leads = new_leads
        else:
            historical_leads = load_historical_leads(DATA_DIR, exclude_job_id=job_id, user_id=job_user_id)
            _, skipped_previous = filter_previously_seen_leads(cleaned_leads, historical_leads)

        # Enrich social profiles (Instagram / Facebook / LinkedIn) from websites
        if params.get("socials", True) and cleaned_leads:
            _update_durable_job(job_id, {
                "stage": "Scanning websites for Instagram, Facebook, and LinkedIn profiles...",
                "progress_percent": 96
            })
            cleaned_leads = enrich_socials(cleaned_leads)

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
                _update_durable_job(job_id, {
                    "stage": "Syncing qualified leads and history to Supabase Cloud..."
                })
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

        metrics = {
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

        _update_durable_job(job_id, {
            "status": "ok",
            "progress_percent": 100,
            "stage": stage_desc,
            "completed_at": time.time(),
            "leads": cleaned_leads,
            "skipped_previous_count": len(skipped_previous),
            "metrics": metrics,
            "error": None
        })

    except Exception as e:
        safe_err = f"Processing error: {str(e)[:200]}"
        print(f"[background processor unhandled error] {e}", file=sys.stderr)
        _update_durable_job(job_id, {
            "status": "failed",
            "stage": "Processing error",
            "completed_at": time.time(),
            "error": safe_err
        })
    finally:
        # Safe termination of any registered subprocesses for this job
        _terminate_job_processes(job_id)


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
                "active_jobs_count": _get_active_job_count(),
                "max_concurrent_jobs": MAX_CONCURRENT_JOBS
            })
            return

        if path == "/api/diagnostics":
            user = self._require_auth()
            if not user:
                return

            try:
                rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                rss_bytes = rss_kb * 1024
            except Exception:
                rss_bytes = 0

            data_jobs_count = 0
            data_dir_size = 0
            try:
                if os.path.isdir(DATA_DIR):
                    for fn in os.listdir(DATA_DIR):
                        fp = os.path.join(DATA_DIR, fn)
                        if os.path.isfile(fp):
                            data_dir_size += os.path.getsize(fp)
                            if fn.startswith("leads_") and fn.endswith(".json"):
                                data_jobs_count += 1
            except Exception:
                pass

            disk_free_bytes = 0
            try:
                vfs = os.statvfs(DATA_DIR)
                disk_free_bytes = vfs.f_bavail * vfs.f_frsize
            except Exception:
                pass

            self._send_json({
                "status": "ok",
                "uptime_seconds": int(time.time() - SERVER_START_TIME),
                "active_jobs_count": _get_active_job_count(),
                "max_concurrent_jobs": MAX_CONCURRENT_JOBS,
                "data_jobs_count": data_jobs_count,
                "data_dir_size_bytes": data_dir_size,
                "disk_free_bytes": disk_free_bytes,
                "scraper_up": check_scraper_health(),
                "scraper_base": SCRAPER_BASE,
                "supabase_configured": is_supabase_configured(),
                "memory_rss_bytes": rss_bytes
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
            user = self._require_auth()
            if not user:
                return
            current_uid = user.get("id")
            # List past and current jobs
            jobs = []
            with JOB_LOCK:
                for j in JOB_CACHE.values():
                    j_uid = j.get("user_id") or j.get("params", {}).get("user_id")
                    if j_uid and j_uid != current_uid:
                        continue
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
                                file_uid = d.get("user_id") or d.get("params", {}).get("user_id")
                                if file_uid and file_uid != current_uid:
                                    continue
                                leads = d.get("leads", [])
                                jobs.append({
                                    "id": jid,
                                    "status": d.get("status", "ok"),
                                    "stage": d.get("stage", "Loaded from archive"),
                                    "progress_percent": d.get("progress_percent", 100),
                                    "started_at": d.get("started_at", d.get("completed_at", 0)),
                                    "keyword": d.get("params", {}).get("keyword", ""),
                                    "city": d.get("params", {}).get("city", ""),
                                    "metrics": d.get("metrics") or {
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
            user = self._require_auth()
            if not user:
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
                            "user_id": d.get("user_id"),
                            "status": d.get("status", "ok"),
                            "stage": d.get("stage", "Completed"),
                            "progress_percent": d.get("progress_percent", 100),
                            "leads": leads,
                            "created_at": d.get("created_at"),
                            "updated_at": d.get("updated_at"),
                            "started_at": d.get("started_at"),
                            "completed_at": d.get("completed_at"),
                            "error": d.get("error"),
                            "params": d.get("params", {}),
                            "metrics": d.get("metrics") or {
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

            job_owner = job_info.get("user_id") or job_info.get("params", {}).get("user_id")
            if job_owner and job_owner != user.get("id"):
                self._send_json({"error": "Forbidden: You do not have permission to access this job"}, 403)
                return

            self._send_json(job_info)
            return

        if path.startswith("/api/job/") and path.endswith("/leads"):
            user = self._require_auth()
            if not user:
                return
            parts = path.split("/")
            if len(parts) < 4:
                self._send_json({"error": "Invalid job ID"}, 400)
                return
            job_id = parts[3]
            if not _validate_job_id(job_id):
                self._send_json({"error": "Invalid job ID format"}, 400)
                return

            job_owner = _get_job_owner_id(job_id)
            if job_owner and job_owner != user.get("id"):
                self._send_json({"error": "Forbidden: You do not have permission to access this job"}, 403)
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
            user = self._require_auth()
            if not user:
                return
            job_id = query.get("job_id", [""])[0].strip()
            if not _validate_job_id(job_id):
                self._send_json({"error": "Invalid or missing job ID"}, 400)
                return

            job_owner = _get_job_owner_id(job_id)
            if job_owner and job_owner != user.get("id"):
                self._send_json({"error": "Forbidden: You do not have permission to export this job"}, 403)
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

            elif fmt in ("whatsapp", "wa"):
                # Formatted specifically for external WhatsApp portal ingestion
                output = io.StringIO()
                wa_fields = [
                    "phone", "name", "category", "rating", "address", "website",
                    "custom_pitch", "consent_source", "whatsapp_status"
                ]
                writer = csv.DictWriter(output, fieldnames=wa_fields)
                writer.writeheader()
                for l in leads:
                    wa_phone = l.get("whatsapp_phone_e164") or l.get("clean_phone") or l.get("phone", "")
                    if not wa_phone:
                        continue
                    title = l.get("title", "")
                    cat = l.get("category", "")
                    rating = str(l.get("review_rating", ""))
                    addr = l.get("address", "")
                    pitch = f"Hi {title}, noticed your business in {addr}. We help {cat} businesses acquire qualified clients."
                    writer.writerow({
                        "phone": sanitize_csv_cell(wa_phone),
                        "name": sanitize_csv_cell(title),
                        "category": sanitize_csv_cell(cat),
                        "rating": sanitize_csv_cell(rating),
                        "address": sanitize_csv_cell(addr),
                        "website": sanitize_csv_cell(l.get("website", "")),
                        "custom_pitch": sanitize_csv_cell(pitch),
                        "consent_source": sanitize_csv_cell(l.get("consent_source", "public_directory")),
                        "whatsapp_status": sanitize_csv_cell(l.get("whatsapp_status", "eligible"))
                    })
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{filename_base}_whatsapp_portal.csv"')
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
            _add_session(token, email, user_id)

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
            _add_session(token, email, user.get("id"))

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
            if token:
                _revoke_session(token)
            self._send_json({"success": True, "message": "Logged out successfully."})
            return

        if path == "/api/scrape/start":
            user = self._require_auth()
            if not user:
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

            # Bounded job admission under JOB_ADMISSION_LOCK
            with JOB_ADMISSION_LOCK:
                if _get_active_job_count() >= MAX_CONCURRENT_JOBS:
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Retry-After", "30")
                    cors_origin = self._get_cors_origin()
                    if cors_origin:
                        self.send_header("Access-Control-Allow-Origin", cors_origin)
                        self.send_header("Access-Control-Allow-Credentials", "true")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "error": f"Maximum concurrent jobs limit reached ({MAX_CONCURRENT_JOBS}). Please wait for an active job to finish."
                    }).encode("utf-8"))
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
                    "max_time": JOB_TIMEOUT_SECONDS
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

                # Initialize persistent durable job record on disk and memory
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
                    "max_time": JOB_TIMEOUT_SECONDS,
                    "user_id": user.get("id"),
                    "user_email": user.get("email")
                }
                _create_durable_job(job_id, user.get("id"), user.get("email"), job_params)

            # Launch background poll & processing thread with owner identification
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
            user = self._require_auth()
            if not user:
                return
            params = self._read_json_body()
            if params is None:
                return
            job_id = params.get("job_id", "")
            if not job_id or not _validate_job_id(job_id):
                self._send_json({"error": "Invalid or missing job_id"}, 400)
                return

            job_owner = _get_job_owner_id(job_id)
            if job_owner and job_owner != user.get("id"):
                self._send_json({"error": "Forbidden: You do not have permission to cancel this job"}, 403)
                return

            with JOB_LOCK:
                if job_id in JOB_CACHE:
                    JOB_CACHE[job_id]["cancel_requested"] = True

            _update_durable_job(job_id, {
                "status": "cancelled",
                "stage": "Extraction cancelled by user.",
                "completed_at": time.time(),
                "error": "Cancelled by user"
            })

            # Terminate any child subprocesses associated with this job
            _terminate_job_processes(job_id)

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
            user = self._require_auth()
            if not user:
                return
            body = self._read_json_body()
            if body is None:
                return
            job_id = body.get("job_id", "")
            if job_id and not _validate_job_id(job_id):
                self._send_json({"error": "Invalid job_id format"}, 400)
                return
            if job_id:
                job_owner = _get_job_owner_id(job_id)
                if job_owner and job_owner != user.get("id"):
                    self._send_json({"error": "Forbidden: You do not have permission to sync this job"}, 403)
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
    valid, err_msg = validate_environment()
    if not valid:
        logger.error(f"Configuration error: {err_msg}")
        print(f"❌ Configuration error: {err_msg}", file=sys.stderr)
        sys.exit(1)

    global _ACTIVE_SERVER_INSTANCE

    # Register OS signal handlers for graceful shutdown (systemd SIGTERM / Ctrl+C SIGINT)
    try:
        signal.signal(signal.SIGTERM, graceful_shutdown_signal_handler)
        signal.signal(signal.SIGINT, graceful_shutdown_signal_handler)
    except (ValueError, AttributeError):
        pass

    # Phase 2 Reliability: Run restart recovery and retention cleanup on startup
    interrupted = recover_interrupted_jobs(DATA_DIR)
    if interrupted:
        print(f"🔄 Recovered {interrupted} interrupted job(s) from previous session.")
    cleanup_info = cleanup_stale_jobs_and_temp_files(DATA_DIR)
    if cleanup_info.get("pruned_jobs") or cleanup_info.get("pruned_temps"):
        print(f"🧹 Cleaned up {cleanup_info.get('pruned_jobs')} stale job(s) and {cleanup_info.get('pruned_temps')} temp file(s).")

    server = ThreadingHTTPServer((HOST, PORT), DashboardRequestHandler)
    _ACTIVE_SERVER_INSTANCE = server
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
    except (KeyboardInterrupt, SystemExit):
        print("\nShutting down dashboard server...")
        server.shutdown()


if __name__ == "__main__":
    run_server()

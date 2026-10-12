#!/usr/bin/env python3
"""Supabase Client for LeadMap Pro.

Zero third-party dependencies — uses Python standard library (urllib + json).
Communicates with Supabase via the PostgREST REST API.
"""
import json
import os
import sys
import uuid
import urllib.error
import urllib.parse
import urllib.request

# Dynamic reload for .env
def reload_env():
    """Read .env file and update os.environ."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.isfile(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

reload_env()


def get_config():
    """Retrieve Supabase URL and Key from environment variables."""
    reload_env()
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "").strip()
    table = os.environ.get("SUPABASE_TABLE", "leads").strip()
    return url, key, table


def is_configured():
    """Check if Supabase credentials are set."""
    url, key, _ = get_config()
    return bool(url and key and url.startswith("http"))


def disconnect():
    """Clear Supabase credentials from .env and os.environ, reverting to Local DB."""
    os.environ.pop("SUPABASE_URL", None)
    os.environ.pop("SUPABASE_KEY", None)
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.isfile(env_file):
        lines = []
        with open(env_file) as f:
            for line in f:
                if line.startswith("SUPABASE_URL="):
                    lines.append("SUPABASE_URL=\n")
                elif line.startswith("SUPABASE_KEY="):
                    lines.append("SUPABASE_KEY=\n")
                else:
                    lines.append(line)
        with open(env_file, "w") as f:
            f.writelines(lines)
    return True


def is_likely_anon_key(key: str) -> bool:
    """Check if key is a public publishable/anon key rather than service_role key."""
    if not key or not isinstance(key, str):
        return False
    if key.startswith("sb_publishable_"):
        return True
    parts = key.split(".")
    if len(parts) == 3:
        try:
            import base64
            payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8"))
            if payload.get("role") == "anon":
                return True
        except Exception:
            pass
    return False


def test_connection(supabase_url=None, supabase_key=None, table=None):
    """Test connection to Supabase PostgREST endpoint."""
    reload_env()
    cfg_url, cfg_key, cfg_table = get_config()
    url = (supabase_url or cfg_url).strip().rstrip("/")
    key = (supabase_key or cfg_key).strip()
    tbl = (table or cfg_table or "leads").strip()

    if not (url and key):
        return False, "Missing Supabase URL or API Key."

    if not url or not isinstance(url, str):
        return False, "Supabase URL is required."

    try:
        parsed = urllib.parse.urlparse(url.strip())
    except Exception as e:
        return False, f"Invalid Supabase URL: {e}"

    if parsed.scheme.lower() not in ("http", "https"):
        return False, "Supabase URL must start with http:// or https://"

    hostname = parsed.hostname
    if not hostname:
        return False, "Supabase URL must contain a valid domain or host."

    clean_host = hostname.strip().lower()
    if clean_host in ("169.254.169.254", "169.254.170.2", "metadata.google.internal", "metadata.google", "instance-data", "metadata") or clean_host.endswith(".internal"):
        return False, "Restricted destination URL: cloud metadata destinations are blocked."

    if key.startswith("http"):
        return False, "Your SUPABASE_KEY appears to be a URL instead of an API Key! Please copy the private 'service_role' secret key from Supabase Project Settings -> API."

    if is_likely_anon_key(key):
        return False, "The provided key is a public 'anon' key. Hardened database tables require the server-side 'service_role' secret key (Project Settings -> API -> Project API keys) to bypass RLS and sync backend leads."

    endpoint = f"{url}/rest/v1/{tbl}?limit=1"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "LeadMap-Pro-Supabase/1.0"
    }

    try:
        req = urllib.request.Request(endpoint, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 206):
                return True, f"Successfully connected to Supabase table '{tbl}'!"
            return False, f"Unexpected response status: {resp.status}"
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", "replace")
        if e.code == 404:
            return False, f"Table '{tbl}' not found in Supabase. Have you run supabase_schema.sql in your Supabase SQL Editor?"
        elif e.code in (401, 403):
            if is_likely_anon_key(key):
                return False, f"Authentication error (HTTP {e.code}): Access denied. The provided key is a public 'anon' key. Use your private 'service_role' key."
            return False, f"Authentication error (HTTP {e.code}). Verify your Supabase service_role API Key."
        return False, f"HTTP Error {e.code}: {err_msg}"
    except Exception as e:
        return False, f"Connection error: {str(e)}"

# Alias for backwards compatibility
test_supabase_connection = test_connection


def format_lead_for_supabase(lead: dict, job_id: str = "") -> dict:
    """Format and cast lead fields to match the Supabase table schema."""
    # Rating
    rating = None
    if lead.get("review_rating") not in (None, ""):
        try:
            rating = round(float(lead["review_rating"]), 2)
        except (ValueError, TypeError):
            rating = None

    # Review Count
    reviews = 0
    if lead.get("review_count") not in (None, ""):
        try:
            reviews = int(lead["review_count"])
        except (ValueError, TypeError):
            reviews = 0

    # Score
    score = 0
    if lead.get("lead_score") not in (None, ""):
        try:
            score = int(lead["lead_score"])
        except (ValueError, TypeError):
            score = 0

    # Score reasons array
    reasons = lead.get("score_reasons") or []
    if isinstance(reasons, str):
        reasons = [r.strip() for r in reasons.split(",") if r.strip()]

    phone_val = lead.get("clean_phone") or lead.get("phone") or ""
    domain_val = lead.get("domain") or ""
    title_val = lead.get("title") or "Unknown Business"
    addr_val = lead.get("address") or ""
    unique_key = f"{phone_val}|{domain_val}" if (phone_val and domain_val) else f"{title_val}|{addr_val}"
    record_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, unique_key))

    return {
        "id": record_id,
        "job_id": job_id or lead.get("job_id") or "",
        "title": title_val,
        "category": lead.get("category") or "",
        "clean_phone": lead.get("clean_phone") or lead.get("phone") or "",
        "phone": lead.get("phone") or "",
        "emails": lead.get("emails") or "",
        "website": lead.get("website") or "",
        "domain": lead.get("domain") or "",
        "address": lead.get("address") or "",
        "review_rating": rating,
        "review_count": reviews,
        "latitude": str(lead.get("latitude") or ""),
        "longitude": str(lead.get("longitude") or ""),
        "lead_score": score,
        "lead_tier": lead.get("lead_tier") or "COLD",
        "score_reasons": reasons,
        "instagram": lead.get("instagram") or "",
        "facebook": lead.get("facebook") or "",
        "linkedin": lead.get("linkedin") or ""
    }


def sync_leads(leads, job_id="", supabase_url=None, supabase_key=None, table=None, batch_size=50, max_retries=3, retry_delay=0.5):
    """Sync a list of leads to Supabase using safe, idempotent PostgREST upsert (on_conflict=id).

    Guarantees:
    - Never pre-deletes existing rows before replacement inserts are confirmed.
    - Idempotently merges/updates records by deterministic primary key (id).
    - Deduplicates records in-memory before POST to prevent PostgreSQL cardinality errors.
    - Retries transient failures (timeouts, HTTP 500/502/503/504) with backoff.
    - Returns structured write confirmation and batch diagnostic metadata.
    """
    import time
    url = (supabase_url or os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = (supabase_key or os.environ.get("SUPABASE_KEY", "")).strip()
    table_name = table or os.environ.get("SUPABASE_TABLE", "leads").strip()

    if not (url and key):
        return {"success": False, "error": "Supabase credentials not configured in .env."}

    if not leads:
        return {"success": True, "count": 0, "confirmed_count": 0, "message": "No leads to sync."}

    # 1. Format leads and deduplicate by deterministic record_id to avoid PostgreSQL cardinality errors
    formatted_records = [format_lead_for_supabase(l, job_id=job_id) for l in leads]
    seen_ids = set()
    unique_records = []
    for r in formatted_records:
        rec_id = r.get("id")
        if rec_id and rec_id not in seen_ids:
            seen_ids.add(rec_id)
            unique_records.append(r)
        elif not rec_id:
            unique_records.append(r)

    total_records = len(unique_records)
    if total_records == 0:
        return {"success": True, "count": 0, "confirmed_count": 0, "message": "No valid leads to sync."}

    # Explicit conflict target on primary key 'id'
    endpoint = f"{url}/rest/v1/{table_name}?on_conflict=id"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
        "User-Agent": "LeadMap-Pro-Supabase/1.0"
    }

    chunks = [unique_records[i : i + batch_size] for i in range(0, total_records, batch_size)]
    total_batches = len(chunks)
    total_synced = 0

    # 2. Process each batch with safe retry logic
    for batch_idx, chunk in enumerate(chunks):
        data = json.dumps(chunk).encode("utf-8")
        batch_success = False
        last_err = None
        last_code = None

        for attempt in range(max_retries):
            req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status in (200, 201):
                        inserted = json.loads(resp.read().decode())
                        confirmed_count = len(inserted) if isinstance(inserted, list) else len(chunk)
                        total_synced += confirmed_count
                        batch_success = True
                        break
                    else:
                        total_synced += len(chunk)
                        batch_success = True
                        break
            except urllib.error.HTTPError as e:
                last_code = e.code
                err_body = e.read().decode("utf-8", "replace")
                last_err = f"HTTP {e.code}: {err_body}"

                # Retry transient gateway / server errors
                is_transient = e.code in (500, 502, 503, 504)
                if is_transient and attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
                else:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = str(e)
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
                else:
                    break

        if not batch_success:
            print(f"[Supabase sync error] Batch {batch_idx + 1}/{total_batches} failed: {last_err}", file=sys.stderr)
            return {
                "success": False,
                "error": last_err or "Unknown batch transmission failure",
                "confirmed_count": total_synced,
                "synced_before_error": total_synced,
                "failed_batch_index": batch_idx,
                "total_batches": total_batches,
                "total_records": total_records,
                "retryable": (last_code in (500, 502, 503, 504)) if last_code else True
            }

    return {
        "success": True,
        "count": total_synced,
        "confirmed_count": total_synced,
        "total_records": total_records,
        "total_batches": total_batches,
        "message": f"Successfully synced {total_synced} leads to Supabase table '{table_name}'."
    }


def save_scrape_job_to_supabase(job_info: dict, supabase_url=None, supabase_key=None, history_table="scrape_history"):
    """Store scrape job metadata into Supabase scrape_history table."""
    import time
    url = (supabase_url or os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = (supabase_key or os.environ.get("SUPABASE_KEY", "")).strip()

    if not (url and key):
        return {"success": False, "error": "Supabase credentials not configured in .env."}

    job_id = job_info.get("id") or job_info.get("job_id")
    if not job_id:
        return {"success": False, "error": "Missing job id."}

    metrics = job_info.get("metrics") or {}
    params = job_info.get("params") or {}
    keyword = job_info.get("keyword") or params.get("keyword") or ""
    city = job_info.get("city") or params.get("city") or ""
    depth = int(job_info.get("depth") or params.get("depth") or 5)
    status = job_info.get("status") or "ok"
    stage = job_info.get("stage") or "Completed"
    started_at = int(job_info.get("started_at") or time.time())
    completed_at = int(job_info.get("completed_at") or time.time())
    leads_count = int(metrics.get("total") or len(job_info.get("leads") or []))

    record = {
        "id": str(job_id),
        "started_at": started_at,
        "completed_at": completed_at,
        "keyword": keyword,
        "city": city,
        "depth": depth,
        "status": status,
        "stage": stage,
        "leads_count": leads_count,
        "hot_count": int(metrics.get("hot") or 0),
        "warm_count": int(metrics.get("warm") or 0),
        "cold_count": int(metrics.get("cold") or 0),
        "email_count": int(metrics.get("with_email") or 0),
        "phone_count": int(metrics.get("with_phone") or 0),
        "skipped_previous_count": int(metrics.get("skipped_previous") or 0),
        "metrics": metrics,
        "params": params
    }

    endpoint = f"{url}/rest/v1/{history_table}?on_conflict=id"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
        "User-Agent": "LeadMap-Pro-Supabase/1.0"
    }

    data = json.dumps([record]).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                return {"success": True, "message": f"Job {job_id} saved to Supabase '{history_table}'."}
            return {"success": True, "message": f"Status: {resp.status}"}
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", "replace")
        if e.code == 404:
            return {"success": False, "error": f"Table '{history_table}' does not exist in Supabase yet. Run updated supabase_schema.sql in Supabase SQL editor.", "table_missing": True}
        return {"success": False, "error": f"HTTP {e.code}: {err_msg}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def fetch_history_from_supabase(supabase_url=None, supabase_key=None, history_table="scrape_history"):
    """Retrieve past scrape jobs from Supabase.
    
    Tries public.scrape_history table first. If not found (404),
    falls back to aggregating past jobs directly from public.leads table.
    """
    url = (supabase_url or os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = (supabase_key or os.environ.get("SUPABASE_KEY", "")).strip()

    if not (url and key):
        return []

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "LeadMap-Pro-Supabase/1.0"
    }

    # 1. Try dedicated history table
    endpoint = f"{url}/rest/v1/{history_table}?select=*&order=started_at.desc&limit=100"
    try:
        req = urllib.request.Request(endpoint, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status in (200, 206):
                rows = json.loads(resp.read().decode("utf-8"))
                jobs = []
                for r in rows:
                    jobs.append({
                        "id": r.get("id"),
                        "status": r.get("status") or "ok",
                        "stage": r.get("stage") or "Loaded from Supabase Cloud",
                        "progress_percent": 100,
                        "started_at": r.get("started_at") or 0,
                        "completed_at": r.get("completed_at") or 0,
                        "keyword": r.get("keyword") or "",
                        "city": r.get("city") or "",
                        "metrics": r.get("metrics") or {
                            "total": r.get("leads_count") or 0,
                            "hot": r.get("hot_count") or 0,
                            "warm": r.get("warm_count") or 0,
                            "cold": r.get("cold_count") or 0,
                            "with_email": r.get("email_count") or 0,
                            "with_phone": r.get("phone_count") or 0
                        },
                        "params": r.get("params") or {},
                        "source": "supabase_history_table"
                    })
                return jobs
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"[Supabase history fetch error] HTTP {e.code}", file=sys.stderr)
    except Exception as e:
        print(f"[Supabase history fetch error] {e}", file=sys.stderr)

    # 2. Fallback: Aggregate from public.leads table
    try:
        leads_endpoint = f"{url}/rest/v1/leads?select=job_id,created_at,category,title,clean_phone,emails,lead_tier,address&order=created_at.desc&limit=1000"
        req = urllib.request.Request(leads_endpoint, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 206):
                leads = json.loads(resp.read().decode("utf-8"))
                groups = {}
                for l in leads:
                    jid = l.get("job_id") or "unassigned"
                    if jid not in groups:
                        groups[jid] = {
                            "id": jid,
                            "leads": [],
                            "created_at": l.get("created_at"),
                            "category": l.get("category") or "",
                            "sample_title": l.get("title") or ""
                        }
                    groups[jid]["leads"].append(l)

                import datetime
                jobs = []
                for jid, g in groups.items():
                    gleads = g["leads"]
                    ts = 0
                    if g.get("created_at"):
                        try:
                            clean_dt = g["created_at"].replace("Z", "+00:00")
                            dt = datetime.datetime.fromisoformat(clean_dt)
                            ts = int(dt.timestamp())
                        except Exception:
                            ts = 0

                    jobs.append({
                        "id": jid,
                        "status": "ok",
                        "stage": "Synced in Supabase Cloud",
                        "progress_percent": 100,
                        "started_at": ts,
                        "keyword": g.get("category") or g.get("sample_title") or "Leads Search",
                        "city": "Cloud Archive",
                        "metrics": {
                            "total": len(gleads),
                            "hot": sum(1 for x in gleads if x.get("lead_tier") == "HOT"),
                            "warm": sum(1 for x in gleads if x.get("lead_tier") == "WARM"),
                            "cold": sum(1 for x in gleads if x.get("lead_tier") == "COLD"),
                            "with_email": sum(1 for x in gleads if x.get("emails")),
                            "with_phone": sum(1 for x in gleads if x.get("clean_phone"))
                        },
                        "source": "supabase_leads_table"
                    })
                return jobs
    except Exception as e:
        print(f"[Supabase leads fallback error] {e}", file=sys.stderr)

    return []


def fetch_leads_for_job_from_supabase(job_id: str, supabase_url=None, supabase_key=None):
    """Retrieve full lead records from Supabase for a given job_id."""
    url = (supabase_url or os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = (supabase_key or os.environ.get("SUPABASE_KEY", "")).strip()

    if not (url and key and job_id):
        return []

    endpoint = f"{url}/rest/v1/leads?job_id=eq.{urllib.parse.quote(str(job_id))}&order=lead_score.desc"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "LeadMap-Pro-Supabase/1.0"
    }

    try:
        req = urllib.request.Request(endpoint, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 206):
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[Supabase fetch leads error] {e}", file=sys.stderr)
    return []


def sync_all_history_to_supabase(data_dir: str):
    """Iterate through all local data/leads_*.json files and upload both the job history and the leads to Supabase."""
    import glob
    results = {"jobs_synced": 0, "leads_synced": 0, "errors": []}
    if not data_dir or not os.path.isdir(data_dir):
        return results

    for filepath in sorted(glob.glob(os.path.join(data_dir, "leads_*.json")), reverse=True):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            job_id = data.get("job_id")
            if not job_id:
                fn = os.path.basename(filepath)
                job_id = fn[6:-5]

            leads = data.get("leads", [])
            params = data.get("params", {})
            completed_at = data.get("completed_at", 0)

            # Sync leads
            if leads:
                l_res = sync_leads(leads, job_id=job_id)
                if l_res.get("success"):
                    results["leads_synced"] += l_res.get("count", len(leads))

            # Sync history job
            j_info = {
                "id": job_id,
                "keyword": params.get("keyword", ""),
                "city": params.get("city", ""),
                "depth": params.get("depth", 5),
                "status": "ok",
                "stage": "Loaded from archive",
                "started_at": completed_at,
                "completed_at": completed_at,
                "metrics": {
                    "total": len(leads),
                    "hot": sum(1 for l in leads if l.get("lead_tier") == "HOT"),
                    "warm": sum(1 for l in leads if l.get("lead_tier") == "WARM"),
                    "cold": sum(1 for l in leads if l.get("lead_tier") == "COLD"),
                    "with_email": sum(1 for l in leads if l.get("emails")),
                    "with_phone": sum(1 for l in leads if l.get("clean_phone"))
                },
                "params": params
            }
            save_scrape_job_to_supabase(j_info)
            results["jobs_synced"] += 1
        except Exception as e:
            results["errors"].append(f"{os.path.basename(filepath)}: {str(e)}")

    return results


def save_user_to_supabase(user_obj: dict, supabase_url=None, supabase_key=None):
    """Store or update user profile in Supabase 'users' table."""
    url = (supabase_url or os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = (supabase_key or os.environ.get("SUPABASE_KEY", "")).strip()

    if not (url and key):
        return {"success": False, "error": "Supabase credentials not configured."}

    endpoint = f"{url}/rest/v1/users?on_conflict=email"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
        "User-Agent": "LeadMap-Pro-Supabase/1.0"
    }

    record = {
        "id": user_obj.get("id"),
        "email": user_obj.get("email"),
        "name": user_obj.get("name"),
        "password_hash": user_obj.get("password_hash"),
        "raw_user_meta": {
            "created_at": user_obj.get("created_at")
        }
    }

    try:
        data = json.dumps([record]).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                return {"success": True, "data": json.loads(resp.read().decode())}
            return {"success": True}
    except Exception as e:
        print(f"[Supabase save user error] {e}", file=sys.stderr)
        return {"success": False, "error": str(e)}


def get_user_from_supabase_by_email(email: str, supabase_url=None, supabase_key=None):
    """Fetch user record from Supabase by email."""
    url = (supabase_url or os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = (supabase_key or os.environ.get("SUPABASE_KEY", "")).strip()

    if not (url and key and email):
        return None

    endpoint = f"{url}/rest/v1/users?email=eq.{urllib.parse.quote(str(email))}&limit=1"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "LeadMap-Pro-Supabase/1.0"
    }

    try:
        req = urllib.request.Request(endpoint, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 206):
                rows = json.loads(resp.read().decode("utf-8"))
                if rows and isinstance(rows, list) and len(rows) > 0:
                    r = rows[0]
                    meta = r.get("raw_user_meta") or {}
                    return {
                        "id": r.get("id"),
                        "email": r.get("email"),
                        "name": r.get("name"),
                        "password_hash": r.get("password_hash"),
                        "created_at": meta.get("created_at") or 0
                    }
    except Exception as e:
        print(f"[Supabase get user error] {e}", file=sys.stderr)
    return None


def fetch_all_users_from_supabase(supabase_url=None, supabase_key=None):
    """Retrieve dictionary of all registered users from Supabase keyed by email."""
    url = (supabase_url or os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = (supabase_key or os.environ.get("SUPABASE_KEY", "")).strip()

    if not (url and key):
        return {}

    endpoint = f"{url}/rest/v1/users?select=*"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "LeadMap-Pro-Supabase/1.0"
    }

    try:
        req = urllib.request.Request(endpoint, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 206):
                rows = json.loads(resp.read().decode("utf-8"))
                users_map = {}
                for r in rows:
                    email = r.get("email")
                    if email:
                        meta = r.get("raw_user_meta") or {}
                        users_map[email] = {
                            "id": r.get("id"),
                            "email": email,
                            "name": r.get("name"),
                            "password_hash": r.get("password_hash"),
                            "created_at": meta.get("created_at") or 0
                        }
                return users_map
    except Exception as e:
        print(f"[Supabase fetch all users error] {e}", file=sys.stderr)
    return {}


# ── scrape_jobs Table Integration (Phase 2 & 3: Telegram Bot) ────────────────

def create_scrape_job_record(job_info: dict, supabase_url=None, supabase_key=None) -> dict:
    """Create a new job tracking record in public.scrape_jobs via PostgREST."""
    url = (supabase_url or os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = (supabase_key or os.environ.get("SUPABASE_KEY", "")).strip()

    if not (url and key):
        return {"success": False, "error": "Supabase not configured"}

    job_id = job_info.get("id") or str(uuid.uuid4())
    endpoint = f"{url}/rest/v1/scrape_jobs"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
        "User-Agent": "LeadMap-Pro-Supabase/1.0"
    }

    record = {
        "id": job_id,
        "scraper_job_id": job_info.get("scraper_job_id") or job_info.get("id") or "",
        "telegram_user_id": str(job_info.get("telegram_user_id") or ""),
        "telegram_chat_id": str(job_info.get("telegram_chat_id") or ""),
        "telegram_update_id": str(job_info.get("telegram_update_id") or "") if job_info.get("telegram_update_id") else None,
        "query": job_info.get("query") or job_info.get("keyword") or "Search",
        "location": job_info.get("location") or job_info.get("city") or "",
        "requested_limit": int(job_info.get("requested_limit") or job_info.get("depth") or 5),
        "status": job_info.get("status") or "pending",
        "stage": job_info.get("stage") or "Queued",
        "progress": int(job_info.get("progress") or 0),
        "lead_counts": job_info.get("lead_counts") or {},
        "total_leads": int(job_info.get("total_leads") or 0),
        "error_summary": job_info.get("error_summary") or None,
        "source": job_info.get("source") or "telegram",
        "metadata": job_info.get("metadata") or {}
    }

    try:
        req = urllib.request.Request(endpoint, data=json.dumps(record).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                raw = json.loads(resp.read().decode("utf-8"))
                res_obj = raw[0] if isinstance(raw, list) and raw else record
                return {"success": True, "job": res_obj}
            return {"success": False, "error": f"Unexpected status {resp.status}"}
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", "replace")
        return {"success": False, "error": f"HTTP {e.code}: {err_msg}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def update_scrape_job_record(job_id: str, updates: dict, supabase_url=None, supabase_key=None) -> dict:
    """Update fields on an existing record in public.scrape_jobs via PostgREST PATCH."""
    url = (supabase_url or os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = (supabase_key or os.environ.get("SUPABASE_KEY", "")).strip()

    if not (url and key and job_id):
        return {"success": False, "error": "Supabase not configured or missing job_id"}

    endpoint = f"{url}/rest/v1/scrape_jobs?id=eq.{urllib.parse.quote(job_id)}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
        "User-Agent": "LeadMap-Pro-Supabase/1.0"
    }

    try:
        req = urllib.request.Request(endpoint, data=json.dumps(updates).encode("utf-8"), headers=headers, method="PATCH")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 204):
                return {"success": True}
            return {"success": False, "error": f"Unexpected status {resp.status}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def fetch_recent_scrape_jobs_from_supabase(limit: int = 5, supabase_url=None, supabase_key=None) -> list:
    """Fetch the most recent scrape jobs from public.scrape_jobs via PostgREST."""
    url = (supabase_url or os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = (supabase_key or os.environ.get("SUPABASE_KEY", "")).strip()

    if not (url and key):
        return []

    endpoint = f"{url}/rest/v1/scrape_jobs?select=*&order=created_at.desc&limit={limit}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "LeadMap-Pro-Supabase/1.0"
    }

    try:
        req = urllib.request.Request(endpoint, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 206):
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[Supabase fetch recent jobs error] {e}", file=sys.stderr)
    return []


if __name__ == "__main__":
    url, key, tbl = get_config()
    print("Testing Supabase connection...")
    if not (url and key):
        print("✗ Supabase is not configured yet. Add SUPABASE_URL and SUPABASE_KEY to your .env file.")
        sys.exit(1)

    ok, msg = test_connection(url, key, tbl)
    if ok:
        print(f"✔ {msg}")
        print("Testing history fetch from Supabase...")
        hist = fetch_history_from_supabase()
        print(f"Found {len(hist)} historical jobs in Supabase.")
    else:
        print(f"✗ {msg}")
        sys.exit(1)

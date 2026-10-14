#!/usr/bin/env python3
"""Unified Scraper Engine for LeadMap Pro.

Centralizes:
- Docker container health check and low-level HTTP requests.
- OpenStreetMap Nominatim geocoding.
- Scraper job initiation and polling lifecycle.
- Lead cleansing, multi-criteria deduplication, and social media enrichment.
- Local disk persistence (data/leads_{job_id}.json) and Supabase synchronization.
- Multi-format exports (Standard CSV, CRM Outreach CSV, JSON) with formula injection protection.
"""
import csv
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

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Auto-load .env
_env_path = os.path.join(REPO_ROOT, ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from scripts.cleanser import (
    clean_lead,
    deduplicate_leads,
    load_historical_leads,
    filter_previously_seen_leads,
    enrich_socials,
    sanitize_csv_cell,
    sanitize_csv_row
)
from scripts.supabase_client import (
    is_configured as is_supabase_configured,
    get_config as get_supabase_config,
    sync_leads as sync_supabase_leads,
    save_scrape_job_to_supabase,
    create_scrape_job_record,
    update_scrape_job_record,
    fetch_scrape_job_by_id,
    fetch_scrape_job_by_update_id,
    fetch_recent_scrape_jobs_from_supabase
)

DATA_DIR = os.path.join(REPO_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

SCRAPER_BASE = os.environ.get("SCRAPER_BASE_URL", "http://localhost:8085")
SCRAPER_KEY = os.environ.get("SCRAPER_API_KEY", "")
UA = "LeadMap-Pro-Bot/1.0 (https://github.com/vighneshpote55-svg/B2B-Scraper)"

JOB_LOCK = threading.Lock()
JOB_CACHE = {}


def validate_job_id(job_id: str) -> bool:
    """Validate that job_id matches alphanumeric safe pattern without path traversal."""
    if not job_id or not isinstance(job_id, str):
        return False
    return bool(re.match(r"^[a-zA-Z0-9_\-]{1,64}$", job_id.strip()))


def get_job_file_path(job_id: str) -> str:
    """Return canonical path to job leads JSON file, guaranteed inside DATA_DIR."""
    if not validate_job_id(job_id):
        raise ValueError(f"Invalid job_id: {job_id}")
    fname = f"leads_{job_id}.json"
    full_path = os.path.abspath(os.path.join(DATA_DIR, fname))
    if not full_path.startswith(os.path.abspath(DATA_DIR) + os.sep):
        raise ValueError("Directory traversal attempt detected")
    return full_path


def scraper_req(method: str, path: str, body: dict = None, timeout: int = 60) -> tuple:
    """Execute HTTP request against the local Go scraper container."""
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if SCRAPER_KEY:
        headers["X-API-Key"] = SCRAPER_KEY
    data = json.dumps(body).encode("utf-8") if body is not None else None
    url = f"{SCRAPER_BASE}{path}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def check_scraper_health() -> bool:
    """Check if the local Go scraper container is reachable."""
    try:
        status, _ = scraper_req("GET", "/api/v1/jobs", timeout=5)
        return status in (200, 204)
    except Exception:
        return False


def geocode_place(place_name: str) -> tuple:
    """Resolve city/place to (lat, lon, display_name) via OpenStreetMap Nominatim."""
    if not place_name or not place_name.strip():
        return None, None, None
    q = urllib.parse.urlencode({"format": "json", "limit": 1, "q": place_name.strip()})
    url = f"https://nominatim.openstreetmap.org/search?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            hits = json.loads(resp.read().decode("utf-8"))
            if hits and isinstance(hits, list) and len(hits) > 0:
                first = hits[0]
                lat = str(first.get("lat", ""))
                lon = str(first.get("lon", ""))
                name = first.get("display_name", place_name).split(",")[0].strip()
                return lat, lon, name
    except Exception as e:
        print(f"[geocode warning] {e}", file=sys.stderr)
    return None, None, None


def sanitize_error_message(err: Exception) -> str:
    """Sanitize internal errors to prevent leaking tokens, database credentials, or internal paths."""
    err_str = str(err)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token and token in err_str:
        err_str = err_str.replace(token, "[REDACTED_BOT_TOKEN]")

    sb_key = os.environ.get("SUPABASE_KEY", "").strip()
    if sb_key and sb_key in err_str:
        err_str = err_str.replace(sb_key, "[REDACTED_KEY]")

    if REPO_ROOT in err_str:
        err_str = err_str.replace(REPO_ROOT, "/app")

    if any(k in err_str.lower() for k in ("traceback", "syntaxerror", "operationalerror", "postgres")):
        return "An internal service error occurred. Please try again later."

    return err_str


def initiate_scrape_job(params: dict) -> tuple:
    """Start an asynchronous scraping job.

    Returns (success: bool, job_id_or_error: str, resolved_params: dict).
    Reverses submission order: performs atomic job reservation in Supabase
    before submitting to the scraper service to prevent duplicate jobs.
    """
    keyword = (params.get("keyword") or "").strip()
    city = (params.get("city") or "").strip()
    depth = int(params.get("depth") or 5)
    extract_email = bool(params.get("email", True))
    find_socials = bool(params.get("socials", True))
    exclude_seen = bool(params.get("exclude_seen", True))
    lat = params.get("lat")
    lon = params.get("lon")

    if not keyword:
        return False, "Keyword is required.", {}

    full_query = f"{keyword} in {city}" if city and " in " not in keyword.lower() else keyword
    place_for_geo = city if city else keyword
    resolved_city = city or place_for_geo

    if not (lat and lon):
        lat_str, lon_str, resolved_name = geocode_place(place_for_geo)
        if not (lat_str and lon_str):
            return False, f"Could not determine geographic coordinates for '{place_for_geo}'.", {}
        lat, lon = lat_str, lon_str
        resolved_city = resolved_name

    if not check_scraper_health():
        return False, f"Scraper service is not reachable at {SCRAPER_BASE}. Is Docker running?", {}

    resolved_params = {
        "keyword": keyword,
        "city": resolved_city,
        "full_query": full_query,
        "lat": lat,
        "lon": lon,
        "depth": depth,
        "email": extract_email,
        "socials": find_socials,
        "exclude_seen": exclude_seen,
        "max_time": params.get("max_time", 600),
        "source": params.get("source", "web"),
        "telegram_user_id": params.get("telegram_user_id"),
        "telegram_chat_id": params.get("telegram_chat_id"),
        "telegram_update_id": params.get("telegram_update_id")
    }

    # Atomic Reservation Step: Create Supabase scrape_jobs record BEFORE invoking scraper
    reservation_id = None
    if is_supabase_configured():
        reservation_id = str(uuid.uuid4())
        res = create_scrape_job_record({
            "id": reservation_id,
            "scraper_job_id": "",
            "telegram_user_id": resolved_params.get("telegram_user_id"),
            "telegram_chat_id": resolved_params.get("telegram_chat_id"),
            "telegram_update_id": resolved_params.get("telegram_update_id"),
            "query": full_query,
            "location": resolved_city,
            "requested_limit": depth,
            "status": "pending",
            "stage": "Reserving scrape slot...",
            "progress": 5,
            "source": resolved_params.get("source", "web")
        })

        if not res.get("success"):
            if res.get("is_duplicate"):
                # Duplicate update detected via unique constraint on telegram_update_id
                up_id = resolved_params.get("telegram_update_id")
                existing = fetch_scrape_job_by_update_id(str(up_id)) if up_id else {}
                existing_id = existing.get("scraper_job_id") or existing.get("id") or "existing"
                return False, f"Duplicate update: job already reserved ({existing_id})", {
                    "duplicate": True,
                    "job_id": existing_id,
                    "existing_job": existing
                }
            else:
                # Database error/outage distinct from duplicate conflict
                db_err = res.get("error", "Database error during job reservation")
                return False, f"Database error reserving job: {db_err}", {"db_error": True}

    scraper_body = {
        "name": f"job-{int(time.time())}",
        "keywords": [full_query],
        "lang": "en",
        "zoom": 15,
        "lat": str(lat),
        "lon": str(lon),
        "fast_mode": False,
        "radius": 10000,
        "depth": depth,
        "email": extract_email,
        "max_time": params.get("max_time", 600)
    }

    try:
        _, resp_raw = scraper_req("POST", "/api/v1/jobs", scraper_body)
        job_id = json.loads(resp_raw.decode("utf-8")).get("id")
        if not job_id:
            raise ValueError("No job ID returned from scraper service.")
    except urllib.error.HTTPError as e:
        err_text = f"Scraper service HTTP {e.code}: {e.read().decode('utf-8', 'replace')}"
        sanitized = sanitize_error_message(Exception(err_text))
        if reservation_id and is_supabase_configured():
            try:
                update_scrape_job_record(reservation_id, {
                    "status": "failed",
                    "stage": "Failed to submit to scraper service",
                    "error_summary": sanitized
                })
            except Exception:
                pass
        return False, sanitized, {"submission_failed": True}
    except Exception as e:
        sanitized = sanitize_error_message(e)
        if reservation_id and is_supabase_configured():
            try:
                update_scrape_job_record(reservation_id, {
                    "status": "failed",
                    "stage": "Failed to submit to scraper service",
                    "error_summary": sanitized
                })
            except Exception:
                pass
        return False, f"Failed to start scrape: {sanitized}", {"submission_failed": True}

    # Initialize in-memory cache
    with JOB_LOCK:
        JOB_CACHE[job_id] = {
            "id": job_id,
            "reservation_id": reservation_id,
            "status": "working",
            "progress_percent": 15,
            "stage": "Scraping Google Maps listings...",
            "started_at": time.time(),
            "leads": [],
            "params": resolved_params,
            "error": None
        }

    # Update reserved record with real scraper_job_id and running status
    if reservation_id and is_supabase_configured():
        try:
            update_scrape_job_record(reservation_id, {
                "scraper_job_id": job_id,
                "status": "running",
                "stage": "Scraping listings on Google Maps...",
                "progress": 15
            })
        except Exception as e:
            print(f"[Supabase scrape_jobs update error] {e}", file=sys.stderr)

    return True, job_id, resolved_params


def poll_and_process_job(job_id: str, params: dict) -> dict:
    """Continuously poll a submitted scraper job, download CSV, clean, enrich, and persist."""
    max_wait = params.get("max_time", 600)
    start_time = time.time()
    completed = False

    while time.time() - start_time < max_wait:
        with JOB_LOCK:
            if JOB_CACHE.get(job_id, {}).get("cancel_requested"):
                JOB_CACHE[job_id]["status"] = "cancelled"
                JOB_CACHE[job_id]["stage"] = "Extraction cancelled by user."
                if is_supabase_configured():
                    try:
                        update_scrape_job_record(job_id, {"status": "cancelled", "stage": "Cancelled by user."})
                    except Exception:
                        pass
                return {"status": "cancelled", "id": job_id}

        try:
            _, raw = scraper_req("GET", f"/api/v1/jobs/{job_id}", timeout=10)
            res = json.loads(raw.decode("utf-8"))
            status = res.get("Status")

            elapsed = int(time.time() - start_time)
            progress = min(85, 15 + int(elapsed * 2))

            with JOB_LOCK:
                if job_id in JOB_CACHE:
                    JOB_CACHE[job_id]["status"] = status
                    JOB_CACHE[job_id]["progress_percent"] = progress
                    JOB_CACHE[job_id]["stage"] = f"Scraping listings on Google Maps ({elapsed}s elapsed)..."

            if is_supabase_configured() and elapsed % 15 == 0:
                try:
                    update_scrape_job_record(job_id, {"progress": progress, "stage": f"Scraping ({elapsed}s elapsed)..."})
                except Exception:
                    pass

            if status == "ok":
                completed = True
                break
            elif status == "failed":
                with JOB_LOCK:
                    JOB_CACHE[job_id]["status"] = "failed"
                    JOB_CACHE[job_id]["error"] = "Scraper service returned failed status. IP may be throttled."
                if is_supabase_configured():
                    try:
                        update_scrape_job_record(job_id, {"status": "failed", "error_summary": "Scraper failed or throttled."})
                    except Exception:
                        pass
                return {"status": "failed", "id": job_id, "error": "Scraper service returned failed status."}
        except Exception as e:
            print(f"[poll error] {e}", file=sys.stderr)

        time.sleep(4)

    if not completed:
        with JOB_LOCK:
            JOB_CACHE[job_id]["status"] = "failed"
            JOB_CACHE[job_id]["error"] = "Job timed out waiting for scraper completion."
        return {"status": "failed", "id": job_id, "error": "Job timed out."}

    # Download raw CSV output
    with JOB_LOCK:
        JOB_CACHE[job_id]["stage"] = "Downloading and parsing listings..."
        JOB_CACHE[job_id]["progress_percent"] = 90

    try:
        _, raw_csv = scraper_req("GET", f"/api/v1/jobs/{job_id}/download", timeout=60)
        csv_text = raw_csv.decode("utf-8", "replace")
        rows = list(csv.DictReader(io.StringIO(csv_text)))

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
            cleaned_leads.append(clean_lead(lead))

        cleaned_leads = deduplicate_leads(cleaned_leads)

        exclude_seen = params.get("exclude_seen", True)
        skipped_previous = []
        if exclude_seen:
            historical_leads = load_historical_leads(DATA_DIR, exclude_job_id=job_id)
            new_leads, skipped_previous = filter_previously_seen_leads(cleaned_leads, historical_leads)
            cleaned_leads = new_leads
        else:
            historical_leads = load_historical_leads(DATA_DIR, exclude_job_id=job_id)
            _, skipped_previous = filter_previously_seen_leads(cleaned_leads, historical_leads)

        if params.get("socials", True) and cleaned_leads:
            with JOB_LOCK:
                JOB_CACHE[job_id]["stage"] = "Scanning websites for social profiles..."
                JOB_CACHE[job_id]["progress_percent"] = 96
            cleaned_leads = enrich_socials(cleaned_leads)

        # Compute metric aggregates
        hot_cnt = sum(1 for l in cleaned_leads if l.get("lead_tier") == "HOT")
        warm_cnt = sum(1 for l in cleaned_leads if l.get("lead_tier") == "WARM")
        cold_cnt = sum(1 for l in cleaned_leads if l.get("lead_tier") == "COLD")
        email_cnt = sum(1 for l in cleaned_leads if l.get("emails"))
        phone_cnt = sum(1 for l in cleaned_leads if (l.get("clean_phone") or l.get("phone")))

        metrics = {
            "total": len(cleaned_leads),
            "hot": hot_cnt,
            "warm": warm_cnt,
            "cold": cold_cnt,
            "with_email": email_cnt,
            "with_phone": phone_cnt,
            "skipped_previous": len(skipped_previous),
            "raw_extracted": len(cleaned_leads) + len(skipped_previous)
        }

        # Persist locally in DATA_DIR
        data_file = get_job_file_path(job_id)
        with open(data_file, "w", encoding="utf-8") as f:
            json.dump({
                "job_id": job_id,
                "params": params,
                "completed_at": time.time(),
                "leads": cleaned_leads,
                "skipped_previous_count": len(skipped_previous),
                "metrics": metrics
            }, f, indent=2)

        # Sync to Supabase if configured
        supabase_synced = 0
        if is_supabase_configured() and os.environ.get("SUPABASE_AUTO_SYNC", "true").lower() == "true":
            try:
                sync_res = sync_supabase_leads(cleaned_leads, job_id=job_id)
                if sync_res.get("success"):
                    supabase_synced = sync_res.get("confirmed_count") or sync_res.get("count", 0)

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
                    "metrics": metrics,
                    "params": params
                })

                update_scrape_job_record(job_id, {
                    "status": "ok",
                    "stage": "Completed",
                    "progress": 100,
                    "total_leads": len(cleaned_leads),
                    "lead_counts": metrics,
                    "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                })
            except Exception as se:
                print(f"[Supabase sync warning] {se}", file=sys.stderr)

        metrics["supabase_synced"] = supabase_synced

        with JOB_LOCK:
            JOB_CACHE[job_id]["status"] = "ok"
            JOB_CACHE[job_id]["progress_percent"] = 100
            JOB_CACHE[job_id]["stage"] = f"Complete! {len(cleaned_leads)} qualified leads ready."
            JOB_CACHE[job_id]["leads"] = cleaned_leads
            JOB_CACHE[job_id]["metrics"] = metrics

        return {
            "status": "ok",
            "id": job_id,
            "leads": cleaned_leads,
            "metrics": metrics
        }
    except Exception as ex:
        with JOB_LOCK:
            JOB_CACHE[job_id]["status"] = "failed"
            JOB_CACHE[job_id]["error"] = str(ex)
        return {"status": "failed", "id": job_id, "error": str(ex)}


def cancel_job(job_id: str) -> bool:
    """Request graceful cancellation of an active scraping job."""
    if not validate_job_id(job_id):
        return False
    with JOB_LOCK:
        if job_id in JOB_CACHE:
            JOB_CACHE[job_id]["cancel_requested"] = True
            JOB_CACHE[job_id]["status"] = "cancelled"
            return True
    return False


def get_job_info(job_id: str) -> dict:
    """Retrieve job metadata and results from in-memory cache, local disk, or Supabase."""
    if not validate_job_id(job_id):
        return {}

    with JOB_LOCK:
        if job_id in JOB_CACHE:
            return dict(JOB_CACHE[job_id])

    try:
        fpath = get_job_file_path(job_id)
        if os.path.isfile(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "id": job_id,
                    "status": "ok",
                    "progress_percent": 100,
                    "stage": "Completed",
                    "leads": data.get("leads", []),
                    "params": data.get("params", {}),
                    "metrics": data.get("metrics", {})
                }
    except Exception:
        pass

    # Supabase-backed fallback for /status when not in memory or local files
    if is_supabase_configured():
        try:
            record = fetch_scrape_job_by_id(job_id)
            if record:
                return {
                    "id": record.get("scraper_job_id") or record.get("id"),
                    "status": record.get("status", "unknown"),
                    "progress_percent": record.get("progress", 0),
                    "stage": record.get("stage", ""),
                    "leads": [],
                    "params": {
                        "query": record.get("query"),
                        "city": record.get("location"),
                        "depth": record.get("requested_limit")
                    },
                    "metrics": record.get("lead_counts") or {},
                    "error": record.get("error_summary"),
                    "is_fallback": True
                }
        except Exception as e:
            print(f"[Supabase get_job_info fallback error] {e}", file=sys.stderr)

    return {}


def export_leads(leads: list, fmt: str = "csv") -> tuple:
    """Export lead listings into sanitized bytes (CSV, CRM outreach CSV, or JSON).

    Neutralizes formula injection characters (=, +, -, @, \\t, \\r).
    Returns (bytes, filename_suffix, mime_type).
    """
    fmt = (fmt or "csv").lower().strip()
    if fmt == "json":
        buf = json.dumps(leads, indent=2, ensure_ascii=False).encode("utf-8")
        return buf, ".json", "application/json"

    if fmt in ("crm", "outreach"):
        headers = [
            "Company Name", "Contact Email", "Phone Number", "Website", "Category",
            "Address", "Rating", "Review Count", "Lead Tier", "Lead Score",
            "Instagram", "Facebook", "LinkedIn"
        ]
        out_rows = []
        for l in leads:
            row = {
                "Company Name": sanitize_csv_cell(l.get("title", "")),
                "Contact Email": sanitize_csv_cell(l.get("emails", "")),
                "Phone Number": sanitize_csv_cell(l.get("clean_phone") or l.get("phone", "")),
                "Website": sanitize_csv_cell(l.get("website", "")),
                "Category": sanitize_csv_cell(l.get("category", "")),
                "Address": sanitize_csv_cell(l.get("address", "")),
                "Rating": sanitize_csv_cell(str(l.get("review_rating", ""))),
                "Review Count": sanitize_csv_cell(str(l.get("review_count", ""))),
                "Lead Tier": sanitize_csv_cell(l.get("lead_tier", "")),
                "Lead Score": sanitize_csv_cell(str(l.get("lead_score", ""))),
                "Instagram": sanitize_csv_cell(l.get("instagram", "")),
                "Facebook": sanitize_csv_cell(l.get("facebook", "")),
                "LinkedIn": sanitize_csv_cell(l.get("linkedin", ""))
            }
            out_rows.append(row)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=headers)
        writer.writeheader()
        writer.writerows(out_rows)
        return buf.getvalue().encode("utf-8"), "_crm_outreach.csv", "text/csv"

    if fmt in ("whatsapp", "wa"):
        headers = [
            "phone", "name", "category", "rating", "address", "website",
            "custom_pitch", "consent_source", "whatsapp_status"
        ]
        out_rows = []
        for l in leads:
            wa_phone = l.get("whatsapp_phone_e164") or l.get("clean_phone") or l.get("phone", "")
            if not wa_phone:
                continue
            title = l.get("title", "")
            cat = l.get("category", "")
            rating = str(l.get("review_rating", ""))
            addr = l.get("address", "")
            pitch = f"Hi {title}, noticed your business in {addr}. We help {cat} businesses acquire qualified clients."
            row = {
                "phone": sanitize_csv_cell(wa_phone),
                "name": sanitize_csv_cell(title),
                "category": sanitize_csv_cell(cat),
                "rating": sanitize_csv_cell(rating),
                "address": sanitize_csv_cell(addr),
                "website": sanitize_csv_cell(l.get("website", "")),
                "custom_pitch": sanitize_csv_cell(pitch),
                "consent_source": sanitize_csv_cell(l.get("consent_source", "public_directory")),
                "whatsapp_status": sanitize_csv_cell(l.get("whatsapp_status", "eligible"))
            }
            out_rows.append(row)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=headers)
        writer.writeheader()
        writer.writerows(out_rows)
        return buf.getvalue().encode("utf-8"), "_whatsapp_portal.csv", "text/csv"


    # Default: Standard Clean CSV
    headers = [
        "title", "clean_phone", "emails", "website", "category",
        "address", "review_rating", "review_count", "lead_tier", "lead_score",
        "instagram", "facebook", "linkedin"
    ]
    out_rows = []
    for l in leads:
        row = {
            "title": sanitize_csv_cell(l.get("title", "")),
            "clean_phone": sanitize_csv_cell(l.get("clean_phone") or l.get("phone", "")),
            "emails": sanitize_csv_cell(l.get("emails", "")),
            "website": sanitize_csv_cell(l.get("website", "")),
            "category": sanitize_csv_cell(l.get("category", "")),
            "address": sanitize_csv_cell(l.get("address", "")),
            "review_rating": sanitize_csv_cell(str(l.get("review_rating", ""))),
            "review_count": sanitize_csv_cell(str(l.get("review_count", ""))),
            "lead_tier": sanitize_csv_cell(l.get("lead_tier", "")),
            "lead_score": sanitize_csv_cell(str(l.get("lead_score", ""))),
            "instagram": sanitize_csv_cell(l.get("instagram", "")),
            "facebook": sanitize_csv_cell(l.get("facebook", "")),
            "linkedin": sanitize_csv_cell(l.get("linkedin", ""))
        }
        out_rows.append(row)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    writer.writerows(out_rows)
    return buf.getvalue().encode("utf-8"), "_clean.csv", "text/csv"

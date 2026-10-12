#!/usr/bin/env python3
"""Unified Scraper Execution & Processing Engine for LeadMap Pro.

Centralizes:
- Scraper container proxy & health checks.
- Nominatim geocoding.
- Job polling, cancellation, and progress reporting.
- Lead cleansing, multi-criteria deduplication, and social media enrichment.
- Local JSON archive writing (data/leads_<job_id>.json).
- Supabase cloud synchronization (leads & scrape_history).
- Sanitized multi-format lead exports (CSV, CRM outreach CSV, JSON).

Reusable by both the Web Dashboard (dashboard.py) and Telegram Bot (scripts/telegram_bot.py).
Zero third-party dependencies — relies entirely on Python standard library.
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

# Dynamic reload for .env
def reload_env():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.isfile(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

reload_env()

SCRAPER_BASE = os.environ.get("SCRAPER_BASE_URL", "http://localhost:8085")
SCRAPER_KEY = os.environ.get("SCRAPER_API_KEY", "")
UA = "google-maps-scraper-kit/1.0 (https://github.com/Mahanaicoach/google-maps-scraper-kit)"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

JOB_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# In-memory store for active job progress across threads/processes
JOB_CACHE = {}
JOB_LOCK = threading.Lock()

# Import cleansing and Supabase tools
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    from scripts.cleanser import (
        clean_lead,
        deduplicate_leads,
        enrich_socials,
        load_historical_leads,
        filter_previously_seen_leads,
        sanitize_csv_cell,
        sanitize_csv_row
    )
except ImportError:
    from cleanser import (
        clean_lead,
        deduplicate_leads,
        enrich_socials,
        load_historical_leads,
        filter_previously_seen_leads,
        sanitize_csv_cell,
        sanitize_csv_row
    )

try:
    from scripts.supabase_client import (
        is_configured as is_supabase_configured,
        sync_leads as sync_supabase_leads,
        save_scrape_job_to_supabase,
        fetch_leads_for_job_from_supabase,
        create_scrape_job_record,
        update_scrape_job_record,
        fetch_recent_scrape_jobs_from_supabase
    )
except ImportError:
    is_supabase_configured = lambda: False
    sync_supabase_leads = lambda *args, **kwargs: {"success": False, "error": "Supabase client unavailable"}
    save_scrape_job_to_supabase = lambda *args, **kwargs: {"success": False, "error": "Supabase client unavailable"}
    fetch_leads_for_job_from_supabase = lambda *args, **kwargs: []
    create_scrape_job_record = lambda *args, **kwargs: {"success": False}
    update_scrape_job_record = lambda *args, **kwargs: {"success": False}
    fetch_recent_scrape_jobs_from_supabase = lambda *args, **kwargs: []


def validate_job_id(job_id: str) -> bool:
    """Validate that job_id is alphanumeric with underscores/hyphens and max 64 chars."""
    if not job_id or not isinstance(job_id, str):
        return False
    return bool(JOB_ID_REGEX.match(job_id.strip()))


def get_job_file_path(job_id: str):
    """Safely resolve job JSON file path, preventing directory traversal."""
    if not validate_job_id(job_id):
        return None
    data_dir_abs = os.path.abspath(DATA_DIR)
    safe_path = os.path.abspath(os.path.join(data_dir_abs, f"leads_{job_id.strip()}.json"))
    if not safe_path.startswith(data_dir_abs + os.sep):
        return None
    return safe_path


def scraper_req(method, path, body=None, timeout=30):
    """Perform HTTP request against local gmaps-scraper Docker service."""
    reload_env()
    base = os.environ.get("SCRAPER_BASE_URL", SCRAPER_BASE).rstrip("/")
    key = os.environ.get("SCRAPER_API_KEY", SCRAPER_KEY)

    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if key:
        headers["X-API-Key"] = key
    data = json.dumps(body).encode() if body is not None else None
    url = base + path
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def check_scraper_health():
    """Verify if the scraper container is reachable."""
    try:
        status, _ = scraper_req("GET", "/api/v1/jobs", timeout=3)
        return status == 200
    except Exception:
        return False


def geocode_place(place: str):
    """Geocode city or place name using Nominatim OpenStreetMap (free, ~1 req/sec)."""
    if not place or not isinstance(place, str):
        return None, None, ""
    q = urllib.parse.urlencode({"format": "json", "limit": 1, "q": place.strip()})
    url = f"https://nominatim.openstreetmap.org/search?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            time.sleep(1)  # respect Nominatim's policy
            if data:
                return str(data[0]["lat"]), str(data[0]["lon"]), data[0].get("display_name", place)
    except Exception as e:
        print(f"[geocode error] {e}", file=sys.stderr)
    return None, None, place


def initiate_scrape_job(params: dict) -> tuple:
    """Submit a scrape job to the local scraper service.

    Returns (success: bool, job_id_or_err: str, resolved_params: dict).
    """
    keyword = (params.get("keyword") or "").strip()
    city = (params.get("city") or "").strip()
    depth = int(params.get("depth", 5))
    extract_email = bool(params.get("email", True))
    find_socials = bool(params.get("socials", True))
    exclude_seen = bool(params.get("exclude_seen", True))
    lat = params.get("lat")
    lon = params.get("lon")
    max_time = int(params.get("max_time", 600))

    if not keyword:
        return False, "Keyword is required", {}

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
        base = os.environ.get("SCRAPER_BASE_URL", SCRAPER_BASE)
        return False, f"Scraper service is not reachable at {base}. Is Docker running?", {}

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
        "max_time": max_time
    }

    try:
        _, resp_raw = scraper_req("POST", "/api/v1/jobs", scraper_body)
        job_id = json.loads(resp_raw.decode()).get("id")
        if not job_id:
            return False, "No job ID returned from scraper service", {}
    except urllib.error.HTTPError as e:
        err_text = e.read().decode("utf-8", "replace")
        return False, f"Scraper service HTTP {e.code}: {err_text}", {}
    except Exception as e:
        return False, f"Failed to start scrape: {str(e)}", {}

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
        "max_time": max_time,
        "source": params.get("source", "core"),
        "telegram_user_id": str(params.get("telegram_user_id") or ""),
        "telegram_chat_id": str(params.get("telegram_chat_id") or ""),
        "telegram_update_id": str(params.get("telegram_update_id") or "") if params.get("telegram_update_id") else None
    }

    # Record in Supabase scrape_jobs table if configured
    if is_supabase_configured():
        try:
            create_scrape_job_record({
                "id": str(uuid.uuid4()),
                "scraper_job_id": job_id,
                "telegram_user_id": resolved_params.get("telegram_user_id"),
                "telegram_chat_id": resolved_params.get("telegram_chat_id"),
                "telegram_update_id": resolved_params.get("telegram_update_id"),
                "query": full_query,
                "location": resolved_city,
                "requested_limit": depth,
                "status": "running",
                "stage": "Scraping Google Maps listings...",
                "progress": 15,
                "source": resolved_params.get("source", "core")
            })
        except Exception as e:
            print(f"[Supabase scrape_jobs notice] {e}", file=sys.stderr)

    with JOB_LOCK:
        JOB_CACHE[job_id] = {
            "id": job_id,
            "status": "working",
            "progress_percent": 15,
            "stage": "Scraping Google Maps listings...",
            "started_at": time.time(),
            "leads": [],
            "params": resolved_params,
            "error": None
        }

    return True, job_id, resolved_params


def poll_and_process_job(job_id: str, params: dict, progress_cb=None, cancel_check=None) -> dict:
    """Poll job until completion, cleanse, deduplicate, enrich, and archive leads."""
    max_wait = int(params.get("max_time", 600))
    start_time = time.time()
    completed = False

    def update_progress(pct, stage, status="working"):
        with JOB_LOCK:
            if job_id in JOB_CACHE:
                JOB_CACHE[job_id]["status"] = status
                JOB_CACHE[job_id]["progress_percent"] = pct
                JOB_CACHE[job_id]["stage"] = stage
        if progress_cb:
            try:
                progress_cb(job_id, pct, stage, status)
            except Exception:
                pass

    update_progress(15, "Scraping Google Maps listings...")

    while time.time() - start_time < max_wait:
        # Check cancellation
        is_cancelled = False
        with JOB_LOCK:
            if JOB_CACHE.get(job_id, {}).get("cancel_requested"):
                is_cancelled = True
        if cancel_check and cancel_check(job_id):
            is_cancelled = True

        if is_cancelled:
            update_progress(0, "Extraction cancelled by user.", status="cancelled")
            return {"status": "cancelled", "leads": [], "error": "Cancelled"}

        try:
            _, raw = scraper_req("GET", f"/api/v1/jobs/{job_id}", timeout=10)
            res = json.loads(raw.decode())
            status = res.get("Status")

            elapsed = int(time.time() - start_time)
            progress = min(85, 15 + int(elapsed * 2))
            update_progress(progress, f"Scraping listings on Google Maps ({elapsed}s elapsed)...", status=status or "working")

            if status == "ok":
                completed = True
                break
            elif status == "failed":
                update_progress(0, "Scraper service returned failed status. IP may be throttled.", status="failed")
                return {"status": "failed", "leads": [], "error": "Scraper service failed"}
        except Exception as e:
            print(f"[poll error] {e}", file=sys.stderr)

        time.sleep(4)

    if not completed:
        update_progress(0, "Job timed out waiting for scraper completion.", status="failed")
        return {"status": "failed", "leads": [], "error": "Timed out"}

    # Download CSV
    update_progress(90, "Downloading and parsing listings...")
    try:
        _, raw_csv = scraper_req("GET", f"/api/v1/jobs/{job_id}/download", timeout=60)
        csv_text = raw_csv.decode("utf-8", "replace")
        rows = list(csv.DictReader(io.StringIO(csv_text)))

        # Cleanse and score
        update_progress(95, "Cleansing emails, formatting phones, and calculating lead scores...")
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

        # Social enrichment
        if params.get("socials", True) and cleaned_leads:
            update_progress(96, "Scanning websites for Instagram, Facebook, and LinkedIn profiles...")
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

        # Supabase sync if configured
        supabase_synced = 0
        if is_supabase_configured() and os.environ.get("SUPABASE_AUTO_SYNC", "true").lower() == "true":
            try:
                update_progress(98, "Syncing qualified leads and history to Supabase Cloud...")
                sync_res = sync_supabase_leads(cleaned_leads, job_id=job_id)
                if sync_res.get("success"):
                    supabase_synced = sync_res.get("count", 0)

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
                print(f"[Supabase sync note] {se}", file=sys.stderr)

        stage_desc = f"Complete! {len(cleaned_leads)} new leads ready"
        if skipped_previous:
            stage_desc += f" ({len(skipped_previous)} previously scraped leads skipped)"
        if not cleaned_leads and skipped_previous:
            stage_desc = f"All {len(skipped_previous)} leads were already collected in past searches! Increase depth for new leads."
        if supabase_synced:
            stage_desc += f" ({supabase_synced} synced to Supabase)"

        final_job_data = {
            "id": job_id,
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
        }

        with JOB_LOCK:
            if job_id in JOB_CACHE:
                JOB_CACHE[job_id].update(final_job_data)

        if progress_cb:
            try:
                progress_cb(job_id, 100, stage_desc, "ok")
            except Exception:
                pass

        return final_job_data

    except Exception as e:
        err_msg = f"Processing error: {str(e)}"
        update_progress(0, err_msg, status="failed")
        return {"status": "failed", "leads": [], "error": err_msg}


def get_job_info(job_id: str) -> dict:
    """Retrieve in-memory or persisted job information."""
    if not validate_job_id(job_id):
        return {}

    with JOB_LOCK:
        if job_id in JOB_CACHE:
            return dict(JOB_CACHE[job_id])

    file_path = get_job_file_path(job_id)
    if file_path and os.path.isfile(file_path):
        try:
            with open(file_path) as f:
                data = json.load(f)
                return {
                    "id": job_id,
                    "status": "ok",
                    "progress_percent": 100,
                    "stage": f"Complete ({len(data.get('leads', []))} leads)",
                    "leads": data.get("leads", []),
                    "params": data.get("params", {}),
                    "metrics": data.get("metrics", {})
                }
        except Exception:
            pass
    return {}


def cancel_job(job_id: str) -> bool:
    """Signal cancellation for an active job."""
    if not validate_job_id(job_id):
        return False
    with JOB_LOCK:
        if job_id in JOB_CACHE:
            JOB_CACHE[job_id]["cancel_requested"] = True
            JOB_CACHE[job_id]["status"] = "cancelled"
            JOB_CACHE[job_id]["stage"] = "Extraction cancelled by user."
            return True
    return False


def export_leads(leads: list, fmt: str = "csv") -> tuple:
    """Export leads list into CSV, CRM Outreach CSV, or JSON bytes with formula injection sanitization.

    Returns (content_bytes, file_suffix, mime_type).
    """
    fmt = (fmt or "csv").lower().strip()

    if fmt == "json":
        data = json.dumps(leads, indent=2, ensure_ascii=False).encode("utf-8")
        return data, ".json", "application/json"

    elif fmt in ("crm", "outreach", "instantly", "smartlead", "hubspot"):
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
        return output.getvalue().encode("utf-8"), "_crm_outreach.csv", "text/csv"

    else:
        # Standard Clean CSV
        output = io.StringIO()
        fields = [
            "lead_tier", "lead_score", "title", "phone", "emails", "website",
            "category", "address", "review_rating", "review_count",
            "instagram", "facebook", "linkedin"
        ]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for l in leads:
            writer.writerow(sanitize_csv_row(l))
        return output.getvalue().encode("utf-8"), "_clean.csv", "text/csv"

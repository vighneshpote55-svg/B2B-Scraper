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


def test_connection(supabase_url=None, supabase_key=None, table=None):
    """Test connection to Supabase PostgREST endpoint."""
    reload_env()
    cfg_url, cfg_key, cfg_table = get_config()
    url = (supabase_url or cfg_url).strip().rstrip("/")
    key = (supabase_key or cfg_key).strip()
    tbl = (table or cfg_table or "leads").strip()

    if not (url and key):
        return False, "Missing Supabase URL or API Key."

    if not url.startswith("http"):
        return False, "Supabase URL must start with http:// or https://"

    if key.startswith("http"):
        return False, "Your SUPABASE_KEY appears to be a URL instead of an API Key! Please copy the 'anon' public key or 'service_role' key from Supabase Project Settings -> API."

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
            return False, f"Authentication error (HTTP {e.code}). Verify your Supabase anon/service_role API Key."
        return False, f"HTTP Error {e.code}: {err_msg}"
    except Exception as e:
        return False, f"Connection error: {str(e)}"


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


def sync_leads(leads, job_id="", supabase_url=None, supabase_key=None, table=None, batch_size=50):
    """Sync a list of leads to Supabase using PostgREST upsert (resolution=merge-duplicates)."""
    url = (supabase_url or os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
    key = (supabase_key or os.environ.get("SUPABASE_KEY", "")).strip()
    table_name = table or os.environ.get("SUPABASE_TABLE", "leads").strip()

    if not (url and key):
        return {"success": False, "error": "Supabase credentials not configured in .env."}

    if not leads:
        return {"success": True, "count": 0, "message": "No leads to sync."}

    endpoint = f"{url}/rest/v1/{table_name}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
        "User-Agent": "LeadMap-Pro-Supabase/1.0"
    }

    formatted_records = [format_lead_for_supabase(l, job_id=job_id) for l in leads]
    total_synced = 0

    # If re-syncing a specific job, clean out prior copy to avoid unique index conflict
    if job_id:
        try:
            del_endpoint = f"{url}/rest/v1/{table_name}?job_id=eq.{urllib.parse.quote(str(job_id))}"
            del_req = urllib.request.Request(del_endpoint, headers=headers, method="DELETE")
            urllib.request.urlopen(del_req, timeout=10)
        except Exception:
            pass

    # Batch inserts
    for i in range(0, len(formatted_records), batch_size):
        chunk = formatted_records[i : i + batch_size]
        data = json.dumps(chunk).encode("utf-8")

        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status in (200, 201):
                    inserted = json.loads(resp.read().decode())
                    total_synced += len(inserted) if isinstance(inserted, list) else len(chunk)
                else:
                    total_synced += len(chunk)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            print(f"[Supabase sync error] HTTP {e.code}: {err_body}", file=sys.stderr)
            return {"success": False, "error": f"HTTP {e.code}: {err_body}", "synced_before_error": total_synced}
        except Exception as e:
            print(f"[Supabase sync exception] {str(e)}", file=sys.stderr)
            return {"success": False, "error": str(e), "synced_before_error": total_synced}

    return {"success": True, "count": total_synced, "message": f"Successfully synced {total_synced} leads to Supabase table '{table_name}'."}


if __name__ == "__main__":
    url, key, tbl = get_config()
    print("Testing Supabase connection...")
    if not (url and key):
        print("✗ Supabase is not configured yet. Add SUPABASE_URL and SUPABASE_KEY to your .env file.")
        sys.exit(1)

    ok, msg = test_connection(url, key, tbl)
    if ok:
        print(f"✔ {msg}")
    else:
        print(f"✗ {msg}")
        sys.exit(1)

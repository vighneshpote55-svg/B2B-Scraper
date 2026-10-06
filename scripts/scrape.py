#!/usr/bin/env python3
"""One-shot Google Maps scrape using only the Python standard library (no pip installs).

Single keyword:
    python3 scripts/scrape.py "gyms in Miami FL" 25.7617 -80.1918 --depth 5

Auto-geocode (no coordinates needed):
    python3 scripts/scrape.py "cafes in Austin TX" --city "Austin, TX" --depth 5

Batch (one job, many keywords) from a file (one keyword per line):
    python3 scripts/scrape.py --keywords-file examples/queries.example.txt --city "Denver, CO"

Notes:
- Required by the API: keywords, lat/lon (strings), max_time (SECONDS). This script fills them in.
- Geocoding uses OpenStreetMap Nominatim (free, no key). Please be gentle: it allows ~1 request/sec
  and requires a descriptive User-Agent (set below). Don't loop it aggressively.
"""
import argparse, csv, io, json, os, re, sys, time, urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor

# Auto-load .env from repository root if present
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# Include project root in sys.path for cleanser import
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

try:
    from scripts.cleanser import clean_lead, deduplicate_leads, load_historical_leads, filter_previously_seen_leads
except ImportError:
    try:
        from cleanser import clean_lead, deduplicate_leads, load_historical_leads, filter_previously_seen_leads
    except ImportError:
        clean_lead = lambda x: x
        deduplicate_leads = lambda x: x
        load_historical_leads = lambda *args, **kwargs: []
        filter_previously_seen_leads = lambda leads, hist: (leads, [])

BASE = os.environ.get("SCRAPER_BASE_URL", "http://localhost:8080")
KEY = os.environ.get("SCRAPER_API_KEY", "")
# Money-useful LEAD fields only — what you actually use to contact/qualify a lead.
# Everything else (geo coordinates, IDs, hours, images, reviews blobs…) is dropped by default.
LEAD = ["title", "phone", "emails", "website", "category", "address", "review_rating", "review_count"]
UA = "google-maps-scraper-kit/1.0 (https://github.com/Mahanaicoach/google-maps-scraper-kit)"


def req(method, path, body=None):
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if KEY:
        headers["X-API-Key"] = KEY
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=60) as resp:
        return resp.status, resp.read()


def geocode(place):
    """City/place name -> ('lat','lon') strings via Nominatim, or None."""
    q = urllib.parse.urlencode({"format": "json", "limit": 1, "q": place})
    url = f"https://nominatim.openstreetmap.org/search?{q}"
    r = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            hits = json.loads(resp.read())
        time.sleep(1)  # respect Nominatim's ~1 req/sec policy
        if hits:
            return str(hits[0]["lat"]), str(hits[0]["lon"])
    except Exception as e:
        print(f"  (geocoding failed: {e})", file=sys.stderr)
    return None


def collect_keywords(a):
    kws = []
    if a.keyword:
        kws.append(a.keyword)
    kws.extend(a.also or [])
    if a.keywords_file:
        with open(a.keywords_file) as f:
            kws.extend(line.strip() for line in f if line.strip() and not line.startswith("#"))
    # de-dupe, keep order
    seen, out = set(), []
    for k in kws:
        if k not in seen:
            seen.add(k); out.append(k)
    return out


# ── Optional social enrichment (Instagram / Facebook / LinkedIn) ──────────────
# Pure code (HTTP + regex) → ZERO LLM tokens. Visits each business website once.
SOCIAL_RE = {
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)", re.I),
    "facebook":  re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/([A-Za-z0-9_.\-]+)", re.I),
    "linkedin":  re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|school)/([A-Za-z0-9_.\-%]+)", re.I),
}
_SKIP_HANDLE = {"", "home", "pages", "people", "help", "about", "policies", "policy",
                "legal", "tos", "privacy", "settings", "sharer", "tr", "profile.php",
                "plugins", "dialog", "intent", "login", "share.php", "permalink.php",
                "p", "reel", "reels", "explore", "stories", "tv", "watch", "events",
                "groups", "marketplace", "gaming", "photo", "hashtag", "search"}

def _fetch_html(url, timeout=10):
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    try:
        r = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; " + UA + ")", "Accept": "text/html"})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.read(400_000).decode("utf-8", "replace")
    except Exception:
        return ""

def _find_socials(html):
    out = {"instagram": "", "facebook": "", "linkedin": ""}
    for plat, rx in SOCIAL_RE.items():
        for m in rx.finditer(html or ""):
            h = m.group(1).lower()
            if h in _SKIP_HANDLE:
                continue
            if plat == "facebook" and (h.isdigit() or len(h) < 3):  # skip junk like /2008
                continue
            out[plat] = m.group(0).rstrip('"\'/').replace("\\", "")
            break
    return out

def enrich_socials(results, workers=8):
    """Add instagram/facebook/linkedin to each lead by scanning its website. Zero LLM tokens."""
    for r in results:                       # make sure every row has the keys
        r.setdefault("instagram", ""); r.setdefault("facebook", ""); r.setdefault("linkedin", "")
    todo = [r for r in results if r.get("website")]
    done = 0
    def work(r):
        r.update(_find_socials(_fetch_html(r["website"])))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in ex.map(work, todo):
            done += 1
            print(f"\r  socials: {done}/{len(todo)} sites checked", end="", flush=True)
    print()


def main():
    ap = argparse.ArgumentParser(description="Scrape Google Maps business listings.")
    ap.add_argument("keyword", nargs="?", help='e.g. "coffee shops in Austin TX"')
    ap.add_argument("lat", nargs="?", help="latitude (optional if --city given)")
    ap.add_argument("lon", nargs="?", help="longitude (optional if --city given)")
    ap.add_argument("--keyword", dest="also", action="append", help="extra keyword (repeatable)")
    ap.add_argument("--keywords-file", help="file with one keyword per line (batch)")
    ap.add_argument("--city", help="city/place to auto-geocode for lat/lon")
    ap.add_argument("--depth", type=int, default=5)
    # Emails are ON by default (they're the most valuable lead field). Visits each business
    # website to find an address — a bit slower. Use --no-email to skip for a fast run.
    ap.add_argument("--email", dest="email", action="store_true", default=True, help=argparse.SUPPRESS)
    ap.add_argument("--no-email", dest="email", action="store_false",
                    help="skip email extraction for a faster run (emails are on by default)")
    ap.add_argument("--max-time", type=int, default=600, help="job time limit in SECONDS")
    ap.add_argument("--out", default=None, help="output file path (default: results-<id>.csv). "
                    "The extension picks the format: .csv (default) or .json")
    ap.add_argument("--json", action="store_true", help="write JSON instead of the default CSV")
    ap.add_argument("--full", action="store_true", help="keep ALL raw columns (default: lean lead fields)")
    ap.add_argument("--fields", help="comma-separated columns to keep (overrides the default lead set)")
    ap.add_argument("--socials", action="store_true",
                    help="also find Instagram/Facebook/LinkedIn from each website (0 LLM tokens; slower)")
    ap.add_argument("--no-clean", dest="clean", action="store_false", default=True,
                    help="disable automatic lead cleansing (smart email filtering, phone formatting, deduplication)")
    ap.add_argument("--score", dest="score", action="store_true", default=True,
                    help="include lead scoring (0-100) and tier (HOT/WARM/COLD) in output (default: ON)")
    ap.add_argument("--no-score", dest="score", action="store_false",
                    help="disable lead scoring columns")
    ap.add_argument("--exclude-seen", dest="exclude_seen", action="store_true", default=True,
                    help="exclude businesses already collected in past searches (default: ON)")
    ap.add_argument("--no-exclude-seen", dest="exclude_seen", action="store_false",
                    help="allow duplicate businesses from past searches")
    a = ap.parse_args()

    keywords = collect_keywords(a)
    if not keywords:
        sys.exit("✗ No keywords. Pass a keyword, --keyword, or --keywords-file.")

    # Resolve coordinates: explicit lat/lon, else geocode --city, else geocode first keyword.
    lat, lon = a.lat, a.lon
    if not (lat and lon):
        place = a.city or keywords[0]
        print(f"▶ Geocoding \"{place}\"…")
        coords = geocode(place)
        if not coords:
            sys.exit("✗ Could not resolve coordinates. Pass lat/lon or a clearer --city.")
        lat, lon = coords
        print(f"  → {lat}, {lon}")

    if a.email:
        print("ℹ Email extraction is ON (visits each business website; a bit slower). "
              "Use --no-email for a fast run.")

    # --- WARN, don't block: flag high-volume jobs (proceed anyway) ---
    if a.depth >= 15 or len(keywords) >= 10:
        print("⚠️  Heads-up: this is a large job (high depth or many keywords).")
        print("    Running it back-to-back without proxies can get your IP temporarily rate-limited by")
        print("    Google (clears in minutes–hours; returns empty/failed jobs meanwhile). For big or")
        print("    repeated runs, add proxies. Proceeding…\n")

    # Health check
    try:
        req("GET", "/api/v1/jobs")
    except Exception as e:
        sys.exit(f"✗ Scraper not reachable at {BASE} — run 'docker compose up -d' first.\n  ({e})")

    body = {"name": "scrape-py", "keywords": keywords, "lang": "en", "zoom": 15,
            "lat": str(lat), "lon": str(lon), "fast_mode": False, "radius": 10000,
            "depth": a.depth, "email": a.email, "max_time": a.max_time}
    print(f"▶ Creating job: {len(keywords)} keyword(s) @ {lat},{lon} depth={a.depth} email={a.email}")
    for k in keywords:
        print(f"    • {k}")
    try:
        _, raw = req("POST", "/api/v1/jobs", body)
    except urllib.error.HTTPError as e:
        sys.exit(f"✗ Create failed: HTTP {e.code} — {e.read().decode()[:200]}")
    job_id = json.loads(raw).get("id")
    if not job_id:
        sys.exit("✗ No job id returned.")
    print(f"  job id: {job_id}")

    print("▶ Polling…")
    status = None
    for i in range(120):
        _, raw = req("GET", f"/api/v1/jobs/{job_id}")
        status = json.loads(raw).get("Status")
        print(f"\r  status: {str(status):<10} (attempt {i + 1})", end="", flush=True)
        if status == "ok":
            print(); break
        if status == "failed":
            sys.exit("\n✗ Job failed. If this keeps happening you may be rate-limited — wait or add proxies.")
        time.sleep(8)
    else:
        sys.exit("\n✗ Timed out.")

    _, raw = req("GET", f"/api/v1/jobs/{job_id}/download")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))
    if a.full:
        fields = list(rows[0].keys()) if rows else LEAD
    elif a.fields:
        fields = [c.strip() for c in a.fields.split(",") if c.strip()]
    else:
        fields = LEAD
    results = [{k: r.get(k, "") for k in fields} for r in rows]
    print(f"✓ Done — {len(results)} businesses (fields: {', '.join(fields)}).")

    if a.socials:
        print("▶ Finding socials (Instagram / Facebook / LinkedIn) on each website…")
        print("  ℹ Cost: this runs in CODE = 0 LLM tokens. It only adds ~40–50 tokens per business")
        print("    IF you load the results into an AI's context (negligible if you keep the file on disk).")
        enrich_socials(results)
        fields = fields + ["instagram", "facebook", "linkedin"]
        found = sum(1 for r in results if r.get("instagram") or r.get("facebook") or r.get("linkedin"))
        print(f"  socials found for {found}/{len(results)} businesses")

    if a.clean:
        print("▶ Cleansing lead data (filtering bot emails, formatting phones, deduplicating)…")
        results = [clean_lead(r) for r in results]
        before_count = len(results)
        results = deduplicate_leads(results)
        if len(results) < before_count:
            print(f"  removed {before_count - len(results)} duplicate business listing(s)")

        if a.exclude_seen:
            data_dir = os.path.join(_repo_root, "data")
            historical = load_historical_leads(data_dir, exclude_job_id=job_id)
            if historical:
                new_res, skipped = filter_previously_seen_leads(results, historical)
                results = new_res
                if skipped:
                    print(f"  🛡️ Excluded {len(skipped)} previously collected lead(s) from past searches (keeping {len(results)} brand-new).")
                if not results:
                    print("  ⚠️ All extracted listings were already collected in your previous searches!")
                    print("     Tip: Increase --depth (e.g. --depth 10 or 15) to uncover new businesses deeper in the map.")

    if a.score and a.clean:
        if "lead_tier" not in fields:
            fields = ["lead_tier", "lead_score"] + [f for f in fields if f not in ("lead_tier", "lead_score")]
        hot_count = sum(1 for r in results if r.get("lead_tier") == "HOT")
        warm_count = sum(1 for r in results if r.get("lead_tier") == "WARM")
        print(f"  lead quality breakdown: {hot_count} HOT 🔥 | {warm_count} WARM ⚡ | {len(results) - hot_count - warm_count} COLD ❄️")

    # Default output is a CSV file (opens in Excel / Google Sheets). Use --json (or a .json --out path) for JSON.
    as_json = a.json or (a.out and a.out.lower().endswith(".json"))
    out = a.out or f"results-{job_id[:8]}.{'json' if as_json else 'csv'}"
    if as_json:
        with open(out, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
    else:
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(results)
    print(f"  saved → {out}")
    for r in results[:5]:
        score_tag = f"[{r.get('lead_tier','')} {r.get('lead_score','')}] " if a.score and a.clean else ""
        tail = f" | IG:{r.get('instagram','') or '—'}" if a.socials else f" | {r.get('website','')}"
        print(f"  • {score_tag}{r.get('title','')} | {r.get('phone','')} | {r.get('emails','')}{tail}")

    # Automatic Supabase sync if credentials exist in .env
    try:
        from scripts.supabase_client import is_configured as is_sb_ready, sync_leads as sb_sync
        if is_sb_ready():
            print("▶ Syncing qualified leads to Supabase Cloud...")
            sb_res = sb_sync(results, job_id=job_id)
            if sb_res.get("success"):
                print(f"  ✔ {sb_res.get('message')}")
            else:
                print(f"  ✗ Supabase sync error: {sb_res.get('error')}", file=sys.stderr)
    except Exception as e:
        pass


if __name__ == "__main__":
    main()

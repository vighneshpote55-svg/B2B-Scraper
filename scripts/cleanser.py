#!/usr/bin/env python3
"""Advanced Lead Cleansing, Normalization, and Scoring Engine.

Features:
- Smart email cleansing: eliminates bot, crawler, sentry, CMS, and image spam emails.
- Phone number normalization and formatting.
- Social media URL cleaning.
- Lead scoring (0-100) and tiering (HOT 🔥, WARM ⚡, COLD ❄️).
- Deduplication across leads based on phone, domain, or address.
"""
import re
import urllib.parse
import urllib.request

# Common invalid / placeholder / bot email prefixes & domains to discard
JUNK_EMAIL_EXACT = {
    "user@domain.com", "email@example.com", "name@email.com", "test@test.com",
    "info@example.com", "admin@domain.com", "user@example.com", "support@wix.com",
    "sentry@wix.com", "noreply@wix.com", "example@example.com", "domain@domain.com"
}

JUNK_EMAIL_PREFIXES = (
    "sentry@", "mailer-daemon@", "postmaster@", "noreply@", "no-reply@",
    "donotreply@", "webmaster@", "hostmaster@", "privacy@", "abuse@",
    "root@", "daemon@", "nobody@", "undisclosed@"
)

JUNK_EMAIL_DOMAINS = (
    "sentry.io", "wixpress.com", "domain.com", "example.com",
    "placeholder.com", "test.com", "invalid.com", "sample.com"
)

# Common image/asset extensions mistakenly parsed as emails (e.g. user@2x.png)
ASSET_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".pdf"
)

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def extract_domain(url: str) -> str:
    """Extract normalized root domain from website URL."""
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc.split(":")[0]
    except Exception:
        return ""


def clean_emails(emails_input, website_url: str = "") -> list:
    """Filter out bot/spam/asset emails and return cleaned, deduplicated email list.

    Domain-matched emails are sorted first.
    """
    if not emails_input:
        return []

    if isinstance(emails_input, str):
        # Split by commas, semicolons, whitespace, or pipes
        raw_list = re.split(r"[,;\s|]+", emails_input)
    elif isinstance(emails_input, (list, tuple, set)):
        raw_list = emails_input
    else:
        return []

    site_domain = extract_domain(website_url)

    cleaned = []
    seen = set()

    for item in raw_list:
        if not item or not isinstance(item, str):
            continue
        e = item.strip().lower()

        # Strip mailto: if present
        if e.startswith("mailto:"):
            e = e[7:].strip()

        # Remove trailing punctuation (commas, dots, semicolons)
        e = e.strip(".,;:!'\"<>(){}[]")

        if not e or len(e) < 6 or "@" not in e:
            continue

        # Check for asset file extensions
        if any(e.endswith(ext) for ext in ASSET_EXTENSIONS):
            continue

        # Check exact junk matches
        if e in JUNK_EMAIL_EXACT:
            continue

        # Check prefix junk
        if any(e.startswith(prefix) for prefix in JUNK_EMAIL_PREFIXES):
            continue

        # Check domain junk
        email_domain = e.split("@")[-1]
        if any(email_domain == jd or email_domain.endswith("." + jd) for jd in JUNK_EMAIL_DOMAINS):
            continue

        # Regex validity check
        if not EMAIL_REGEX.match(e):
            continue

        if e not in seen:
            seen.add(e)
            cleaned.append(e)

    # Sort domain-matching emails first
    if site_domain:
        cleaned.sort(key=lambda em: 0 if em.split("@")[-1] == site_domain or em.endswith("." + site_domain) else 1)

    return cleaned


def clean_phone(phone_input: str) -> str:
    """Format and normalize phone number into standard readable format."""
    if not phone_input or not isinstance(phone_input, str):
        return ""

    raw = phone_input.strip()
    if not raw:
        return ""

    # Keep only digits and leading +
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) < 7:
        return ""

    # Indian phone numbers (+91 or starting with 91, or 0-prefixed 11 digits, or 10-digit mobile 6-9)
    if raw.startswith("+91") or (len(digits) == 12 and digits.startswith("91")):
        core = digits[-10:]
        return f"+91 {core[0:5]} {core[5:10]}"
    elif len(digits) == 11 and digits.startswith("0") and digits[1] in "6789":
        core = digits[1:]
        return f"+91 {core[0:5]} {core[5:10]}"
    elif raw.startswith("+") and not raw.startswith("+1"):
        return raw

    # North American Numbering Plan: 11 digits starting with 1
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:11]}"
    elif len(digits) == 10:
        # If starts with 6, 7, 8, 9, commonly Indian mobile or international
        if raw.startswith("+91") or digits[0] in "6789":
            return f"+91 {digits[0:5]} {digits[5:10]}"
        return f"+1 ({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"

    return f"+{digits}" if len(digits) >= 10 else raw


def clean_social_url(url: str) -> str:
    """Clean tracking params (?igshid=, ?ref=, etc.) from social URLs."""
    if not url or not isinstance(url, str):
        return ""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        # Keep only scheme, netloc, path
        cleaned = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        return cleaned
    except Exception:
        return url.strip()


def score_lead(lead: dict) -> dict:
    """Compute 0-100 lead quality score and assign tier (HOT 🔥, WARM ⚡, COLD ❄️).

    Scoring weights:
    - Valid Email: +35 (Highest value for outbound communication)
    - Valid Phone: +25 (Direct calling channel)
    - Working Website: +20 (Legitimate online presence)
    - High Rating (>=4.5: +10, >=4.0: +5)
    - Review Count (>=20: +5)
    - Social Profile presence: +5
    """
    score = 0
    reasons = []

    emails = lead.get("emails_list", [])
    phone = lead.get("clean_phone", "")
    website = lead.get("website", "")
    rating_val = lead.get("review_rating")
    reviews_val = lead.get("review_count")

    # Email
    if emails:
        score += 35
        reasons.append("Email Available (+35)")

    # Phone
    if phone:
        score += 25
        reasons.append("Phone Available (+25)")

    # Website
    if website and extract_domain(website):
        score += 20
        reasons.append("Website Active (+20)")

    # Rating
    try:
        r = float(rating_val) if rating_val not in (None, "") else 0.0
        if r >= 4.5:
            score += 10
            reasons.append(f"Top Rated {r}★ (+10)")
        elif r >= 4.0:
            score += 5
            reasons.append(f"Rated {r}★ (+5)")
    except (ValueError, TypeError):
        pass

    # Reviews
    try:
        cnt = int(reviews_val) if reviews_val not in (None, "") else 0
        if cnt >= 20:
            score += 5
            reasons.append(f"Established ({cnt} reviews) (+5)")
    except (ValueError, TypeError):
        pass

    # Socials
    if lead.get("instagram") or lead.get("facebook") or lead.get("linkedin"):
        score += 5
        reasons.append("Social Profiles Found (+5)")

    # Assign Tier
    if score >= 70:
        tier = "HOT"
        tier_label = "HOT 🔥"
    elif score >= 40:
        tier = "WARM"
        tier_label = "WARM ⚡"
    else:
        tier = "COLD"
        tier_label = "COLD ❄️"

    return {
        "score": min(score, 100),
        "tier": tier,
        "tier_label": tier_label,
        "reasons": reasons
    }


def clean_lead(record: dict) -> dict:
    """Enrich and cleanse a single lead dictionary."""
    rec = dict(record)

    # Clean Website
    website = (rec.get("website") or "").strip()
    rec["website"] = website
    rec["domain"] = extract_domain(website)

    # Clean Email
    raw_emails = rec.get("emails") or rec.get("email") or ""
    emails_list = clean_emails(raw_emails, website)
    rec["emails_list"] = emails_list
    rec["emails"] = ", ".join(emails_list)

    # Clean Phone
    raw_phone = rec.get("phone") or ""
    rec["clean_phone"] = clean_phone(raw_phone)
    if not rec.get("phone"):
        rec["phone"] = rec["clean_phone"]

    # Clean Socials
    if "instagram" in rec:
        rec["instagram"] = clean_social_url(rec["instagram"])
    if "facebook" in rec:
        rec["facebook"] = clean_social_url(rec["facebook"])
    if "linkedin" in rec:
        rec["linkedin"] = clean_social_url(rec["linkedin"])

    # Score Lead
    scoring = score_lead(rec)
    rec["lead_score"] = scoring["score"]
    rec["lead_tier"] = scoring["tier"]
    rec["lead_tier_label"] = scoring["tier_label"]
    rec["score_reasons"] = scoring["reasons"]

    return rec


def deduplicate_leads(leads: list) -> list:
    """Deduplicate leads by matching clean phone, domain, or title+address."""
    unique = []
    seen_phones = set()
    seen_domains = set()
    seen_ident = set()

    for item in leads:
        phone = item.get("clean_phone") or item.get("phone") or ""
        domain = item.get("domain") or ""
        title = (item.get("title") or "").strip().lower()
        address = (item.get("address") or "").strip().lower()
        ident = f"{title}|{address}"

        if phone and phone in seen_phones:
            continue
        if domain and domain in seen_domains:
            continue
        if ident and ident != "|" and ident in seen_ident:
            continue

        if phone:
            seen_phones.add(phone)
        if domain:
            seen_domains.add(domain)
        if ident and ident != "|":
            seen_ident.add(ident)

        unique.append(item)

    return unique


# ── Website Social Profile Discovery (Instagram, Facebook, LinkedIn) ─────────
SOCIAL_RE = {
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)", re.I),
    "facebook":  re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/([A-Za-z0-9_.\-]+)", re.I),
    "linkedin":  re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|school)/([A-Za-z0-9_.\-%]+)", re.I),
}
_SKIP_HANDLES = {
    "", "home", "pages", "people", "help", "about", "policies", "policy",
    "legal", "tos", "privacy", "settings", "sharer", "tr", "profile.php",
    "plugins", "dialog", "intent", "login", "share.php", "permalink.php",
    "p", "reel", "reels", "explore", "stories", "tv", "watch", "events",
    "groups", "marketplace", "gaming", "photo", "hashtag", "search", "pg"
}

def _fetch_site_html(url: str, timeout: int = 8) -> str:
    """Fetch website HTML with quick timeout."""
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml"
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(300_000).decode("utf-8", "replace")
    except Exception:
        return ""

def find_socials_in_html(html: str) -> dict:
    """Extract first valid social profile links found in HTML content."""
    socials = {"instagram": "", "facebook": "", "linkedin": ""}
    for platform, pattern in SOCIAL_RE.items():
        for m in pattern.finditer(html or ""):
            handle = m.group(1).lower()
            if handle in _SKIP_HANDLES:
                continue
            if platform == "facebook" and (handle.isdigit() or len(handle) < 3):
                continue
            socials[platform] = m.group(0).rstrip('"\'/').replace("\\", "")
            break
    return socials

def enrich_socials(leads: list, max_workers: int = 6) -> list:
    """Scan websites of leads in parallel to extract Instagram, Facebook, and LinkedIn IDs."""
    from concurrent.futures import ThreadPoolExecutor

    to_scan = [l for l in leads if l.get("website") and not (l.get("instagram") and l.get("facebook"))]
    if not to_scan:
        return leads

    def _worker(lead):
        try:
            html = _fetch_site_html(lead["website"])
            if html:
                found = find_socials_in_html(html)
                for k, v in found.items():
                    if v and not lead.get(k):
                        lead[k] = clean_social_url(v)
                # Recalculate score with new socials
                scoring = score_lead(lead)
                lead["lead_score"] = scoring["score"]
                lead["lead_tier"] = scoring["tier"]
                lead["lead_tier_label"] = scoring["tier_label"]
                lead["score_reasons"] = scoring["reasons"]
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_worker, to_scan))

    return leads

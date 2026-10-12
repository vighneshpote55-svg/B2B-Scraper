#!/usr/bin/env python3
"""Advanced Lead Cleansing, Normalization, and Scoring Engine.

Features:
- Smart email cleansing: eliminates bot, crawler, sentry, CMS, and image spam emails.
- Phone number normalization and formatting.
- Social media URL cleaning.
- Lead scoring (0-100) and tiering (HOT 🔥, WARM ⚡, COLD ❄️).
- Deduplication across leads based on phone, domain, or address.
"""
import glob
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
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


def clean_phone(phone_input: str, default_country: str = "") -> str:
    """Format and normalize phone number into standard readable international format.

    Note: This performs structural normalization and formatting only; it does not claim
    telecom carrier verification or active number reachability.
    """
    if not phone_input or not isinstance(phone_input, str):
        return ""

    raw = phone_input.strip()
    if not raw:
        return ""

    has_plus = raw.startswith("+")
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) < 7:
        return ""

    # Convert international '00' prefix to standard '+' handling
    if raw.startswith("00") and len(digits) >= 9:
        digits = digits[2:]
        has_plus = True

    # International normalization based on standard country calling codes
    # 1. North American Numbering Plan (+1: US, Canada)
    if (has_plus and digits.startswith("1") and len(digits) == 11) or (not has_plus and len(digits) == 11 and digits.startswith("1")):
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:11]}"

    # 2. India (+91)
    if (has_plus and digits.startswith("91") and len(digits) == 12) or (raw.startswith("+91")):
        core = digits[-10:]
        return f"+91 {core[0:5]} {core[5:10]}"
    elif not has_plus and len(digits) == 11 and digits.startswith("0") and digits[1] in "6789":
        core = digits[1:]
        return f"+91 {core[0:5]} {core[5:10]}"

    # 3. United Kingdom (+44)
    if has_plus and digits.startswith("44") and len(digits) in (11, 12, 13):
        core = digits[2:]
        if core.startswith("0"):
            core = core[1:]
        if len(core) == 10:
            return f"+44 {core[0:4]} {core[4:]}"
        return f"+44 {core[0:3]} {core[3:7]} {core[7:]}" if len(core) >= 7 else f"+44 {core}"

    # 4. Australia (+61)
    if has_plus and digits.startswith("61") and len(digits) in (10, 11, 12):
        core = digits[2:]
        if core.startswith("0"):
            core = core[1:]
        if len(core) == 9:
            return f"+61 {core[0]} {core[1:5]} {core[5:]}"
        return f"+61 {core}"

    # 5. Germany (+49)
    if has_plus and digits.startswith("49") and len(digits) >= 9:
        core = digits[2:]
        if core.startswith("0"):
            core = core[1:]
        return f"+49 {core[0:3]} {core[3:]}"

    # 6. France (+33)
    if has_plus and digits.startswith("33") and len(digits) in (10, 11, 12):
        core = digits[2:]
        if core.startswith("0"):
            core = core[1:]
        if len(core) == 9:
            return f"+33 {core[0]} {core[1:3]} {core[3:5]} {core[5:7]} {core[7:]}"
        return f"+33 {core}"

    # 7. United Arab Emirates (+971)
    if has_plus and digits.startswith("971") and len(digits) in (11, 12):
        core = digits[3:]
        if core.startswith("0"):
            core = core[1:]
        return f"+971 {core[0:2]} {core[2:5]} {core[5:]}" if len(core) >= 7 else f"+971 {core}"

    # 8. Singapore (+65)
    if has_plus and digits.startswith("65") and len(digits) == 10:
        core = digits[2:]
        return f"+65 {core[0:4]} {core[4:]}"

    # If already had a plus and unrecognized dial code, format with +
    if has_plus:
        return f"+{digits}"

    # If 10 digits without leading +
    if len(digits) == 10:
        c = default_country.upper().strip() if default_country else ""
        if c == "IN":
            return f"+91 {digits[0:5]} {digits[5:10]}"
        elif c in ("US", "CA"):
            return f"+1 ({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
        # If starts with 6-9 in Indian context
        if digits[0] in "6789" and not c:
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


GENERIC_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com",
    "google.com", "wix.com", "wordpress.com", "site123.com", "weebly.com",
    "wa.me", "api.whatsapp.com", "youtube.com", "linktr.ee", "bio.link",
    "squarespace.com", "myshopify.com", "carrd.co"
}


def normalize_business_name(name: str) -> str:
    """Normalize business name for comparison by removing legal entity suffixes and punctuation."""
    if not name or not isinstance(name, str):
        return ""
    text = name.lower().strip()
    # Remove common business suffixes (llc, inc, ltd, corp, co, etc.)
    text = re.sub(r"\b(llc|inc|incorporated|ltd|limited|corp|corporation|co|company)\b\.?", "", text)
    # Remove punctuation
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_address(addr: str) -> str:
    """Normalize address for deduplication comparison."""
    if not addr or not isinstance(addr, str):
        return ""
    text = addr.lower().strip()
    # Normalize common roadway and unit abbreviations
    abbrevs = {
        r"\bst\b\.?": "street",
        r"\bave\b\.?": "avenue",
        r"\brd\b\.?": "road",
        r"\bblvd\b\.?": "boulevard",
        r"\bdr\b\.?": "drive",
        r"\bln\b\.?": "lane",
        r"\bste\b\.?": "suite",
        r"\bapt\b\.?": "apartment",
        r"\bfl\b\.?": "floor",
        r"\bpkwy\b\.?": "parkway"
    }
    for pat, rep in abbrevs.items():
        text = re.sub(pat, rep, text)
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def deduplicate_leads(leads: list) -> list:
    """Deduplicate leads by prioritizing stable Google Place IDs, normalized title & address,
    and corroborated title matches. Different businesses sharing a domain or phone are preserved."""
    if not leads:
        return []

    unique = []
    seen_places = set()
    seen_cids = set()
    seen_ident_pairs = set()

    for item in leads:
        pid = (item.get("place_id") or "").strip()
        cid = str(item.get("cid") or "").strip()
        raw_title = item.get("title") or ""
        norm_title = normalize_business_name(raw_title)
        raw_addr = item.get("address") or ""
        norm_addr = normalize_address(raw_addr)
        raw_phone = item.get("clean_phone") or item.get("phone") or ""
        phone = re.sub(r"[^\d]", "", str(raw_phone))[-10:] if raw_phone else ""
        domain = (item.get("domain") or "").lower().strip()

        is_dup = False

        # 1. Stable Google Place ID match
        if pid and pid in seen_places:
            is_dup = True

        # 2. Stable Google CID match
        elif cid and cid != "0" and cid in seen_cids:
            is_dup = True

        # 3. Exact normalized title + normalized address
        elif norm_title and norm_addr and (norm_title, norm_addr) in seen_ident_pairs:
            is_dup = True

        # 4. Corroborated check: only consider duplicates if title matches AND (phone or domain or address matches)
        else:
            for prev in unique:
                p_title = normalize_business_name(prev.get("title") or "")
                p_addr = normalize_address(prev.get("address") or "")
                p_phone_raw = prev.get("clean_phone") or prev.get("phone") or ""
                p_phone = re.sub(r"[^\d]", "", str(p_phone_raw))[-10:] if p_phone_raw else ""
                p_domain = (prev.get("domain") or "").lower().strip()

                # If titles are identical or one is clearly a variant/substring of the other
                title_matches = bool(norm_title and p_title and (norm_title == p_title or norm_title in p_title or p_title in norm_title))

                if title_matches:
                    # Title matches AND phone matches
                    if phone and p_phone and phone == p_phone and len(phone) >= 7:
                        is_dup = True
                        break
                    # Title matches AND non-generic domain matches
                    if domain and p_domain and domain == p_domain and domain not in GENERIC_DOMAINS:
                        is_dup = True
                        break
                    # Title matches AND address matches
                    if norm_addr and p_addr and norm_addr == p_addr:
                        is_dup = True
                        break

        if not is_dup:
            unique.append(item)
            if pid:
                seen_places.add(pid)
            if cid and cid != "0":
                seen_cids.add(cid)
            if norm_title and norm_addr:
                seen_ident_pairs.add((norm_title, norm_addr))

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

BLOCKED_HOSTNAMES = {
    "localhost", "localhost.localdomain", "local",
    "metadata.google.internal", "metadata.google",
    "instance-data", "metadata"
}

def is_safe_public_ip(ip_str: str) -> bool:
    """Verify that an IP address is public and routable, blocking loopback,
    private, link-local, multicast, reserved, unspecified, carrier-grade NAT,
    and cloud metadata destinations."""
    try:
        ip = ipaddress.ip_address(ip_str)
        # Handle IPv4-mapped IPv6 addresses (::ffff:x.x.x.x)
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped

        # Any non-global IP is not safe (covers loopback, private, link-local, CGNAT 100.64.0.0/10, ULA fc00::/7, unspecified, reserved)
        if not ip.is_global:
            return False

        if (ip.is_private or ip.is_loopback or ip.is_link_local or
            ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False

        # Specific cloud metadata addresses
        s = str(ip)
        if s in ("169.254.169.254", "169.254.170.2", "fd00:ec2::254"):
            return False

        return True
    except (ValueError, TypeError):
        return False

def resolve_and_validate_url(url: str) -> tuple:
    """Validate URL scheme and resolve DNS IPs, ensuring all are safe and public.
    
    Returns (is_safe: bool, error_reason: str, normalized_url: str, resolved_ip: str).
    """
    if not url or not isinstance(url, str):
        return False, "Empty URL", "", ""

    raw = url.strip()
    if not raw:
        return False, "Empty URL", "", ""

    # Detect non-http schemes before adding http://
    parsed_check = urllib.parse.urlparse(raw)
    scheme = parsed_check.scheme.lower()
    if scheme and scheme not in ("http", "https"):
        return False, f"Disallowed scheme: {scheme}", "", ""

    if not raw.startswith(("http://", "https://")):
        raw = "http://" + raw

    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception as e:
        return False, f"Invalid URL: {e}", "", ""

    if parsed.scheme.lower() not in ("http", "https"):
        return False, f"Disallowed scheme: {parsed.scheme}", "", ""

    # Reject credentials embedded in URL
    if parsed.username or parsed.password:
        return False, "Embedded credentials in URL are prohibited", "", ""

    hostname = parsed.hostname
    if not hostname:
        return False, "Missing hostname", "", ""

    clean_host = hostname.strip().lower().rstrip(".")
    if (clean_host in BLOCKED_HOSTNAMES or
        clean_host.endswith(".localhost") or
        clean_host.endswith(".local") or
        clean_host.endswith(".internal") or
        clean_host.endswith(".arpa")):
        return False, f"Restricted destination host: {clean_host}", "", ""

    # Check direct IP literals first
    try:
        ip_obj = ipaddress.ip_address(clean_host)
        if not is_safe_public_ip(str(ip_obj)):
            return False, f"Direct IP destination {clean_host} is non-public or restricted", "", ""
    except ValueError:
        pass

    # Pre-flight DNS resolution to check all resolved target IPs
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        addr_info = socket.getaddrinfo(clean_host, port, proto=socket.IPPROTO_TCP)
        if not addr_info:
            return False, f"DNS resolution failed for {clean_host}", "", ""
        safe_ip = None
        for item in addr_info:
            sockaddr = item[4]
            ip_str = sockaddr[0]
            if not is_safe_public_ip(ip_str):
                return False, f"Resolved destination IP {ip_str} is non-public or restricted", "", ""
            if safe_ip is None:
                safe_ip = ip_str
        return True, "", raw, safe_ip
    except Exception as e:
        return False, f"DNS resolution failed for {clean_host}: {e}", "", ""

def validate_url_for_ssrf(url: str) -> tuple:
    """Validate URL scheme and ensure all resolved DNS IPs are public.
    
    Returns (is_safe: bool, error_reason: str, normalized_url: str).
    """
    ok, reason, norm, _ = resolve_and_validate_url(url)
    return ok, reason, norm

class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Intercept HTTP redirects to ensure redirect destinations do not bypass SSRF checks."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        is_safe, reason, normalized_url = validate_url_for_ssrf(newurl)
        if not is_safe:
            return None  # Aborts redirect to private or loopback destination
        return super().redirect_request(req, fp, code, msg, headers, normalized_url)

_safe_opener = urllib.request.build_opener(SafeRedirectHandler)

class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that connects directly to a pre-validated IP address while
    preserving domain certificate validation and SNI."""
    def __init__(self, ip_str: str, port: int, server_hostname: str, timeout: int = 8, context=None):
        super().__init__(ip_str, port=port, timeout=timeout, context=context)
        self.server_hostname = server_hostname

    def connect(self):
        http.client.HTTPConnection.connect(self)
        self.sock = self._context.wrap_socket(
            self.sock,
            server_hostname=self.server_hostname
        )

def _fetch_site_html(url: str, timeout: int = 8, max_redirects: int = 3, max_size: int = 500_000) -> str:
    """Safely fetch website HTML with SSRF defense, DNS rebinding elimination via pinned sockets,
    redirect validation, content-type verification, and response byte limits."""
    current_url = url
    redirect_count = 0
    ssl_context = ssl.create_default_context()

    while redirect_count <= max_redirects:
        is_safe, reason, normalized_url, resolved_ip = resolve_and_validate_url(current_url)
        if not is_safe or not resolved_ip:
            return ""

        parsed = urllib.parse.urlparse(normalized_url)
        scheme = parsed.scheme.lower()
        port = parsed.port or (443 if scheme == "https" else 80)
        hostname = parsed.hostname
        path_query = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")

        # Format Host header (include port if non-standard)
        if (scheme == "https" and port != 443) or (scheme == "http" and port != 80):
            host_header = f"{hostname}:{port}"
        else:
            host_header = hostname

        headers = {
            "Host": host_header,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "identity",
            "Connection": "close"
        }

        conn = None
        try:
            if scheme == "https":
                conn = PinnedHTTPSConnection(
                    resolved_ip,
                    port=port,
                    server_hostname=hostname,
                    timeout=timeout,
                    context=ssl_context
                )
            else:
                conn = http.client.HTTPConnection(
                    resolved_ip,
                    port=port,
                    timeout=timeout
                )

            conn.request("GET", path_query, headers=headers)
            resp = conn.getresponse()

            # Handle redirects (301, 302, 303, 307, 308)
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.getheader("Location")
                if not location:
                    return ""
                redirect_count += 1
                if redirect_count > max_redirects:
                    return ""
                current_url = urllib.parse.urljoin(normalized_url, location)
                continue

            if resp.status != 200:
                return ""

            # Enforce Content-Type limit (allow text/html, application/xhtml+xml, text/*)
            content_type = resp.getheader("Content-Type", "").lower()
            if content_type:
                media_type = content_type.split(";")[0].strip()
                if not (media_type.startswith("text/") or media_type in ("application/xhtml+xml", "application/xml")):
                    return ""

            # Check Content-Length header limit
            cl_header = resp.getheader("Content-Length")
            if cl_header:
                try:
                    if int(cl_header) > max_size:
                        return ""
                except ValueError:
                    pass

            # Read bounded body up to max_size bytes
            body_bytes = resp.read(max_size + 1)
            if len(body_bytes) > max_size:
                return ""

            return body_bytes.decode("utf-8", errors="replace")
        except Exception:
            return ""
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

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


def load_historical_leads(data_dir: str, exclude_job_id: str = "") -> list:
    """Load all leads from past scrape JSON files in data_dir."""
    historical = []
    if not data_dir or not os.path.isdir(data_dir):
        return historical

    for filepath in glob.glob(os.path.join(data_dir, "leads_*.json")):
        filename = os.path.basename(filepath)
        job_id = filename[6:-5] if filename.startswith("leads_") and filename.endswith(".json") else ""
        if exclude_job_id and job_id == exclude_job_id:
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                leads = data.get("leads", [])
                if isinstance(leads, list):
                    historical.extend(leads)
        except Exception:
            continue
    return historical


def filter_previously_seen_leads(leads: list, historical_leads: list) -> tuple:
    """Filter out leads that already exist in historical_leads.

    Prioritizes stable Google Place IDs, normalized title+address, and corroborated title matches.
    Different businesses sharing a domain or phone are preserved.

    Returns:
        (new_leads, skipped_leads)
    """
    seen_place_ids = set()
    seen_cids = set()
    seen_title_addresses = set()
    seen_title_phones = set()
    seen_title_domains = set()

    for item in historical_leads:
        pid = (item.get("place_id") or "").strip()
        cid = str(item.get("cid") or "").strip()
        norm_title = normalize_business_name(item.get("title") or "")
        norm_addr = normalize_address(item.get("address") or "")
        raw_phone = item.get("clean_phone") or item.get("phone") or ""
        phone_digits = re.sub(r"[^\d]", "", str(raw_phone))[-10:] if raw_phone else ""
        domain = (item.get("domain") or "").lower().strip()

        if pid:
            seen_place_ids.add(pid)
        if cid and cid != "0":
            seen_cids.add(cid)
        if norm_title and norm_addr:
            seen_title_addresses.add((norm_title, norm_addr))
        if norm_title and phone_digits and len(phone_digits) >= 8:
            seen_title_phones.add((norm_title, phone_digits))
        if norm_title and domain and domain not in GENERIC_DOMAINS:
            seen_title_domains.add((norm_title, domain))

    new_leads = []
    skipped_leads = []

    for item in leads:
        pid = (item.get("place_id") or "").strip()
        cid = str(item.get("cid") or "").strip()
        norm_title = normalize_business_name(item.get("title") or "")
        norm_addr = normalize_address(item.get("address") or "")
        raw_phone = item.get("clean_phone") or item.get("phone") or ""
        phone_digits = re.sub(r"[^\d]", "", str(raw_phone))[-10:] if raw_phone else ""
        domain = (item.get("domain") or "").lower().strip()

        is_duplicate = False
        duplicate_reason = ""

        # 1. Place ID match (Exact stable Google ID)
        if pid and pid in seen_place_ids:
            is_duplicate = True
            duplicate_reason = "Already in past scrape (HIGH confidence: matched Google Place ID)"
        # 2. CID match (Exact Google Customer ID)
        elif cid and cid != "0" and cid in seen_cids:
            is_duplicate = True
            duplicate_reason = "Already in past scrape (HIGH confidence: matched Google CID)"
        # 3. Normalized Name & Address match
        elif norm_title and norm_addr and (norm_title, norm_addr) in seen_title_addresses:
            is_duplicate = True
            duplicate_reason = "Already in past scrape (HIGH confidence: matched Name & Address)"
        # 4. Corroborated Name & Phone match
        elif norm_title and phone_digits and len(phone_digits) >= 8 and (norm_title, phone_digits) in seen_title_phones:
            is_duplicate = True
            duplicate_reason = "Already in past scrape (HIGH confidence: matched Name & Phone)"
        # 5. Corroborated Name & Website domain match
        elif norm_title and domain and domain not in GENERIC_DOMAINS and (norm_title, domain) in seen_title_domains:
            is_duplicate = True
            duplicate_reason = "Already in past scrape (HIGH confidence: matched Name & Website domain)"

        if is_duplicate:
            item_copy = dict(item)
            item_copy["is_new"] = False
            item_copy["duplicate_reason"] = duplicate_reason
            skipped_leads.append(item_copy)
        else:
            item["is_new"] = True
            new_leads.append(item)
            # Add to local sets for deduplication within the current batch
            if pid:
                seen_place_ids.add(pid)
            if cid and cid != "0":
                seen_cids.add(cid)
            if norm_title and norm_addr:
                seen_title_addresses.add((norm_title, norm_addr))
            if norm_title and phone_digits and len(phone_digits) >= 8:
                seen_title_phones.add((norm_title, phone_digits))
            if norm_title and domain and domain not in GENERIC_DOMAINS:
                seen_title_domains.add((norm_title, domain))

    return new_leads, skipped_leads


def sanitize_csv_cell(val):
    """Sanitize CSV cell content to protect against spreadsheet formula injection."""
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        return val
    val_str = str(val)
    stripped = val_str.lstrip()
    if not stripped:
        return val_str
    if stripped[0] in ("=", "@", "\t", "\r"):
        return "'" + val_str
    if stripped[0] in ("+", "-"):
        try:
            float(stripped)
            return val_str
        except ValueError:
            return "'" + val_str
    return val_str


def sanitize_csv_row(row_dict: dict) -> dict:
    """Return a new dictionary with all string fields sanitized against CSV injection."""
    return {k: sanitize_csv_cell(v) for k, v in row_dict.items()}


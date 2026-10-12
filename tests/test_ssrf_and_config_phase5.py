#!/usr/bin/env python3
"""Phase 5: SSRF Defense and Configuration Hardening Tests.

Validates:
1. Strict IP space validation (is_safe_public_ip) across IPv4, IPv6, CGNAT, metadata, ULA, and mapped IPs.
2. URL scheme and hostname validation (validate_url_for_ssrf, resolve_and_validate_url).
3. DNS rebinding prevention via socket pinning (PinnedHTTPSConnection).
4. HTTP redirect re-validation and hop limit enforcement.
5. Content-Type restrictions and 500 KB response-size limits.
6. Timeout handling.
7. Port and URL standardization across docker-compose.yml, .env.example, scripts/scrape.py, scripts/scrape.sh.
8. Database connection URL protection against cloud metadata SSRF.
9. Preservation of legitimate business social profile enrichment.
"""

import http.server
import ipaddress
import os
import socket
import ssl
import sys
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

# Include project root
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from scripts.cleanser import (
    is_safe_public_ip,
    resolve_and_validate_url,
    validate_url_for_ssrf,
    _fetch_site_html,
    find_socials_in_html,
    enrich_socials,
    PinnedHTTPSConnection
)
from scripts.supabase_client import test_supabase_connection


class TestIPAddressValidation(unittest.TestCase):
    """Test is_safe_public_ip against all restricted network ranges."""

    def test_loopback_ipv4_and_ipv6_blocked(self):
        blocked = [
            "127.0.0.1", "127.0.0.2", "127.1.2.3", "127.255.255.255",
            "::1", "0:0:0:0:0:0:0:1"
        ]
        for ip in blocked:
            self.assertFalse(is_safe_public_ip(ip), f"Expected loopback {ip} to be blocked")

    def test_private_rfc1918_and_ula_blocked(self):
        blocked = [
            # 10.0.0.0/8
            "10.0.0.1", "10.254.254.254", "10.100.50.25",
            # 172.16.0.0/12
            "172.16.0.1", "172.24.1.1", "172.31.255.255",
            # 192.168.0.0/16
            "192.168.0.1", "192.168.1.100", "192.168.255.254",
            # IPv6 Unique Local fc00::/7
            "fc00::1", "fd00::1", "fd12:3456:789a::1"
        ]
        for ip in blocked:
            self.assertFalse(is_safe_public_ip(ip), f"Expected private IP {ip} to be blocked")

    def test_link_local_and_cloud_metadata_blocked(self):
        blocked = [
            # 169.254.0.0/16
            "169.254.0.1", "169.254.1.1", "169.254.169.254", "169.254.170.2",
            # IPv6 Link-Local
            "fe80::1", "fe80::dead:beef",
            # AWS IPv6 Metadata
            "fd00:ec2::254"
        ]
        for ip in blocked:
            self.assertFalse(is_safe_public_ip(ip), f"Expected metadata/link-local {ip} to be blocked")

    def test_carrier_grade_nat_blocked(self):
        blocked = [
            "100.64.0.1", "100.64.100.100", "100.127.255.254"
        ]
        for ip in blocked:
            self.assertFalse(is_safe_public_ip(ip), f"Expected CGNAT IP {ip} to be blocked")

    def test_unspecified_multicast_reserved_blocked(self):
        blocked = [
            "0.0.0.0", "::",
            "224.0.0.1", "239.255.255.250", "ff02::1", "ff05::2",
            "240.0.0.1", "255.255.255.255"
        ]
        for ip in blocked:
            self.assertFalse(is_safe_public_ip(ip), f"Expected reserved/multicast {ip} to be blocked")

    def test_ipv4_mapped_ipv6_blocked(self):
        blocked = [
            "::ffff:127.0.0.1",
            "::ffff:169.254.169.254",
            "::ffff:10.0.0.1",
            "::ffff:192.168.1.1"
        ]
        for ip in blocked:
            self.assertFalse(is_safe_public_ip(ip), f"Expected mapped IP {ip} to be blocked")

    def test_public_globally_routable_ips_allowed(self):
        allowed = [
            "8.8.8.8", "1.1.1.1", "93.184.215.14", "142.250.190.46",
            "2606:4700:4700::1111", "2001:4860:4860::8888"
        ]
        for ip in allowed:
            self.assertTrue(is_safe_public_ip(ip), f"Expected public IP {ip} to be allowed")


class TestURLValidationAndSchemes(unittest.TestCase):
    """Test URL scheme, host filtering, and input sanitization."""

    def test_disallowed_schemes_rejected(self):
        bad_schemes = [
            "file:///etc/passwd",
            "ftp://example.com/file.txt",
            "gopher://example.com",
            "data:text/html,<html>alert</html>",
            "javascript:alert(document.cookie)",
            "dict://127.0.0.1:11211",
            "ldap://127.0.0.1"
        ]
        for url in bad_schemes:
            safe, reason, _ = validate_url_for_ssrf(url)
            self.assertFalse(safe, f"Expected {url} to be rejected")

    def test_embedded_credentials_rejected(self):
        bad_urls = [
            "http://user:password@example.com/",
            "https://admin:secret@1.1.1.1/info"
        ]
        for url in bad_urls:
            safe, reason, _ = validate_url_for_ssrf(url)
            self.assertFalse(safe, f"Expected URL with credentials {url} to be rejected")
            self.assertIn("credentials", reason.lower())

    def test_restricted_domain_names_rejected(self):
        bad_domains = [
            "http://localhost",
            "http://localhost:8080/path",
            "http://app.localhost",
            "http://printer.local",
            "http://metadata.google.internal",
            "http://instance-data"
        ]
        for url in bad_domains:
            safe, reason, _ = validate_url_for_ssrf(url)
            self.assertFalse(safe, f"Expected restricted host {url} to be rejected")

    def test_direct_ip_literals_in_urls_rejected(self):
        bad_ips = [
            "http://127.0.0.1:8085/api",
            "http://10.0.0.5:8000/internal",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]:8080/test",
            "http://[fd00:ec2::254]/"
        ]
        for url in bad_ips:
            safe, reason, _ = validate_url_for_ssrf(url)
            self.assertFalse(safe, f"Expected direct IP URL {url} to be rejected")

    def test_malformed_and_empty_urls_rejected(self):
        for bad in ["", "   ", None, "http://", "not-a-valid-url!"]:
            safe, _, _ = validate_url_for_ssrf(bad)
            self.assertFalse(safe)


class TestDNSRebindingAndSocketPinning(unittest.TestCase):
    """Verify that socket connects directly to validated IP and sets proper SNI/Host."""

    def test_pinned_https_connection_sets_sni_and_host(self):
        conn = PinnedHTTPSConnection(
            ip_str="93.184.215.14",
            port=443,
            server_hostname="example.com",
            timeout=5
        )
        self.assertEqual(conn.host, "93.184.215.14")
        self.assertEqual(conn.port, 443)
        self.assertEqual(conn.server_hostname, "example.com")
        self.assertEqual(conn.timeout, 5)

    def test_resolve_and_validate_url_returns_pinned_ip(self):
        ok, reason, norm, resolved_ip = resolve_and_validate_url("https://dns.google")
        if ok:  # Depending on network availability
            self.assertTrue(ok)
            self.assertTrue(norm.startswith("https://dns.google"))
            self.assertTrue(is_safe_public_ip(resolved_ip))


class MockRedirectAndLimitServer:
    """Lightweight test HTTP server for testing redirects, limits, and timeouts."""

    def __init__(self, port=0):
        self.port = port
        self.server = None
        self.thread = None

    def start(self, handler_cls):
        self.server = http.server.HTTPServer(("127.0.0.1", self.port), handler_cls)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


class TestRedirectsContentLimitsAndTimeouts(unittest.TestCase):
    """Test redirect re-validation, Content-Type restrictions, size caps, and timeouts."""

    def test_redirect_to_loopback_is_blocked(self):
        """Simulate a public site redirecting to internal 127.0.0.1."""
        class RedirectToInternalHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:8085/secret")
                self.end_headers()
            def log_message(self, *args): pass

        srv = MockRedirectAndLimitServer()
        srv.start(RedirectToInternalHandler)
        try:
            with patch("scripts.cleanser.resolve_and_validate_url") as mock_resolve:
                def mock_side_effect(u):
                    if "safe-public-site.com" in u:
                        return True, "", f"http://127.0.0.1:{srv.port}/", "127.0.0.1"
                    else:
                        return False, "Loopback destination blocked", "", ""
                mock_resolve.side_effect = mock_side_effect

                result = _fetch_site_html("http://safe-public-site.com")
                self.assertEqual(result, "", "Redirect to internal destination must return empty string")
        finally:
            srv.stop()

    def test_redirect_loop_capped_at_three(self):
        """Server creates an infinite redirect chain."""
        class InfiniteRedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/hop")
                self.end_headers()
            def log_message(self, *args): pass

        srv = MockRedirectAndLimitServer()
        srv.start(InfiniteRedirectHandler)
        try:
            with patch("scripts.cleanser.resolve_and_validate_url") as mock_resolve:
                mock_resolve.return_value = (True, "", f"http://127.0.0.1:{srv.port}/", "127.0.0.1")
                result = _fetch_site_html(f"http://127.0.0.1:{srv.port}/", max_redirects=3)
                self.assertEqual(result, "", "Redirect exceeding max hops must abort")
                self.assertLessEqual(mock_resolve.call_count, 5)
        finally:
            srv.stop()

    def test_binary_content_type_rejected(self):
        """Server returns binary content type (e.g., image/png)."""
        class BinaryHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", "10")
                self.end_headers()
                self.wfile.write(b"\x89PNG\r\n\x1a\n\x00\x00")
            def log_message(self, *args): pass

        srv = MockRedirectAndLimitServer()
        srv.start(BinaryHandler)
        try:
            with patch("scripts.cleanser.resolve_and_validate_url") as mock_resolve:
                mock_resolve.return_value = (True, "", f"http://127.0.0.1:{srv.port}/", "127.0.0.1")
                result = _fetch_site_html(f"http://127.0.0.1:{srv.port}/")
                self.assertEqual(result, "", "Binary content type must be aborted")
        finally:
            srv.stop()

    def test_oversized_payload_aborted(self):
        """Server returns payload larger than 500 KB."""
        class OversizedHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(600_000))
                self.end_headers()
                try:
                    self.wfile.write(b"A" * 600_000)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *args): pass

        srv = MockRedirectAndLimitServer()
        srv.start(OversizedHandler)
        try:
            with patch("scripts.cleanser.resolve_and_validate_url") as mock_resolve:
                mock_resolve.return_value = (True, "", f"http://127.0.0.1:{srv.port}/", "127.0.0.1")
                result = _fetch_site_html(f"http://127.0.0.1:{srv.port}/", max_size=500_000)
                self.assertEqual(result, "", "Responses exceeding 500 KB must be aborted")
        finally:
            srv.stop()

    def test_timeout_handling(self):
        """Slow server takes longer than timeout."""
        class SlowHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(2)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html>too late</html>")
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *args): pass

        srv = MockRedirectAndLimitServer()
        srv.start(SlowHandler)
        try:
            with patch("scripts.cleanser.resolve_and_validate_url") as mock_resolve:
                mock_resolve.return_value = (True, "", f"http://127.0.0.1:{srv.port}/", "127.0.0.1")
                start = time.time()
                result = _fetch_site_html(f"http://127.0.0.1:{srv.port}/", timeout=1)
                elapsed = time.time() - start
                self.assertEqual(result, "", "Timed out request must return empty string")
                self.assertLess(elapsed, 2.5)
        finally:
            srv.stop()


class TestConfigurationStandardization(unittest.TestCase):
    """Verify that docker-compose.yml, .env.example, scripts/scrape.py, scripts/scrape.sh align on port 8085."""

    def test_env_example_matches_docker_compose_port(self):
        env_example = os.path.join(_repo_root, ".env.example")
        with open(env_example, "r") as f:
            content = f.read()
        self.assertIn("SCRAPER_PORT=8085", content)
        self.assertIn("SCRAPER_BASE_URL=http://localhost:8085", content)
        self.assertNotIn("SCRAPER_BASE_URL=http://localhost:8080", content)

    def test_scrape_py_defaults_to_8085(self):
        scrape_py = os.path.join(_repo_root, "scripts", "scrape.py")
        with open(scrape_py, "r") as f:
            content = f.read()
        self.assertIn('os.environ.get("SCRAPER_BASE_URL", "http://localhost:8085")', content)

    def test_scrape_sh_defaults_to_8085(self):
        scrape_sh = os.path.join(_repo_root, "scripts", "scrape.sh")
        with open(scrape_sh, "r") as f:
            content = f.read()
        self.assertIn('BASE="${SCRAPER_BASE_URL:-http://localhost:8085}"', content)

    def test_docker_compose_publishes_8085_by_default(self):
        dc_path = os.path.join(_repo_root, "docker-compose.yml")
        with open(dc_path, "r") as f:
            content = f.read()
        self.assertIn("127.0.0.1:${SCRAPER_PORT:-8085}:8080", content)


class TestSupabaseURLHardening(unittest.TestCase):
    """Verify that arbitrary user URLs supplied for database connections reject cloud metadata."""

    def test_cloud_metadata_supabase_url_rejected(self):
        bad_urls = [
            "http://169.254.169.254/latest/meta-data",
            "http://169.254.170.2/v2/metadata",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://instance-data/latest"
        ]
        for url in bad_urls:
            ok, msg = test_supabase_connection(url, "some-key", "leads")
            self.assertFalse(ok)
            self.assertIn("metadata", msg.lower())

    def test_disallowed_schemes_for_supabase_rejected(self):
        bad_schemes = [
            "ftp://example.com/db",
            "file:///etc/hosts",
            "gopher://example.com"
        ]
        for url in bad_schemes:
            ok, msg = test_supabase_connection(url, "some-key", "leads")
            self.assertFalse(ok)
            self.assertIn("http", msg.lower())


class TestBusinessSocialEnrichmentPreserved(unittest.TestCase):
    """Verify that normal public business website enrichment works as expected."""

    def test_html_social_extraction(self):
        sample_html = """
        <!DOCTYPE html>
        <html>
        <head><title>Acme Coffee Roasters</title></head>
        <body>
            <a href="https://www.instagram.com/acmecooffee">Instagram</a>
            <a href="https://www.facebook.com/acmecooffeeofficial">Facebook</a>
            <a href="https://linkedin.com/company/acme-coffee-corp">LinkedIn</a>
        </body>
        </html>
        """
        socials = find_socials_in_html(sample_html)
        self.assertIn("instagram.com/acmecooffee", socials["instagram"])
        self.assertIn("facebook.com/acmecooffeeofficial", socials["facebook"])
        self.assertIn("linkedin.com/company/acme-coffee-corp", socials["linkedin"])

    def test_enrich_socials_pipeline(self):
        """Simulate enrichment pipeline with mocked HTML fetcher returning legitimate business HTML."""
        sample_leads = [
            {
                "title": "Premier Plumbing",
                "phone": "+1 (512) 555-0199",
                "website": "https://premierplumbingtx.example.com",
                "category": "Plumber"
            }
        ]
        sample_html = """
        <html>
        <body>
            <a href="https://instagram.com/premierplumbing">Follow our IG</a>
        </body>
        </html>
        """
        with patch("scripts.cleanser._fetch_site_html", return_value=sample_html):
            enriched = enrich_socials(sample_leads)
            self.assertEqual(len(enriched), 1)
            self.assertIn("instagram.com/premierplumbing", enriched[0]["instagram"])
            self.assertIn("lead_score", enriched[0])
            self.assertIn("lead_tier", enriched[0])


if __name__ == "__main__":
    unittest.main()

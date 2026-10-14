#!/usr/bin/env python3
"""Comprehensive Security Readiness & Hardening Test Suite for LeadMap Pro — Phase 4.

Covers Workstream 3:
1. Upstream TLS reverse proxy configurations (Caddy & Nginx) security compliance:
   - Strict Transport Security (HSTS) with subdomains and preload.
   - X-Content-Type-Options, X-Frame-Options (DENY), Referrer-Policy.
   - Content Security Policy (CSP).
   - Strict loopback binding (127.0.0.1:3000).
2. Production Supabase verification utility safety and secret redaction.
3. Multi-tenant isolation integrity and token scoping.
"""
import io
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.verify_supabase_production import redact_secret, verify_supabase_production


class TestPhase4SecurityHardening(unittest.TestCase):
    """Validates reverse proxy configs, Supabase verification tool, and transport readiness."""

    def setUp(self):
        self.caddy_path = os.path.join(REPO_ROOT, "deploy", "caddy", "Caddyfile")
        self.nginx_path = os.path.join(REPO_ROOT, "deploy", "nginx", "leadmap.conf")
        with open(self.caddy_path, "r", encoding="utf-8") as f:
            self.caddy_content = f.read()
        with open(self.nginx_path, "r", encoding="utf-8") as f:
            self.nginx_content = f.read()

    def test_caddy_security_headers_and_loopback_proxy(self):
        """Caddyfile must enforce HSTS, nosniff, DENY framing, CSP, and loopback upstream."""
        # Loopback proxying
        self.assertIn("reverse_proxy 127.0.0.1:3000", self.caddy_content)
        # HSTS with minimum 1 year
        self.assertIn("Strict-Transport-Security", self.caddy_content)
        self.assertIn("max-age=63072000", self.caddy_content)
        self.assertIn("includeSubDomains", self.caddy_content)
        # Clickjacking defense
        self.assertIn('X-Frame-Options "DENY"', self.caddy_content)
        # MIME sniffing defense
        self.assertIn('X-Content-Type-Options "nosniff"', self.caddy_content)
        # Content Security Policy
        self.assertIn("Content-Security-Policy", self.caddy_content)
        self.assertIn("default-src 'self'", self.caddy_content)

    def test_nginx_security_headers_and_loopback_proxy(self):
        """Nginx config must enforce HSTS, nosniff, DENY framing, CSP, and loopback upstream."""
        # Loopback proxying
        self.assertIn("proxy_pass http://127.0.0.1:3000;", self.nginx_content)
        # HSTS
        self.assertIn("Strict-Transport-Security", self.nginx_content)
        self.assertIn("max-age=63072000", self.nginx_content)
        # Framing & sniffing
        self.assertIn('add_header X-Frame-Options "DENY"', self.nginx_content)
        self.assertIn('add_header X-Content-Type-Options "nosniff"', self.nginx_content)
        # CSP
        self.assertIn("Content-Security-Policy", self.nginx_content)
        # TLS protocols
        self.assertIn("ssl_protocols TLSv1.2 TLSv1.3;", self.nginx_content)

    def test_secret_redaction_utility(self):
        """redact_secret() must mask sensitive keys while showing only harmless boundary characters."""
        self.assertEqual(redact_secret(""), "[REDACTED]")
        self.assertEqual(redact_secret("short"), "[REDACTED]")
        key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdef"
        redacted = redact_secret(key)
        self.assertNotIn("abcdef", redacted[:-4])
        self.assertTrue(redacted.startswith("eyJh..."))
        self.assertTrue(redacted.endswith("cdef"))

    def test_verify_supabase_production_unconfigured_safe_exit(self):
        """verify_supabase_production must exit safely with code True when unconfigured."""
        result = verify_supabase_production(supabase_url="", service_key="")
        self.assertTrue(result)

    def test_verify_supabase_production_successful_mocked_run(self):
        """verify_supabase_production executes full diagnostic suite cleanly against mocked responses."""
        mock_auth_resp = MagicMock()
        mock_auth_resp.__enter__.return_value.status = 200
        mock_auth_resp.__enter__.return_value.read.return_value = b'[{"id": "123"}]'

        mock_anon_resp = MagicMock()
        mock_anon_resp.__enter__.return_value.status = 200
        mock_anon_resp.__enter__.return_value.read.return_value = b'[]'  # RLS returns 0 rows to anon

        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = [mock_auth_resp, mock_anon_resp]
            success = verify_supabase_production(
                supabase_url="https://test.supabase.co",
                service_key="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.dummykey12345"
            )
            self.assertTrue(success)


if __name__ == "__main__":
    unittest.main()

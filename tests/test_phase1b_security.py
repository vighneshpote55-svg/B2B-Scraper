#!/usr/bin/env python3
"""Comprehensive Phase 1B Security Regression Test Suite.

Covers:
1. Strengthened SSRF defenses (mixed public/private DNS, IPv4-mapped IPv6, redirects, loops, socket assertions).
2. Comprehensive CSV formula injection & record boundary defense (whitespace, math prefixes, coordinates, quotes).
3. Concurrent session transaction safety (no lost sessions, atomic revocation under load).
4. Multi-user job ownership isolation (prevent cross-tenant data access).
5. Supabase RLS policy syntax and privilege verification.
"""
import concurrent.futures
import csv
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.cleanser import (
    is_safe_public_ip,
    resolve_and_validate_url,
    validate_url_for_ssrf,
    _fetch_site_html,
    PinnedHTTPSConnection,
    sanitize_csv_cell,
    sanitize_csv_row
)

import dashboard
from dashboard import (
    _add_session,
    _revoke_session,
    _revoke_sessions_for_user,
    _load_sessions,
    _get_job_owner_id,
    _atomic_json_write,
    SESSION_LOCK
)


class TestSSRFStrengthenedDefense(unittest.TestCase):
    """Test SSRF defenses with mock DNS and mock sockets (never probing real external/cloud targets)."""

    def test_mixed_public_private_dns_answers(self):
        """If a hostname resolves to multiple IP addresses where any IP is private, it MUST be rejected."""
        # Scenario: Dual-homed or DNS rebinding attacker hostname returning both 93.184.216.34 and 127.0.0.1
        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 80)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 80)),
            ]
            ok, reason, _, _ = resolve_and_validate_url("http://attacker-dual-homed.com")
            self.assertFalse(ok)
            self.assertIn("non-public or restricted", reason.lower())

        # Scenario: Hostname returning 8.8.8.8 and 169.254.169.254 (cloud metadata)
        with patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 80)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", 80)),
            ]
            ok, reason, _, _ = resolve_and_validate_url("http://metadata-rebinding.com")
            self.assertFalse(ok)
            self.assertIn("non-public or restricted", reason.lower())

    def test_ipv4_mapped_ipv6_addresses(self):
        """IPv4-mapped IPv6 addresses representing private/loopback/metadata MUST be rejected."""
        mapped_vectors = [
            ("::ffff:127.0.0.1", False),
            ("::ffff:127.0.1.1", False),
            ("::ffff:169.254.169.254", False),
            ("::ffff:10.0.0.1", False),
            ("::ffff:172.16.0.1", False),
            ("::ffff:192.168.1.1", False),
            ("::ffff:0.0.0.0", False),
            ("::ffff:8.8.8.8", True),  # Mapped public IP
        ]
        for ip_str, expected in mapped_vectors:
            self.assertEqual(is_safe_public_ip(ip_str), expected, f"Failed for mapped IP {ip_str}")

        # Test URL with mapped IPv6 literal
        ok, reason, _, _ = resolve_and_validate_url("http://[::ffff:127.0.0.1]/secret")
        self.assertFalse(ok)

    def test_redirect_to_loopback_or_private_aborts(self):
        """Simulate a public site returning a 302 redirect to loopback or cloud metadata."""
        with patch("scripts.cleanser.resolve_and_validate_url") as mock_val:
            mock_val.side_effect = [
                (True, "", "http://example.com", "93.184.216.34"),
                (False, "Direct IP destination 127.0.0.1 is non-public or restricted", "", "")
            ]

            mock_conn = MagicMock()
            mock_resp = MagicMock()
            mock_resp.status = 302
            mock_resp.getheader.side_effect = lambda h: "http://127.0.0.1:8080/admin" if h.lower() == "location" else None
            mock_conn.getresponse.return_value = mock_resp

            with patch("http.client.HTTPConnection", return_value=mock_conn):
                result = _fetch_site_html("http://example.com")
                self.assertEqual(result, "")

    def test_redirect_loop_capped_at_three(self):
        """Infinite redirect chain must terminate after max_redirects (3) and return empty string."""
        with patch("scripts.cleanser.resolve_and_validate_url") as mock_val:
            mock_val.return_value = (True, "", "http://example.com/loop", "93.184.216.34")

            mock_conn = MagicMock()
            mock_resp = MagicMock()
            mock_resp.status = 302
            mock_resp.getheader.side_effect = lambda h: "http://example.com/loop" if h.lower() == "location" else None
            mock_conn.getresponse.return_value = mock_resp

            with patch("http.client.HTTPConnection", return_value=mock_conn):
                result = _fetch_site_html("http://example.com/loop", max_redirects=3)
                self.assertEqual(result, "")
                self.assertEqual(mock_conn.request.call_count, 4)

    def test_rejected_destinations_never_open_socket(self):
        """Destinations that fail SSRF validation must abort before any socket is created or opened."""
        with patch("socket.socket.connect") as mock_sock_connect:
            with patch("http.client.HTTPConnection.connect") as mock_http_connect:
                # 1. Loopback
                res = _fetch_site_html("http://127.0.0.1:8080/internal")
                self.assertEqual(res, "")
                mock_sock_connect.assert_not_called()
                mock_http_connect.assert_not_called()

                # 2. Cloud metadata
                res = _fetch_site_html("http://169.254.169.254/latest/meta-data/")
                self.assertEqual(res, "")
                mock_sock_connect.assert_not_called()
                mock_http_connect.assert_not_called()

                # 3. Disallowed scheme
                res = _fetch_site_html("file:///etc/passwd")
                self.assertEqual(res, "")
                mock_sock_connect.assert_not_called()
                mock_http_connect.assert_not_called()


class TestCSVFormulaInjectionAndRecordBoundaries(unittest.TestCase):
    """Test comprehensive CSV sanitization and record boundary protections."""

    def test_all_formula_prefixes(self):
        """Verify =, @, +, -, tab, CR, LF, pipe, and percent triggers receive apostrophe prefix."""
        bad_inputs = [
            "=SUM(A1:A10)",
            "@SUM(A1:A10)",
            "+cmd|' /C calc'!A0",
            "-cmd|' /C calc'!A0",
            "|cmd|' /C calc'!A0",
            "%HOMEPATH%",
            "\t=calc",
            "\r=calc",
            "\n=calc",
            "   =calc",
            "   +calc",
            "   @calc",
            "\u00A0=calc",  # Unicode non-breaking space
            "\ufeff=calc", # BOM prefix
        ]
        for val in bad_inputs:
            sanitized = sanitize_csv_cell(val)
            self.assertTrue(
                sanitized.startswith("'"),
                f"Value {repr(val)} was not neutralized; got {repr(sanitized)}"
            )

    def test_legitimate_numeric_values_preserved(self):
        """Negative numbers, geographic coordinates, and clean numeric integers must remain unescaped."""
        good_numbers = [
            ("-97.7431", "-97.7431"),
            ("+100", "+100"),
            ("-42", "-42"),
            ("-0.5", "-0.5"),
            (42, 42),
            (3.1415, 3.1415),
            (0, 0),
        ]
        for val, expected in good_numbers:
            self.assertEqual(sanitize_csv_cell(val), expected)

    def test_phone_numbers_with_spaces_escaped(self):
        """Phone numbers starting with '+' containing non-numeric spaces must be safely escaped."""
        phone = "+1 (555) 123-4567"
        sanitized = sanitize_csv_cell(phone)
        self.assertEqual(sanitized, "'+1 (555) 123-4567")

    def test_csv_record_boundaries_and_escaping(self):
        """Embedded quotes, commas, and newlines must not split rows or corrupt columns."""
        leads = [
            {
                "title": 'Acme, Inc. "Premier"',
                "category": "Line 1\nLine 2",
                "phone": "+1 555 1234",
                "lead_score": 90,
                "latitude": "-33.8688"
            }
        ]
        sanitized_row = sanitize_csv_row(leads[0])
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(sanitized_row.keys()))
        writer.writeheader()
        writer.writerow(sanitized_row)

        output_csv = buf.getvalue()
        reader = list(csv.DictReader(io.StringIO(output_csv)))
        self.assertEqual(len(reader), 1)
        parsed = reader[0]
        self.assertEqual(parsed["title"], 'Acme, Inc. "Premier"')
        self.assertEqual(parsed["category"], "Line 1\nLine 2")
        self.assertEqual(parsed["phone"], "'+1 555 1234")
        self.assertEqual(parsed["latitude"], "-33.8688")


class TestSessionConcurrencyAndLocking(unittest.TestCase):
    """Test thread-safe session mutation and absence of race-condition lost updates."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sessions_path = os.path.join(self.temp_dir, "test_sessions.json")
        self.original_sessions_file = dashboard.SESSIONS_FILE
        dashboard.SESSIONS_FILE = self.sessions_path

    def tearDown(self):
        dashboard.SESSIONS_FILE = self.original_sessions_file
        if os.path.exists(self.sessions_path):
            os.remove(self.sessions_path)
        os.rmdir(self.temp_dir)

    def test_concurrent_logins_and_logouts(self):
        """Simultaneous logins and logouts must not overwrite or lose each other."""
        num_users = 20
        created_tokens = []

        def worker_login(idx):
            token = f"token_{idx}"
            email = f"user_{idx}@test.io"
            _add_session(token, email, f"u_{idx}")
            return token

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker_login, i) for i in range(num_users)]
            for f in concurrent.futures.as_completed(futures):
                created_tokens.append(f.result())

        sessions = _load_sessions()
        self.assertEqual(len(sessions), num_users)

        def worker_revoke(token):
            _revoke_session(token)

        to_revoke = created_tokens[:10]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker_revoke, t) for t in to_revoke]
            concurrent.futures.wait(futures)

        final_sessions = _load_sessions()
        self.assertEqual(len(final_sessions), num_users - len(to_revoke))
        for t in to_revoke:
            self.assertNotIn(t, final_sessions)


class TestJobOwnershipIsolation(unittest.TestCase):
    """Test multi-user authorization and job ownership boundaries."""

    def test_job_owner_id_resolution(self):
        job_id = "test-job-ownership-01"
        user_a_id = "u_alice_123"

        with dashboard.JOB_LOCK:
            dashboard.JOB_CACHE[job_id] = {
                "id": job_id,
                "status": "ok",
                "user_id": user_a_id,
                "params": {"keyword": "Dentists", "user_id": user_a_id}
            }

        try:
            owner = _get_job_owner_id(job_id)
            self.assertEqual(owner, user_a_id)

            user_b_id = "u_bob_456"
            self.assertNotEqual(owner, user_b_id)
        finally:
            with dashboard.JOB_LOCK:
                dashboard.JOB_CACHE.pop(job_id, None)


class TestSupabasePolicySecurity(unittest.TestCase):
    """Verify that migration scripts and schemas strictly avoid unsafe public policies."""

    def test_no_broad_authenticated_policies_in_migration_001(self):
        mig_path = os.path.join(REPO_ROOT, "migrations", "001_harden_rls_policies.sql")
        with open(mig_path, "r", encoding="utf-8") as f:
            sql = f.read()

        # Must not contain unconditional authenticated policy
        self.assertNotIn(
            'CREATE POLICY "Allow authenticated read and insert on leads"',
            sql,
            "Unsafe broad authenticated policy on leads found in migration 001!"
        )
        self.assertNotIn(
            "FOR ALL TO authenticated USING (true)",
            sql,
            "Unconditional 'TO authenticated' policy found!"
        )

        # Must enforce RLS on users, leads, and scrape_history
        self.assertIn("ALTER TABLE IF EXISTS public.users ENABLE ROW LEVEL SECURITY;", sql)
        self.assertIn("ALTER TABLE IF EXISTS public.leads ENABLE ROW LEVEL SECURITY;", sql)
        self.assertIn("ALTER TABLE IF EXISTS public.scrape_history ENABLE ROW LEVEL SECURITY;", sql)

        # Must revoke from anon and authenticated
        self.assertIn("REVOKE ALL ON TABLE public.users FROM anon;", sql)
        self.assertIn("REVOKE ALL ON TABLE public.users FROM authenticated;", sql)
        self.assertIn("REVOKE ALL ON TABLE public.leads FROM anon;", sql)
        self.assertIn("REVOKE ALL ON TABLE public.leads FROM authenticated;", sql)
        self.assertIn("REVOKE ALL ON TABLE public.scrape_history FROM anon;", sql)
        self.assertIn("REVOKE ALL ON TABLE public.scrape_history FROM authenticated;", sql)

        # Must grant to service_role
        self.assertIn("GRANT ALL ON TABLE public.leads TO service_role;", sql)
        self.assertIn("GRANT ALL ON TABLE public.scrape_history TO service_role;", sql)
        self.assertIn("GRANT ALL ON TABLE public.users TO service_role;", sql)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Comprehensive Security Regression Test Suite for LeadMap Pro — Phase 3: Supabase Security.

Covers:
1. Verification that supabase_schema.sql contains zero broad anon or authenticated policies.
2. Verification that RLS is strictly enabled on leads, scrape_history, and users tables.
3. Verification that migration 002_harden_supabase_rls_phase3.sql safely drops all legacy permissive policies.
4. Verification that migration 002 revokes table grants from untrusted roles (anon, authenticated).
5. Verification that migration 002 grants least-privilege policies restricted strictly to service_role.
6. Verification that docs/SUPABASE_SECURITY_VERIFICATION.sql contains all necessary catalog audit checks.
7. Verification that GET /api/supabase/status never leaks project URL or key preview to unauthenticated callers.
8. Verification that GET /api/supabase/status returns masked key preview only to authenticated users (never raw key).
9. Verification of is_likely_anon_key diagnostic detection.
10. Verification that test_connection rejects publishable/anon keys with clear service_role guidance.
"""
import base64
import json
import os
import re
import secrets
import sys
import time
import unittest
import urllib.request
import urllib.error

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.supabase_client import is_likely_anon_key, test_connection


class TestPhase3SupabaseSecurity(unittest.TestCase):
    """Integration and unit tests for Phase 3 Supabase security hardening."""

    BASE_URL = "http://127.0.0.1:3000"

    def _request(self, method, path, data=None, token=None):
        url = f"{self.BASE_URL}{path}"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                status = response.status
                body_bytes = response.read()
                try:
                    res_json = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    res_json = None
                return status, res_json, response.headers
        except urllib.error.HTTPError as e:
            err_body = e.read()
            try:
                res_json = json.loads(err_body.decode("utf-8"))
            except Exception:
                res_json = None
            return e.code, res_json, e.headers

    def _create_test_account(self):
        email = f"p3_sec_{time.time_ns()}_{secrets.token_hex(4)}@leadmappro.io"
        password = "ValidPhase3Password123!"
        status, data, _ = self._request("POST", "/api/auth/signup", data={
            "email": email,
            "password": password,
            "name": "Phase 3 Tester"
        })
        self.assertEqual(status, 200)
        return email, password, data.get("token")

    def test_schema_has_no_broad_anon_or_authenticated_policies(self):
        """Verify supabase_schema.sql does NOT define broad policies for anon or authenticated."""
        schema_path = os.path.join(REPO_ROOT, "supabase_schema.sql")
        with open(schema_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Must not contain "TO anon" or "TO authenticated" in any policy
        policy_blocks = re.findall(r"CREATE\s+POLICY\s+[\s\S]*?;", content, re.IGNORECASE)
        for block in policy_blocks:
            self.assertNotIn("to anon", block.lower(), f"Found insecure policy granting anon access: {block}")
            self.assertNotIn("to authenticated", block.lower(), f"Found insecure policy granting authenticated access: {block}")

        # All CREATE POLICY statements must strictly target service_role
        for block in policy_blocks:
            self.assertIn("to service_role", block.lower(), f"Policy missing strict service_role target: {block}")

    def test_schema_enables_rls_on_all_tables(self):
        """Verify supabase_schema.sql enables RLS on leads, scrape_history, and users."""
        schema_path = os.path.join(REPO_ROOT, "supabase_schema.sql")
        with open(schema_path, "r", encoding="utf-8") as f:
            content = f.read()

        for tbl in ("leads", "scrape_history", "users"):
            pattern = rf"ALTER\s+TABLE\s+(public\.)?{tbl}\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY"
            self.assertTrue(bool(re.search(pattern, content, re.IGNORECASE)), f"RLS not enabled on table {tbl}")

    def test_migration_002_drops_legacy_policies(self):
        """Verify migration 002 drops all legacy permissive policies safely."""
        mig_path = os.path.join(REPO_ROOT, "migrations", "002_harden_supabase_rls_phase3.sql")
        self.assertTrue(os.path.isfile(mig_path), "Migration 002 file not found!")

        with open(mig_path, "r", encoding="utf-8") as f:
            mig_content = f.read()

        legacy_policies = [
            "Allow anon read and insert on leads",
            "Allow anon read and write on scrape_history",
            "Allow anon read and write on users",
            "Allow authenticated read and insert on leads",
            "Strict service role only on users"
        ]
        for pol in legacy_policies:
            drop_stmt = f'DROP POLICY IF EXISTS "{pol}"'
            self.assertIn(drop_stmt, mig_content, f"Migration 002 missing drop statement for: {pol}")

    def test_migration_002_revokes_untrusted_grants(self):
        """Verify migration 002 revokes table permissions from anon and authenticated."""
        mig_path = os.path.join(REPO_ROOT, "migrations", "002_harden_supabase_rls_phase3.sql")
        with open(mig_path, "r", encoding="utf-8") as f:
            mig_content = f.read()

        for tbl in ("public.leads", "public.scrape_history", "public.users"):
            expected = f"REVOKE ALL ON TABLE {tbl} FROM anon, authenticated;"
            self.assertIn(expected, mig_content, f"Missing revoke statement for {tbl}")

    def test_migration_002_grants_service_role_least_privilege(self):
        """Verify migration 002 establishes strict service_role policies."""
        mig_path = os.path.join(REPO_ROOT, "migrations", "002_harden_supabase_rls_phase3.sql")
        with open(mig_path, "r", encoding="utf-8") as f:
            mig_content = f.read()

        for tbl in ("leads", "scrape_history", "users"):
            self.assertIn(f'"Service role full access on {tbl}"', mig_content)
            grant_stmt = f"GRANT ALL ON TABLE public.{tbl} TO service_role;"
            self.assertIn(grant_stmt, mig_content)

    def test_verification_sql_contains_required_audit_checks(self):
        """Verify docs/SUPABASE_SECURITY_VERIFICATION.sql contains catalog audit queries."""
        verify_path = os.path.join(REPO_ROOT, "docs", "SUPABASE_SECURITY_VERIFICATION.sql")
        self.assertTrue(os.path.isfile(verify_path), "Verification SQL file not found!")

        with open(verify_path, "r", encoding="utf-8") as f:
            sql_content = f.read()

        # Must check pg_tables for rowsecurity
        self.assertIn("pg_tables", sql_content)
        self.assertIn("rowsecurity", sql_content)

        # Must check pg_policies
        self.assertIn("pg_policies", sql_content)
        self.assertIn("unhardened_policy_count", sql_content)

        # Must check role_table_grants
        self.assertIn("information_schema.role_table_grants", sql_content)

    def test_supabase_status_unauthenticated_does_not_leak_url_or_key(self):
        """Verify unauthenticated callers cannot access Supabase project URL or key preview."""
        status, data, _ = self._request("GET", "/api/supabase/status")
        self.assertEqual(status, 200)

        # High-level indicators are allowed
        self.assertIn("configured", data)
        self.assertIn("connected", data)
        self.assertIn("default_backend", data)

        # Secrets, URLs, and key previews MUST be omitted for unauthenticated requests
        self.assertNotIn("url", data, "Unauthenticated /api/supabase/status leaked Supabase project URL!")
        self.assertNotIn("key_preview", data, "Unauthenticated /api/supabase/status leaked key_preview!")

    def test_supabase_status_authenticated_masks_key(self):
        """Verify authenticated users receive masked key preview, never raw key."""
        _, _, token = self._create_test_account()
        status, data, _ = self._request("GET", "/api/supabase/status", token=token)
        self.assertEqual(status, 200)

        self.assertIn("url", data)
        self.assertIn("key_preview", data)
        key_preview = data.get("key_preview", "")
        # If a key is configured, key_preview must be masked with "..." or "***"
        if key_preview:
            self.assertTrue("..." in key_preview or key_preview == "***", "Key preview is not properly masked!")
            # Must not be the complete raw key
            self.assertNotEqual(len(key_preview), len(os.environ.get("SUPABASE_KEY", "")))

    def test_anon_key_diagnostic_detection(self):
        """Verify is_likely_anon_key correctly identifies publishable/anon vs service_role keys."""
        # Modern Supabase publishable key prefix
        self.assertTrue(is_likely_anon_key("sb_publishable_dummy1234567890"))
        self.assertFalse(is_likely_anon_key("sb_secret_dummy1234567890"))

        # Simulated JWT with role: "anon"
        header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
        anon_payload = base64.urlsafe_b64encode(b'{"role":"anon","iss":"supabase"}').decode().rstrip("=")
        service_payload = base64.urlsafe_b64encode(b'{"role":"service_role","iss":"supabase"}').decode().rstrip("=")
        dummy_sig = "dummy_signature_bytes_12345"

        anon_jwt = f"{header}.{anon_payload}.{dummy_sig}"
        service_jwt = f"{header}.{service_payload}.{dummy_sig}"

        self.assertTrue(is_likely_anon_key(anon_jwt))
        self.assertFalse(is_likely_anon_key(service_jwt))
        self.assertFalse(is_likely_anon_key("some_regular_string"))

    def test_test_connection_rejects_anon_key(self):
        """Verify test_connection proactively rejects publishable/anon keys with service_role guidance."""
        ok, msg = test_connection("https://dummy.supabase.co", "sb_publishable_test_token_123", "leads")
        self.assertFalse(ok)
        self.assertIn("service_role", msg.lower())
        self.assertIn("anon", msg.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)

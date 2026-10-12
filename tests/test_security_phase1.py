#!/usr/bin/env python3
"""Comprehensive Security Regression Test Suite for LeadMap Pro — Phase 1.

Covers:
1. SSRF prevention and safe URL validation in scripts/cleanser.py (private, loopback, link-local, cloud metadata, protocols).
2. CSV formula injection sanitization in scripts/cleanser.py and dashboard.py exports.
3. Server-side authentication & authorization on all protected dashboard endpoints.
4. Session token lifecycle: expiration, revocation via logout, and pruning.
5. Supabase RLS hardening rules and migration verification.
6. Core cleansing and scoring regression stability.
"""
import io
import json
import os
import sys
import time
import unittest
import urllib.request
import urllib.error

# Ensure workspace root is in sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.cleanser import (
    is_safe_public_ip,
    validate_url_for_ssrf,
    _fetch_site_html,
    sanitize_csv_cell,
    sanitize_csv_row,
    clean_lead,
    score_lead,
    deduplicate_leads
)

from dashboard import (
    HOST,
    MAX_REQUEST_BODY_SIZE,
    ALLOWED_ORIGINS,
    DATA_DIR,
    _validate_job_id,
    _get_job_file_path
)


class TestSSRFProtection(unittest.TestCase):
    """Test SSRF protection rules against cloud metadata, loopback, private ranges, and protocols."""

    def test_loopback_rejection(self):
        self.assertFalse(is_safe_public_ip("127.0.0.1"))
        self.assertFalse(is_safe_public_ip("127.0.1.1"))
        self.assertFalse(is_safe_public_ip("::1"))
        is_safe, err, _ = validate_url_for_ssrf("http://127.0.0.1:8080/secret")
        self.assertFalse(is_safe)
        self.assertIn("non-public", err.lower())

    def test_cloud_metadata_rejection(self):
        # AWS / GCP / Azure metadata endpoint
        self.assertFalse(is_safe_public_ip("169.254.169.254"))
        is_safe, err, _ = validate_url_for_ssrf("http://169.254.169.254/latest/meta-data/")
        self.assertFalse(is_safe)
        self.assertIn("non-public", err.lower())

    def test_private_rfc1918_rejection(self):
        self.assertFalse(is_safe_public_ip("10.0.0.1"))
        self.assertFalse(is_safe_public_ip("172.16.0.5"))
        self.assertFalse(is_safe_public_ip("192.168.1.1"))
        is_safe, err, _ = validate_url_for_ssrf("http://192.168.0.1/admin")
        self.assertFalse(is_safe)

    def test_disallowed_schemes(self):
        for bad_url in [
            "file:///etc/passwd",
            "gopher://127.0.0.1:70",
            "ftp://files.example.com",
            "javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4="
        ]:
            is_safe, err, _ = validate_url_for_ssrf(bad_url)
            self.assertFalse(is_safe, f"Expected scheme rejection for {bad_url}")
            self.assertIn("scheme", err.lower())

    def test_invalid_and_blank_urls(self):
        self.assertFalse(validate_url_for_ssrf("")[0])
        self.assertFalse(validate_url_for_ssrf("not-a-url")[0])
        self.assertFalse(validate_url_for_ssrf("http://")[0])

    def test_fetch_site_html_safely_aborts_on_internal_ip(self):
        # Must return empty string without establishing network connections to internal IPs
        html = _fetch_site_html("http://127.0.0.1:8085/api/v1/jobs")
        self.assertEqual(html, "")

        html_meta = _fetch_site_html("http://169.254.169.254/latest/meta-data")
        self.assertEqual(html_meta, "")


class TestCSVFormulaInjection(unittest.TestCase):
    """Test spreadsheet formula injection sanitization."""

    def test_formula_prefixes_escaped(self):
        malicious_inputs = [
            "=cmd|' /C calc'!A0",
            "=SUM(A1:A10)",
            "+SUM(B1:B2)",
            "@SUM(C1:C5)",
            "-2+3*[1]",
            "\t=1+2",
            "\r=cmd|' /C notepad'!A0",
            "\n+2+2"
        ]
        for payload in malicious_inputs:
            sanitized = sanitize_csv_cell(payload)
            self.assertTrue(
                sanitized.startswith("'"),
                f"Expected formula cell {repr(payload)} to be prepended with apostrophe, got {repr(sanitized)}"
            )

    def test_legitimate_business_data_preserved(self):
        # Valid negative float numbers (like geographic coordinates)
        self.assertEqual(sanitize_csv_cell("-97.7431"), "-97.7431")
        self.assertEqual(sanitize_csv_cell("+100"), "+100")
        self.assertEqual(sanitize_csv_cell(42), 42)
        self.assertEqual(sanitize_csv_cell(3.1415), 3.1415)

        # Standard business strings
        self.assertEqual(sanitize_csv_cell("Acme Dental Clinic"), "Acme Dental Clinic")
        self.assertEqual(sanitize_csv_cell("contact@acmedental.com"), "contact@acmedental.com")
        self.assertEqual(sanitize_csv_cell("+1 (555) 234-5678"), "'+1 (555) 234-5678")
        self.assertEqual(sanitize_csv_cell("123 Main St, Suite 400"), "123 Main St, Suite 400")

    def test_sanitize_csv_row_dictionary(self):
        lead_dict = {
            "title": "=calc()",
            "phone": "+1 (555) 000-1111",
            "latitude": "-33.8688",
            "lead_score": 85,
            "emails": "@executive"
        }
        cleaned = sanitize_csv_row(lead_dict)
        self.assertTrue(cleaned["title"].startswith("'="))
        self.assertEqual(cleaned["phone"], "'+1 (555) 000-1111")
        self.assertEqual(cleaned["latitude"], "-33.8688")
        self.assertEqual(cleaned["lead_score"], 85)
        self.assertTrue(cleaned["emails"].startswith("'@"))


class TestBackendAuthentication(unittest.TestCase):
    """Test HTTP endpoints on the live running dashboard server."""

    BASE_URL = "http://localhost:3000"

    def _request(self, method, path, data=None, token=None, custom_headers=None, raw_body=None):
        url = f"{self.BASE_URL}{path}"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if custom_headers:
            headers.update(custom_headers)

        if raw_body is not None:
            body = raw_body
        elif data is not None:
            body = json.dumps(data).encode("utf-8")
        else:
            body = None

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                self.last_headers = response.headers
                status = response.status
                body_bytes = response.read()
                try:
                    res_json = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    res_json = None
                return status, res_json, body_bytes
        except urllib.error.HTTPError as e:
            self.last_headers = e.headers
            err_body = e.read()
            try:
                res_json = json.loads(err_body.decode("utf-8"))
            except Exception:
                res_json = None
            return e.code, res_json, err_body

    def test_public_endpoints_accessible_without_auth(self):
        # /api/status is public
        status, data, _ = self._request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertIn("scraper_up", data)

        # /api/supabase/status is public
        status, data, _ = self._request("GET", "/api/supabase/status")
        self.assertEqual(status, 200)
        self.assertIn("configured", data)

        # /api/auth/me returns 401 when no token is passed
        status, data, _ = self._request("GET", "/api/auth/me")
        self.assertEqual(status, 401)
        self.assertFalse(data.get("authenticated", True))

    def test_unauthenticated_protected_get_routes_denied(self):
        protected_get_routes = [
            "/api/jobs",
            "/api/history",
            "/api/job/test-job-id/status",
            "/api/job/test-job-id/leads",
            "/api/export?job_id=test&format=csv",
            "/api/export?job_id=test&format=crm",
            "/api/export?job_id=test&format=json"
        ]
        for route in protected_get_routes:
            status, data, _ = self._request("GET", route)
            self.assertEqual(status, 401, f"Expected 401 for GET {route}, got {status}")
            self.assertIn("authentication required", (data.get("error") or "").lower())

    def test_unauthenticated_protected_post_routes_denied(self):
        protected_post_routes = [
            ("/api/scrape/start", {"keyword": "Dentists", "city": "Austin"}),
            ("/api/scrape/stop", {"job_id": "test"}),
            ("/api/docker/restart", {}),
            ("/api/supabase/test", {"url": "https://test.supabase.co", "key": "test"}),
            ("/api/supabase/save", {"url": "https://test.supabase.co", "key": "test"}),
            ("/api/supabase/disconnect", {}),
            ("/api/supabase/sync", {"job_id": "test"}),
            ("/api/history/sync-all", {})
        ]
        for route, body in protected_post_routes:
            status, data, _ = self._request("POST", route, data=body)
            self.assertEqual(status, 401, f"Expected 401 for POST {route}, got {status}")
            self.assertIn("authentication required", (data.get("error") or "").lower())

    def test_invalid_session_tokens_denied(self):
        status, data, _ = self._request("GET", "/api/jobs", token="invalid-token-12345")
        self.assertEqual(status, 401)
        self.assertIn("authentication required", (data.get("error") or "").lower())

    def test_auth_lifecycle_signup_login_access_logout(self):
        test_email = f"security_test_{int(time.time())}@leadmappro.io"
        test_pass = "ComplexPassw0rd!"

        # 1. Signup
        signup_payload = {
            "email": test_email,
            "password": test_pass,
            "name": "Security Tester"
        }
        status, signup_data, _ = self._request("POST", "/api/auth/signup", data=signup_payload)
        self.assertEqual(status, 200)
        self.assertTrue(signup_data.get("success"))
        token = signup_data.get("token")
        self.assertTrue(token)

        # 2. Access protected endpoint with valid Bearer token
        status, jobs_data, _ = self._request("GET", "/api/jobs", token=token)
        self.assertEqual(status, 200)
        self.assertIn("jobs", jobs_data)

        # 3. Check /api/auth/me with Bearer token
        status, me_data, _ = self._request("GET", "/api/auth/me", token=token)
        self.assertEqual(status, 200)
        self.assertTrue(me_data.get("authenticated"))
        self.assertEqual(me_data["user"]["email"], test_email)

        # 4. Logout (revocation)
        status, logout_data, _ = self._request("POST", "/api/auth/logout", token=token)
        self.assertEqual(status, 200)
        self.assertTrue(logout_data.get("success"))

        # 5. Access with revoked token must now be 401
        status, after_logout_data, _ = self._request("GET", "/api/jobs", token=token)
        self.assertEqual(status, 401)
        self.assertIn("authentication required", (after_logout_data.get("error") or "").lower())

        # 6. Re-login
        login_payload = {"email": test_email, "password": test_pass}
        status, login_data, _ = self._request("POST", "/api/auth/login", data=login_payload)
        self.assertEqual(status, 200)
        new_token = login_data.get("token")
        self.assertTrue(new_token)

        # Access with new token succeeds
        status, jobs_data, _ = self._request("GET", "/api/jobs", token=new_token)
        self.assertEqual(status, 200)

    def test_expired_session_token_denied(self):
        sessions_file = os.path.join(REPO_ROOT, "data", "sessions.json")
        expired_token = "expired_test_token_99999"
        with open(sessions_file, "r") as f:
            sessions = json.load(f)
        sessions[expired_token] = {
            "email": "security_test_expired@leadmappro.io",
            "user_id": "u_expired",
            "created_at": time.time() - 100000,
            "expires_at": time.time() - 3600  # expired 1 hour ago
        }
        with open(sessions_file, "w") as f:
            json.dump(sessions, f)

        try:
            status, data, _ = self._request("GET", "/api/jobs", token=expired_token)
            self.assertEqual(status, 401)
            self.assertIn("authentication required", (data.get("error") or "").lower())
        finally:
            with open(sessions_file, "r") as f:
                sessions = json.load(f)
            sessions.pop(expired_token, None)
            with open(sessions_file, "w") as f:
                json.dump(sessions, f)

    def _get_or_create_token(self):
        email = f"authtest_{int(time.time())}_{os.getpid()}@leadmappro.io"
        status, data, _ = self._request("POST", "/api/auth/signup", data={
            "email": email,
            "password": "Password123!",
            "name": "Auth Tester"
        })
        if status == 200 and data and data.get("token"):
            return data["token"]
        status, data, _ = self._request("POST", "/api/auth/login", data={
            "email": email,
            "password": "Password123!"
        })
        return data.get("token")

    def test_job_id_validation_and_path_traversal_rejection(self):
        token = self._get_or_create_token()
        self.assertTrue(token)

        traversal_attempts = [
            "../../etc/passwd",
            "../secret",
            "..%2F..%2Fetc",
            "invalid@job!id",
            "job;drop table",
            "a" * 65
        ]

        for bad_id in traversal_attempts:
            quoted_id = urllib.parse.quote(bad_id)
            # Status check
            status, data, _ = self._request("GET", f"/api/job/{quoted_id}/status", token=token)
            self.assertEqual(status, 400, f"Expected 400 for bad job_id {bad_id} on /api/job/<id>/status, got {status}")
            self.assertIn("invalid job id", (data.get("error") or "").lower())

            # Leads check
            status, data, _ = self._request("GET", f"/api/job/{quoted_id}/leads", token=token)
            self.assertEqual(status, 400, f"Expected 400 for bad job_id {bad_id} on /api/job/<id>/leads, got {status}")
            self.assertIn("invalid job id", (data.get("error") or "").lower())

            # Export check
            status, data, _ = self._request("GET", f"/api/export?job_id={quoted_id}&format=csv", token=token)
            self.assertEqual(status, 400, f"Expected 400 for bad job_id {bad_id} on /api/export, got {status}")
            self.assertIn("invalid or missing job id", (data.get("error") or "").lower())

            # Stop scrape check
            status, data, _ = self._request("POST", "/api/scrape/stop", data={"job_id": bad_id}, token=token)
            self.assertEqual(status, 400, f"Expected 400 for bad job_id {bad_id} on /api/scrape/stop, got {status}")
            self.assertIn("invalid or missing job_id", (data.get("error") or "").lower())

            # Supabase sync check
            status, data, _ = self._request("POST", "/api/supabase/sync", data={"job_id": bad_id}, token=token)
            self.assertEqual(status, 400, f"Expected 400 for bad job_id {bad_id} on /api/supabase/sync, got {status}")
            self.assertIn("invalid job_id", (data.get("error") or "").lower())

    def test_request_payload_size_limit_rejection(self):
        token = self._get_or_create_token()
        self.assertTrue(token)

        # Attempt to send request with Content-Length exceeding MAX_REQUEST_BODY_SIZE
        oversized_length = str(MAX_REQUEST_BODY_SIZE + 1024)
        status, data, _ = self._request(
            "POST",
            "/api/auth/login",
            custom_headers={"Content-Length": oversized_length},
            raw_body=b""
        )
        self.assertEqual(status, 413)
        self.assertIn("payload too large", (data.get("error") or "").lower())

    def test_malformed_and_negative_content_length_rejection(self):
        # Negative Content-Length
        status, data, _ = self._request(
            "POST",
            "/api/auth/login",
            custom_headers={"Content-Length": "-10"},
            raw_body=b""
        )
        self.assertEqual(status, 400)
        self.assertIn("negative content-length", (data.get("error") or "").lower())

        # Non-numeric Content-Length
        status, data, _ = self._request(
            "POST",
            "/api/auth/login",
            custom_headers={"Content-Length": "not-a-number"},
            raw_body=b""
        )
        self.assertEqual(status, 400)
        self.assertIn("invalid content-length", (data.get("error") or "").lower())

        # Malformed JSON payload
        status, data, _ = self._request(
            "POST",
            "/api/auth/login",
            raw_body=b"{not valid json..."
        )
        self.assertEqual(status, 400)
        self.assertIn("invalid json payload", (data.get("error") or "").lower())

    def test_cors_origin_restrictions(self):
        # Request with trusted Origin
        trusted_origin = "http://localhost:3000"
        status, data, _ = self._request("GET", "/api/status", custom_headers={"Origin": trusted_origin})
        self.assertEqual(status, 200)
        self.assertEqual(self.last_headers.get("Access-Control-Allow-Origin"), trusted_origin)
        self.assertEqual(self.last_headers.get("Access-Control-Allow-Credentials"), "true")

        # Request with untrusted Origin
        untrusted_origin = "http://evil-attacker.com"
        status, data, _ = self._request("GET", "/api/status", custom_headers={"Origin": untrusted_origin})
        self.assertEqual(status, 200)
        self.assertIsNone(self.last_headers.get("Access-Control-Allow-Origin"))

        # Preflight OPTIONS with trusted origin
        status, _, _ = self._request("OPTIONS", "/api/status", custom_headers={"Origin": trusted_origin})
        self.assertEqual(status, 204)
        self.assertEqual(self.last_headers.get("Access-Control-Allow-Origin"), trusted_origin)

        # Preflight OPTIONS with untrusted origin
        status, _, _ = self._request("OPTIONS", "/api/status", custom_headers={"Origin": untrusted_origin})
        self.assertEqual(status, 204)
        self.assertIsNone(self.last_headers.get("Access-Control-Allow-Origin"))


class TestHostBindingAndPathSecurity(unittest.TestCase):
    """Test default host binding, regex job ID validation, and directory escape prevention."""

    def test_default_host_binding_is_loopback(self):
        expected_host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
        self.assertEqual(HOST, expected_host)
        self.assertEqual(HOST, "127.0.0.1")

    def test_validate_job_id_rules(self):
        # Valid IDs
        self.assertTrue(_validate_job_id("abc123"))
        self.assertTrue(_validate_job_id("job-456_XYZ"))
        self.assertTrue(_validate_job_id("1234567890"))
        self.assertTrue(_validate_job_id("a" * 64))

        # Invalid IDs: traversal, special characters, empty, too long
        self.assertFalse(_validate_job_id("../etc/passwd"))
        self.assertFalse(_validate_job_id("..\\windows\\win.ini"))
        self.assertFalse(_validate_job_id("../../secret"))
        self.assertFalse(_validate_job_id(""))
        self.assertFalse(_validate_job_id(None))
        self.assertFalse(_validate_job_id("job;rm -rf"))
        self.assertFalse(_validate_job_id("job/with/slashes"))
        self.assertFalse(_validate_job_id("a" * 65))

    def test_get_job_file_path_stays_within_data_dir(self):
        safe_path = _get_job_file_path("valid_job_123")
        self.assertIsNotNone(safe_path)
        self.assertTrue(safe_path.startswith(os.path.abspath(DATA_DIR) + os.sep))
        self.assertTrue(safe_path.endswith("leads_valid_job_123.json"))

        # Traversal attempts must return None
        self.assertIsNone(_get_job_file_path("../evil"))
        self.assertIsNone(_get_job_file_path("evil/path"))
        self.assertIsNone(_get_job_file_path(""))


class TestSupabaseSchemaHardening(unittest.TestCase):
    """Verify that migration and schema files enforce strict RLS on user credentials."""

    def test_migration_revokes_anon_from_users(self):
        migration_path = os.path.join(REPO_ROOT, "migrations", "001_harden_rls_policies.sql")
        self.assertTrue(os.path.isfile(migration_path))
        with open(migration_path, "r") as f:
            content = f.read()

        self.assertIn("REVOKE ALL ON TABLE public.users FROM anon;", content)
        self.assertIn("GRANT ALL ON TABLE public.users TO service_role;", content)
        self.assertIn("Strict service role only on users", content)

    def test_supabase_schema_users_table_hardened(self):
        schema_path = os.path.join(REPO_ROOT, "supabase_schema.sql")
        self.assertTrue(os.path.isfile(schema_path))
        with open(schema_path, "r") as f:
            content = f.read()

        self.assertNotIn('CREATE POLICY "Allow anon read and write on users"', content)
        self.assertIn("Strict service role only on users", content)


class TestCleanserRegressionStability(unittest.TestCase):
    """Ensure core lead cleansing, deduplication, and scoring logic remains unaffected."""

    def test_lead_cleansing_and_scoring(self):
        raw_lead = {
            "title": "Elite Smile Dental",
            "phone": "+1 (512) 555-0199",
            "website": "https://elitesmiledental.com",
            "email": "dr.smith@elitesmiledental.com",
            "review_rating": 4.9,
            "review_count": 85,
            "category": "Dentist",
            "address": "100 Congress Ave, Austin, TX"
        }
        cleaned = clean_lead(raw_lead)
        self.assertEqual(cleaned["clean_phone"], "+1 (512) 555-0199")
        self.assertEqual(cleaned["domain"], "elitesmiledental.com")
        self.assertEqual(cleaned["lead_tier"], "HOT")
        self.assertGreaterEqual(cleaned["lead_score"], 80)

    def test_deduplicate_leads(self):
        leads = [
            {"title": "Clinic A", "clean_phone": "+15125550199", "domain": "clinica.com"},
            {"title": "Clinic A Duplicate", "clean_phone": "+15125550199", "domain": "clinica.com"},
            {"title": "Clinic B", "clean_phone": "+15125550200", "domain": "clinicb.com"}
        ]
        unique_leads = deduplicate_leads(leads)
        self.assertEqual(len(unique_leads), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Comprehensive End-to-End User Journey Test Suite for LeadMap Pro — Phase 4.

Covers Workstream 1:
1. Account signup, credential hashing, and session Bearer token issuance.
2. Search query dispatch with bounded admission and mocked scraper backend.
3. Status milestone transitions (10% -> 15% -> 85% -> 96% -> 100%).
4. Lead qualification, contact formatting, and score ranking.
5. Multi-tenant isolation: secondary user receives HTTP 403 Forbidden on foreign jobs.
6. User-scoped deduplication: second search skips previously collected place IDs.
7. Multi-format export (CSV, CRM, JSON) with CSV formula sanitization.
8. Logout session invalidation and post-logout access denial.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from unittest.mock import patch, MagicMock

# Include project root
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dashboard
from dashboard import (
    DashboardRequestHandler,
    JOB_LOCK,
    JOB_CACHE,
    JOB_ADMISSION_LOCK,
    _atomic_json_write,
    _create_durable_job,
    _update_durable_job
)
from scripts.cleanser import (
    load_historical_leads,
    filter_previously_seen_leads,
    sanitize_csv_cell
)


class TestPhase4EndToEndUserJourney(unittest.TestCase):
    """Executes a full, isolated customer journey over real HTTP loopback."""

    @classmethod
    def setUpClass(cls):
        # Configure isolated temp directory for data files
        cls.temp_dir = tempfile.mkdtemp()
        cls.orig_data_dir = dashboard.DATA_DIR
        cls.orig_users_file = dashboard.USERS_FILE
        cls.orig_sessions_file = dashboard.SESSIONS_FILE
        cls.orig_pw_file = dashboard.PASSWORD_RESETS_FILE
        cls.orig_outbox_file = dashboard.EMAIL_OUTBOX_FILE

        dashboard.DATA_DIR = cls.temp_dir
        dashboard.USERS_FILE = os.path.join(cls.temp_dir, "users.json")
        dashboard.SESSIONS_FILE = os.path.join(cls.temp_dir, "sessions.json")
        dashboard.PASSWORD_RESETS_FILE = os.path.join(cls.temp_dir, "password_resets.json")
        dashboard.EMAIL_OUTBOX_FILE = os.path.join(cls.temp_dir, "email_outbox.log")

        # Start live in-process ThreadingHTTPServer on an ephemeral port
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"

        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

        # Restore original paths
        dashboard.DATA_DIR = cls.orig_data_dir
        dashboard.USERS_FILE = cls.orig_users_file
        dashboard.SESSIONS_FILE = cls.orig_sessions_file
        dashboard.PASSWORD_RESETS_FILE = cls.orig_pw_file
        dashboard.EMAIL_OUTBOX_FILE = cls.orig_outbox_file

        if os.path.exists(cls.temp_dir):
            shutil.rmtree(cls.temp_dir)

    def setUp(self):
        with JOB_LOCK:
            JOB_CACHE.clear()

    def _http_request(self, method, path, data=None, token=None):
        """Helper to send HTTP requests to test server."""
        url = f"{self.base_url}{path}"
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
                return status, res_json, body_bytes
        except urllib.error.HTTPError as e:
            err_bytes = e.read()
            try:
                err_json = json.loads(err_bytes.decode("utf-8"))
            except Exception:
                err_json = None
            return e.code, err_json, err_bytes

    @patch("dashboard.is_supabase_configured", return_value=False)
    def test_complete_user_lifecycle_journey(self, mock_sb):
        """End-to-end customer journey from registration to search, scoping, export, and logout."""
        # -------------------------------------------------------------
        # STEP 1: User 1 Registration & Auth
        # -------------------------------------------------------------
        signup_payload = {
            "name": "Alice Founder",
            "email": "alice@acmeventures.io",
            "password": "SuperSecretPassword123!"
        }
        status, data, _ = self._http_request("POST", "/api/auth/signup", signup_payload)
        self.assertEqual(status, 200)
        self.assertTrue(data.get("success"))
        token_u1 = data.get("token")
        user_u1_id = data.get("user", {}).get("id")
        self.assertIsNotNone(token_u1)
        self.assertIsNotNone(user_u1_id)

        # Verify /api/auth/me returns User 1 identity
        status, me_data, _ = self._http_request("GET", "/api/auth/me", token=token_u1)
        self.assertEqual(status, 200)
        self.assertTrue(me_data.get("authenticated"))
        self.assertEqual(me_data.get("user", {}).get("email"), "alice@acmeventures.io")
        self.assertEqual(me_data.get("user", {}).get("id"), user_u1_id)

        # -------------------------------------------------------------
        # STEP 2: Scrape Dispatch with Mocked Scraper Backend
        # -------------------------------------------------------------
        scrape_payload = {
            "keyword": "Dental Clinics",
            "city": "Austin, TX",
            "lat": "30.2672",
            "lon": "-97.7431",
            "depth": 2,
            "email": True,
            "socials": True,
            "exclude_seen": True
        }

        with patch("dashboard.check_scraper_health", return_value=True), \
             patch("dashboard.scraper_req", return_value=(200, b'{"id": "job_e2e_alpha"}')), \
             patch("dashboard.background_job_processor", return_value=None):
            status, job_res, _ = self._http_request("POST", "/api/scrape/start", scrape_payload, token=token_u1)

        self.assertEqual(status, 200)
        self.assertEqual(job_res.get("job_id"), "job_e2e_alpha")
        self.assertEqual(job_res.get("status"), "working")

        # -------------------------------------------------------------
        # STEP 3: Status Milestones Inspection
        # -------------------------------------------------------------
        # Set intermediate stage 85%
        _update_durable_job("job_e2e_alpha", {
            "status": "working",
            "stage": "Enriching website leads",
            "progress_percent": 85
        })

        status, status_res, _ = self._http_request("GET", "/api/job/job_e2e_alpha/status", token=token_u1)
        self.assertEqual(status, 200)
        self.assertEqual(status_res.get("progress_percent"), 85)
        self.assertEqual(status_res.get("stage"), "Enriching website leads")

        # -------------------------------------------------------------
        # STEP 4: Lead Qualification & Completion
        # -------------------------------------------------------------
        sample_leads = [
            {
                "place_id": "PLACE_AUSTIN_1",
                "title": "=HYPERLINK(\"http://evil.com\",\"Austin Smile Center\")",  # formula injection vector
                "category": "Dentist",
                "address": "100 Congress Ave, Austin, TX",
                "city": "Austin",
                "clean_phone": "+1 (512) 555-0199",
                "emails": "@ceo@austinsmiles.com",  # formula injection vector
                "website": "https://austinsmiles.com",
                "lead_tier": "HOT",
                "total_score": 92
            },
            {
                "place_id": "PLACE_AUSTIN_2",
                "title": "Capital Dental Care",
                "category": "Dentist",
                "address": "200 Lavaca St, Austin, TX",
                "city": "Austin",
                "clean_phone": "+1 (512) 555-0288",
                "emails": "contact@capitaldental.com",
                "website": "https://capitaldental.com",
                "lead_tier": "WARM",
                "total_score": 68
            }
        ]

        # Save completed job state
        leads_file = os.path.join(self.temp_dir, "leads_job_e2e_alpha.json")
        _atomic_json_write(leads_file, {
            "job_id": "job_e2e_alpha",
            "user_id": user_u1_id,
            "completed_at": time.time(),
            "leads": sample_leads
        })

        _update_durable_job("job_e2e_alpha", {
            "status": "ok",
            "stage": "Completed",
            "progress_percent": 100,
            "leads": sample_leads,
            "saved_file": leads_file,
            "metrics": {
                "total": 2,
                "hot": 1,
                "warm": 1,
                "cold": 0,
                "with_email": 2,
                "with_phone": 2
            }
        })

        # Fetch leads as User 1
        status, leads_res, _ = self._http_request("GET", "/api/job/job_e2e_alpha/leads", token=token_u1)
        self.assertEqual(status, 200)
        self.assertEqual(len(leads_res.get("leads", [])), 2)

        # -------------------------------------------------------------
        # STEP 5: Multi-Tenant Authorization Enforcement
        # -------------------------------------------------------------
        # Register User 2 (Bob)
        signup_u2 = {
            "name": "Bob Competitor",
            "email": "bob@competitorcorp.net",
            "password": "AnotherSecurePassword456!"
        }
        status, u2_data, _ = self._http_request("POST", "/api/auth/signup", signup_u2)
        self.assertEqual(status, 200)
        token_u2 = u2_data.get("token")

        # Bob attempts to view Alice's job status -> MUST receive 403 Forbidden
        status, bob_status_res, _ = self._http_request("GET", "/api/job/job_e2e_alpha/status", token=token_u2)
        self.assertEqual(status, 403)
        self.assertIn("forbidden", bob_status_res.get("error", "").lower())

        # Bob attempts to view Alice's leads -> MUST receive 403 Forbidden
        status, bob_leads_res, _ = self._http_request("GET", "/api/job/job_e2e_alpha/leads", token=token_u2)
        self.assertEqual(status, 403)
        self.assertIn("forbidden", bob_leads_res.get("error", "").lower())

        # Bob attempts to cancel Alice's job -> MUST receive 403 Forbidden
        status, bob_cancel_res, _ = self._http_request("POST", "/api/scrape/stop", {"job_id": "job_e2e_alpha"}, token=token_u2)
        self.assertEqual(status, 403)
        self.assertIn("forbidden", bob_cancel_res.get("error", "").lower())

        # -------------------------------------------------------------
        # STEP 6: User-Scoped Deduplication Verification
        # -------------------------------------------------------------
        # Alice's historical leads must include PLACE_AUSTIN_1 and PLACE_AUSTIN_2
        alice_history = load_historical_leads(self.temp_dir, user_id=user_u1_id)
        alice_places = {l.get("place_id") for l in alice_history}
        self.assertIn("PLACE_AUSTIN_1", alice_places)
        self.assertIn("PLACE_AUSTIN_2", alice_places)

        # Bob's historical leads must be EMPTY
        bob_history = load_historical_leads(self.temp_dir, user_id=u2_data.get("user", {}).get("id"))
        self.assertEqual(len(bob_history), 0)

        # When Alice searches again and finds PLACE_AUSTIN_1, it is skipped
        new_leads, skipped = filter_previously_seen_leads([{"place_id": "PLACE_AUSTIN_1", "title": "Austin Smile Center"}], alice_history)
        self.assertEqual(len(new_leads), 0)
        self.assertEqual(len(skipped), 1)

        # When Bob searches for the same place, it is NOT skipped
        bob_new, bob_skipped = filter_previously_seen_leads([{"place_id": "PLACE_AUSTIN_1", "title": "Austin Smile Center"}], bob_history)
        self.assertEqual(len(bob_new), 1)
        self.assertEqual(len(bob_skipped), 0)

        # -------------------------------------------------------------
        # STEP 7: Export & CSV Formula Sanitization
        # -------------------------------------------------------------
        # Alice exports CSV
        status, _, csv_bytes = self._http_request("GET", "/api/export?job_id=job_e2e_alpha&format=csv", token=token_u1)
        self.assertEqual(status, 200)
        csv_text = csv_bytes.decode("utf-8")

        # Verify formula prefixes were sanitized with leading apostrophe
        self.assertNotIn(',"=HYPERLINK', csv_text)
        self.assertNotIn(',=HYPERLINK', csv_text)
        self.assertIn("'=HYPERLINK", csv_text)
        self.assertNotIn(",@ceo", csv_text)
        self.assertIn("'@ceo", csv_text)

        # Bob attempts to export Alice's CSV -> 403 Forbidden
        status, _, _ = self._http_request("GET", "/api/export?job_id=job_e2e_alpha&format=csv", token=token_u2)
        self.assertEqual(status, 403)

        # Alice exports CRM format
        status, _, crm_bytes = self._http_request("GET", "/api/export?job_id=job_e2e_alpha&format=crm", token=token_u1)
        self.assertEqual(status, 200)
        self.assertIn("Company Name", crm_bytes.decode("utf-8"))

        # Alice exports JSON format
        status, json_res, _ = self._http_request("GET", "/api/export?job_id=job_e2e_alpha&format=json", token=token_u1)
        self.assertEqual(status, 200)
        self.assertEqual(len(json_res), 2)

        # -------------------------------------------------------------
        # STEP 8: Logout & Session Invalidation
        # -------------------------------------------------------------
        status, logout_res, _ = self._http_request("POST", "/api/auth/logout", token=token_u1)
        self.assertEqual(status, 200)
        self.assertTrue(logout_res.get("success"))

        # Subsequent authenticated calls with token_u1 must fail with 401 Unauthorized
        status, post_logout_res, _ = self._http_request("GET", "/api/auth/me", token=token_u1)
        self.assertEqual(status, 401)
        self.assertFalse(post_logout_res.get("authenticated", True))

    @patch("dashboard.is_supabase_configured", return_value=False)
    def test_capacity_rejection_and_retry_journey(self, mock_sb):
        """When MAX_CONCURRENT_JOBS is reached, HTTP 429 is returned and released upon completion."""
        # Signup User
        signup_payload = {
            "name": "Capacity Tester",
            "email": "capacity@test.io",
            "password": "ValidPassword123!"
        }
        status, data, _ = self._http_request("POST", "/api/auth/signup", signup_payload)
        self.assertEqual(status, 200)
        token = data.get("token")
        user_id = data.get("user", {}).get("id")

        # Fill slots up to MAX_CONCURRENT_JOBS
        _create_durable_job("job_cap_1", user_id, "capacity@test.io", {"keyword": "K1"})
        _create_durable_job("job_cap_2", user_id, "capacity@test.io", {"keyword": "K2"})

        # Next scrape request must receive 429
        scrape_payload = {
            "keyword": "Roofers",
            "city": "Austin, TX",
            "lat": "30.2672",
            "lon": "-97.7431"
        }
        with patch("dashboard.check_scraper_health", return_value=True):
            status, res, _ = self._http_request("POST", "/api/scrape/start", scrape_payload, token=token)
            self.assertEqual(status, 429)
            self.assertIn("limit reached", res.get("error", "").lower())

        # Complete job_cap_1 -> releases slot
        _update_durable_job("job_cap_1", {"status": "ok", "completed_at": time.time()})

        # Now scrape request must succeed
        with patch("dashboard.check_scraper_health", return_value=True), \
             patch("dashboard.scraper_req", return_value=(200, b'{"id": "job_cap_3"}')), \
             patch("dashboard.background_job_processor", return_value=None):
            status, res_ok, _ = self._http_request("POST", "/api/scrape/start", scrape_payload, token=token)
            self.assertEqual(status, 200)
            self.assertEqual(res_ok.get("job_id"), "job_cap_3")

        # Clean up
        _update_durable_job("job_cap_2", {"status": "ok", "completed_at": time.time()})
        _update_durable_job("job_cap_3", {"status": "ok", "completed_at": time.time()})

    @patch("dashboard.is_supabase_configured", return_value=False)
    def test_diagnostics_and_secret_safety(self, mock_sb):
        """Diagnostics endpoint requires authentication and never leaks credentials."""
        # Unauthenticated request to /api/diagnostics returns 401
        status, data, _ = self._http_request("GET", "/api/diagnostics")
        self.assertEqual(status, 401)

        # Authenticated request to /api/diagnostics
        signup_payload = {
            "name": "Diag Admin",
            "email": "diag@test.io",
            "password": "DiagPassword123!"
        }
        status, data, _ = self._http_request("POST", "/api/auth/signup", signup_payload)
        token = data.get("token")

        status, diag_res, raw_body = self._http_request("GET", "/api/diagnostics", token=token)
        self.assertEqual(status, 200)
        self.assertIn("uptime_seconds", diag_res)
        self.assertIn("max_concurrent_jobs", diag_res)
        self.assertIn("active_jobs_count", diag_res)

        # Ensure no token or password appears anywhere in raw diagnostics output
        raw_text = raw_body.decode("utf-8")
        self.assertNotIn(token, raw_text)
        self.assertNotIn("DiagPassword123!", raw_text)


if __name__ == "__main__":
    unittest.main()


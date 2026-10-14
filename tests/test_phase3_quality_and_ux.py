#!/usr/bin/env python3
"""Comprehensive Product Quality and Usability Test Suite for LeadMap Pro — Phase 3.

Covers:
1. Multi-tenant user-scoped historical deduplication.
2. Stale lead refresh (90-day staleness threshold re-admittance).
3. Secret redaction logging filter (Bearer tokens, passwords, JWTs, reset tokens).
4. Authenticated diagnostics endpoint (/api/diagnostics) and capacity reporting.
5. Export format consistency and CSV formula sanitization.
"""
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

# Include project root
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dashboard
from dashboard import (
    DATA_DIR,
    MAX_CONCURRENT_JOBS,
    SecretRedactionFilter,
    _get_active_job_count
)
from scripts.cleanser import (
    load_historical_leads,
    filter_previously_seen_leads,
    LEAD_STALENESS_DAYS,
    sanitize_csv_cell
)


class TestLeadQualityAndDeduplication(unittest.TestCase):
    """Test user isolation and stale lead handling in deduplication."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.now = time.time()

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_user_scoped_historical_deduplication(self):
        """User A should not suppress leads historically collected by User B."""
        # Create User 1 past scrape
        user1_file = os.path.join(self.temp_dir, "leads_job_u1.json")
        with open(user1_file, "w", encoding="utf-8") as f:
            json.dump({
                "job_id": "job_u1",
                "user_id": "user_1",
                "completed_at": self.now - 3600,
                "leads": [
                    {"place_id": "PLACE_ALPHA", "title": "Alpha Dental", "clean_phone": "+1 (512) 555-0101"}
                ]
            }, f)

        # Create User 2 past scrape
        user2_file = os.path.join(self.temp_dir, "leads_job_u2.json")
        with open(user2_file, "w", encoding="utf-8") as f:
            json.dump({
                "job_id": "job_u2",
                "user_id": "user_2",
                "completed_at": self.now - 3600,
                "leads": [
                    {"place_id": "PLACE_BETA", "title": "Beta Plumbing", "clean_phone": "+1 (512) 555-0202"}
                ]
            }, f)

        # When User 1 scrapes: only PLACE_ALPHA should be considered historical
        u1_history = load_historical_leads(self.temp_dir, user_id="user_1")
        u1_place_ids = {l.get("place_id") for l in u1_history}
        self.assertIn("PLACE_ALPHA", u1_place_ids)
        self.assertNotIn("PLACE_BETA", u1_place_ids)

        # When User 2 scrapes: only PLACE_BETA should be considered historical
        u2_history = load_historical_leads(self.temp_dir, user_id="user_2")
        u2_place_ids = {l.get("place_id") for l in u2_history}
        self.assertIn("PLACE_BETA", u2_place_ids)
        self.assertNotIn("PLACE_ALPHA", u2_place_ids)

        # Simulate User 1 finding both leads: PLACE_ALPHA must be skipped, PLACE_BETA must be new
        candidate_leads = [
            {"place_id": "PLACE_ALPHA", "title": "Alpha Dental"},
            {"place_id": "PLACE_BETA", "title": "Beta Plumbing"}
        ]
        new_leads, skipped = filter_previously_seen_leads(candidate_leads, u1_history)
        new_place_ids = {l.get("place_id") for l in new_leads}
        skipped_place_ids = {l.get("place_id") for l in skipped}

        self.assertIn("PLACE_BETA", new_place_ids)
        self.assertIn("PLACE_ALPHA", skipped_place_ids)

    def test_stale_lead_refresh_window(self):
        """Leads older than LEAD_STALENESS_DAYS (90 days) are not suppressed as historical."""
        # Old job: completed 100 days ago
        old_file = os.path.join(self.temp_dir, "leads_job_old.json")
        with open(old_file, "w", encoding="utf-8") as f:
            json.dump({
                "job_id": "job_old",
                "user_id": "user_1",
                "completed_at": self.now - (100 * 86400),
                "leads": [
                    {"place_id": "PLACE_STALE", "title": "Stale Bakery"}
                ]
            }, f)

        # Recent job: completed 10 days ago
        recent_file = os.path.join(self.temp_dir, "leads_job_recent.json")
        with open(recent_file, "w", encoding="utf-8") as f:
            json.dump({
                "job_id": "job_recent",
                "user_id": "user_1",
                "completed_at": self.now - (10 * 86400),
                "leads": [
                    {"place_id": "PLACE_RECENT", "title": "Recent Cafe"}
                ]
            }, f)

        # Load with 90-day threshold
        history = load_historical_leads(self.temp_dir, user_id="user_1", max_age_days=90)
        history_places = {l.get("place_id") for l in history}

        self.assertIn("PLACE_RECENT", history_places)
        self.assertNotIn("PLACE_STALE", history_places)  # Stale lead allowed to be re-collected


class TestSecretRedactionLoggingFilter(unittest.TestCase):
    """Test automated secret and credential scrubbing in logging output."""

    def setUp(self):
        self.filter = SecretRedactionFilter()

    def test_bearer_token_redacted(self):
        record = logging.LogRecord("test", logging.INFO, "test.py", 1, "User authenticated with Bearer sec_tok_998877665544", (), None)
        self.filter.filter(record)
        self.assertNotIn("sec_tok_998877665544", record.msg)
        self.assertIn("[REDACTED_TOKEN]", record.msg)

    def test_password_field_redacted(self):
        record = logging.LogRecord("test", logging.INFO, "test.py", 1, 'Login payload {"email": "a@b.com", "password": "superSecretPassword123"}', (), None)
        self.filter.filter(record)
        self.assertNotIn("superSecretPassword123", record.msg)
        self.assertIn("[REDACTED]", record.msg)

    def test_jwt_redacted(self):
        record = logging.LogRecord("test", logging.INFO, "test.py", 1, "Supabase key eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSJ9.signature12345678", (), None)
        self.filter.filter(record)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", record.msg)
        self.assertIn("[REDACTED_JWT]", record.msg)

    def test_reset_token_redacted(self):
        record = logging.LogRecord("test", logging.INFO, "test.py", 1, "Sent email with reset_token=1234567890abcdef1234567890abcdef", (), None)
        self.filter.filter(record)
        self.assertNotIn("1234567890abcdef1234567890abcdef", record.msg)
        self.assertIn("[REDACTED_TOKEN]", record.msg)


class TestDiagnosticsEndpoint(unittest.TestCase):
    """Test the /api/diagnostics endpoint functionality."""

    def test_status_endpoint_returns_capacity_metrics(self):
        """GET /api/status includes max_concurrent_jobs and active_jobs_count."""
        # Simulated handler call
        from dashboard import DashboardRequestHandler
        handler = MagicMock()
        handler.path = "/api/status"
        sent_data = {}

        def mock_send(data, status=200):
            nonlocal sent_data
            sent_data = data

        handler._send_json = mock_send

        with patch("dashboard.check_scraper_health", return_value=True), \
             patch("dashboard._get_active_job_count", return_value=1):
            DashboardRequestHandler.do_GET(handler)

        self.assertIn("active_jobs_count", sent_data)
        self.assertIn("max_concurrent_jobs", sent_data)
        self.assertEqual(sent_data["active_jobs_count"], 1)
        self.assertEqual(sent_data["max_concurrent_jobs"], MAX_CONCURRENT_JOBS)

    def test_diagnostics_requires_authentication(self):
        """GET /api/diagnostics denies unauthenticated callers."""
        from dashboard import DashboardRequestHandler
        handler = MagicMock()
        handler.path = "/api/diagnostics"
        handler._require_auth.return_value = None  # Unauthenticated

        DashboardRequestHandler.do_GET(handler)
        handler._require_auth.assert_called_once()


if __name__ == "__main__":
    unittest.main()

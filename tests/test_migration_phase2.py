#!/usr/bin/env python3
"""Phase 2 Test Suite: Additive Migration 003 & scrape_jobs PostgREST Client.

Verifies:
1. Migration 003 SQL syntax and statements.
2. Additive columns and indexes on public.leads.
3. Creation and structure of public.scrape_jobs table with idempotency indexes.
4. RLS enabled on scrape_jobs with strict service_role policies.
5. Preservation of public.leads, public.scrape_history, and public.users tables.
6. Helper functions in scripts/supabase_client.py:
   - create_scrape_job_record
   - update_scrape_job_record
   - fetch_recent_scrape_jobs_from_supabase
"""
import io
import json
import os
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import scripts.supabase_client as sc


class TestAdditiveMigration003SQL(unittest.TestCase):
    """Verify that migration 003 file is well-formed, additive, and secure."""

    def setUp(self):
        self.migration_path = os.path.join(
            os.path.dirname(__file__), "..", "migrations", "003_telegram_scrape_jobs_and_leads_additive.sql"
        )
        self.assertTrue(os.path.isfile(self.migration_path), "Migration 003 file must exist")
        with open(self.migration_path, "r", encoding="utf-8") as f:
            self.sql_content = f.read()

    def test_additive_leads_columns_present(self):
        sql = self.sql_content.upper()
        self.assertIn("ALTER TABLE PUBLIC.LEADS", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS SOURCE TEXT DEFAULT 'WEB'", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS PLACE_ID TEXT", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS CID TEXT", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS ANALYSIS JSONB DEFAULT '{}'::JSONB", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS TELEGRAM_MESSAGE_ID TEXT", sql)

    def test_additive_indexes_on_leads(self):
        sql = self.sql_content.upper()
        self.assertIn("CREATE INDEX IF NOT EXISTS IDX_LEADS_PLACE_ID ON PUBLIC.LEADS (PLACE_ID)", sql)
        self.assertIn("CREATE INDEX IF NOT EXISTS IDX_LEADS_SOURCE ON PUBLIC.LEADS (SOURCE)", sql)

    def test_scrape_jobs_table_structure(self):
        sql = self.sql_content.upper()
        self.assertIn("CREATE TABLE IF NOT EXISTS PUBLIC.SCRAPE_JOBS", sql)
        self.assertIn("ID UUID PRIMARY KEY DEFAULT GEN_RANDOM_UUID()", sql)
        self.assertIn("SCRAPER_JOB_ID TEXT", sql)
        self.assertIn("TELEGRAM_USER_ID TEXT", sql)
        self.assertIn("TELEGRAM_CHAT_ID TEXT", sql)
        self.assertIn("TELEGRAM_UPDATE_ID TEXT", sql)
        self.assertIn("QUERY TEXT NOT NULL", sql)
        self.assertIn("STATUS TEXT NOT NULL DEFAULT 'PENDING'", sql)
        self.assertIn("CHECK (PROGRESS >= 0 AND PROGRESS <= 100)", sql)
        self.assertIn("TOTAL_LEADS INTEGER DEFAULT 0", sql)

    def test_idempotency_index_on_telegram_update(self):
        sql = self.sql_content.upper()
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS IDX_SCRAPE_JOBS_TELEGRAM_UPDATE", sql)
        self.assertIn("ON PUBLIC.SCRAPE_JOBS (TELEGRAM_UPDATE_ID)", sql)

    def test_rls_and_least_privilege_on_scrape_jobs(self):
        sql = self.sql_content.upper()
        self.assertIn("ALTER TABLE PUBLIC.SCRAPE_JOBS ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("REVOKE ALL ON TABLE PUBLIC.SCRAPE_JOBS FROM ANON, AUTHENTICATED", sql)
        self.assertIn("GRANT ALL ON TABLE PUBLIC.SCRAPE_JOBS TO SERVICE_ROLE", sql)
        self.assertIn("CREATE POLICY \"SERVICE ROLE FULL ACCESS ON SCRAPE_JOBS\"", sql)
        self.assertIn("TO SERVICE_ROLE", sql)

    def test_no_destructive_statements(self):
        sql = self.sql_content.upper()
        self.assertNotIn("DROP TABLE PUBLIC.LEADS", sql)
        self.assertNotIn("DROP TABLE PUBLIC.SCRAPE_HISTORY", sql)
        self.assertNotIn("DROP TABLE PUBLIC.USERS", sql)
        self.assertNotIn("DROP COLUMN", sql)


class TestSupabaseClientJobHelpers(unittest.TestCase):
    """Verify PostgREST helper functions for scrape_jobs in supabase_client.py."""

    def setUp(self):
        self.env_patcher = patch.dict(os.environ, {
            "SUPABASE_URL": "https://mockproject.supabase.co",
            "SUPABASE_KEY": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.mockservicekey",
            "SUPABASE_TABLE": "leads"
        })
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    @patch("urllib.request.urlopen")
    def test_create_scrape_job_record_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.read.return_value = json.dumps([{
            "id": "11111111-2222-3333-4444-555555555555",
            "scraper_job_id": "job_123",
            "query": "dentists in Austin, TX",
            "status": "pending"
        }]).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = sc.create_scrape_job_record({
            "scraper_job_id": "job_123",
            "query": "dentists in Austin, TX",
            "telegram_user_id": "999888777",
            "telegram_chat_id": "12345"
        })
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("data", {}).get("scraper_job_id"), "job_123")

    def test_create_scrape_job_record_missing_credentials(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_KEY": ""}):
            res = sc.create_scrape_job_record({"query": "test"})
            self.assertFalse(res.get("success"))
            self.assertIn("not configured", res.get("error"))

    @patch("urllib.request.urlopen")
    def test_update_scrape_job_record_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = sc.update_scrape_job_record("job_123", {
            "status": "ok",
            "progress": 100,
            "stage": "Completed",
            "total_leads": 25
        })
        self.assertTrue(res.get("success"))

    def test_update_scrape_job_record_missing_job_id(self):
        res = sc.update_scrape_job_record("", {"status": "ok"})
        self.assertFalse(res.get("success"))
        self.assertIn("Missing job_id", res.get("error"))

    @patch("urllib.request.urlopen")
    def test_fetch_recent_scrape_jobs_from_supabase(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps([
            {"id": "j1", "query": "Dentists", "status": "ok"},
            {"id": "j2", "query": "Plumbers", "status": "working"}
        ]).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        jobs = sc.fetch_recent_scrape_jobs_from_supabase(limit=2)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["id"], "j1")

    def test_fetch_recent_scrape_jobs_missing_config_returns_empty(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_KEY": ""}):
            jobs = sc.fetch_recent_scrape_jobs_from_supabase()
            self.assertEqual(jobs, [])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Migration Regression Test Suite for Migration 003 (Additive Supabase Migration).

Verifies:
1. File structure, syntax, and rerunnability of migrations/003_telegram_scrape_jobs_and_leads_additive.sql.
2. public.scrape_jobs table columns, types (TEXT for telegram IDs), and default values.
3. Check constraints on status ('pending', 'running', 'ok', 'failed', 'cancelled') and progress (0-100).
4. Idempotency unique index on telegram_update_id and performance indexes.
5. Least-privilege Row Level Security (RLS) enforcement and service_role grants.
6. Additive enhancements to public.leads (source, place_id, cid, analysis).
7. Preservation of existing tables (no destructive DROP TABLE on leads, scrape_history, users).
"""
import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MIGRATION_003_PATH = os.path.join(REPO_ROOT, "migrations", "003_telegram_scrape_jobs_and_leads_additive.sql")
SCHEMA_SQL_PATH = os.path.join(REPO_ROOT, "supabase_schema.sql")


class TestMigration003(unittest.TestCase):
    """Test validation for additive migration 003."""

    @classmethod
    def setUpClass(cls):
        cls.assertTrue(os.path.isfile(MIGRATION_003_PATH), f"Migration file not found at {MIGRATION_003_PATH}")
        with open(MIGRATION_003_PATH, "r", encoding="utf-8") as f:
            cls.sql_content = f.read()

        cls.assertTrue(os.path.isfile(SCHEMA_SQL_PATH), f"Original schema file not found at {SCHEMA_SQL_PATH}")
        with open(SCHEMA_SQL_PATH, "r", encoding="utf-8") as f:
            cls.schema_content = f.read()

    def test_migration_file_exists_and_is_non_empty(self):
        self.assertGreater(len(self.sql_content.strip()), 100)

    def test_original_schema_sql_is_preserved_and_not_deleted(self):
        # Verify supabase_schema.sql still exists with original core definitions
        self.assertIn("CREATE TABLE IF NOT EXISTS public.leads", self.schema_content)
        self.assertIn("CREATE TABLE IF NOT EXISTS public.scrape_history", self.schema_content)
        self.assertIn("CREATE TABLE IF NOT EXISTS public.users", self.schema_content)

    def test_no_destructive_table_drops(self):
        # Ensure migration does NOT drop leads, scrape_history, or users tables
        sql_lower = self.sql_content.lower()
        self.assertNotIn("drop table public.leads", sql_lower)
        self.assertNotIn("drop table if exists public.leads", sql_lower)
        self.assertNotIn("drop table public.scrape_history", sql_lower)
        self.assertNotIn("drop table if exists public.scrape_history", sql_lower)
        self.assertNotIn("drop table public.users", sql_lower)
        self.assertNotIn("drop table if exists public.users", sql_lower)
        self.assertNotIn("drop table public.scrape_jobs", sql_lower)

    def test_scrape_jobs_table_columns_and_types(self):
        # Must create public.scrape_jobs with IF NOT EXISTS
        self.assertRegex(
            self.sql_content,
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+public\.scrape_jobs",
            "Missing CREATE TABLE IF NOT EXISTS public.scrape_jobs"
        )

        required_columns = [
            ("id", r"id\s+UUID\s+PRIMARY\s+KEY"),
            ("created_at", r"created_at\s+TIMESTAMPTZ"),
            ("updated_at", r"updated_at\s+TIMESTAMPTZ"),
            ("started_at", r"started_at\s+TIMESTAMPTZ"),
            ("completed_at", r"completed_at\s+TIMESTAMPTZ"),
            ("scraper_job_id", r"scraper_job_id\s+TEXT"),
            ("telegram_user_id", r"telegram_user_id\s+TEXT"),
            ("telegram_chat_id", r"telegram_chat_id\s+TEXT"),
            ("telegram_update_id", r"telegram_update_id\s+TEXT"),
            ("query", r"query\s+TEXT\s+NOT\s+NULL"),
            ("location", r"location\s+TEXT"),
            ("requested_limit", r"requested_limit\s+INTEGER"),
            ("status", r"status\s+TEXT"),
            ("stage", r"stage\s+TEXT"),
            ("progress", r"progress\s+INTEGER"),
            ("lead_counts", r"lead_counts\s+JSONB"),
            ("total_leads", r"total_leads\s+INTEGER"),
            ("error_summary", r"error_summary\s+TEXT"),
            ("source", r"source\s+TEXT"),
            ("metadata", r"metadata\s+JSONB")
        ]

        for col_name, pattern in required_columns:
            self.assertRegex(
                self.sql_content,
                pattern,
                f"Missing or mismatched definition for column '{col_name}' in public.scrape_jobs"
            )

    def test_telegram_ids_are_stored_as_text(self):
        # Telegram user ID and chat ID must be stored as TEXT to prevent 64-bit precision loss
        self.assertRegex(self.sql_content, r"telegram_user_id\s+TEXT", "telegram_user_id must be TEXT")
        self.assertRegex(self.sql_content, r"telegram_chat_id\s+TEXT", "telegram_chat_id must be TEXT")
        self.assertRegex(self.sql_content, r"telegram_update_id\s+TEXT", "telegram_update_id must be TEXT")

    def test_status_and_progress_check_constraints(self):
        # Status constraint
        self.assertRegex(
            self.sql_content,
            r"CHECK\s*\(\s*status\s+IN\s*\(\s*'pending'\s*,\s*'running'\s*,\s*'ok'\s*,\s*'failed'\s*,\s*'cancelled'\s*\)\s*\)",
            "Missing or incomplete status CHECK constraint"
        )
        # Progress constraint (0 <= progress <= 100)
        self.assertRegex(
            self.sql_content,
            r"CHECK\s*\(\s*progress\s*>=\s*0\s+AND\s+progress\s*<=\s*100\s*\)",
            "Missing progress 0-100 CHECK constraint"
        )

    def test_idempotency_unique_index_on_telegram_update(self):
        # Unique index on telegram_update_id
        self.assertRegex(
            self.sql_content,
            r"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+idx_scrape_jobs_telegram_update\s+ON\s+public\.scrape_jobs\s*\(telegram_update_id\)",
            "Missing unique idempotency index on telegram_update_id"
        )

    def test_performance_indexes_on_scrape_jobs(self):
        indexes = [
            "idx_scrape_jobs_scraper_id",
            "idx_scrape_jobs_telegram_user",
            "idx_scrape_jobs_telegram_chat",
            "idx_scrape_jobs_status_created",
            "idx_scrape_jobs_created_at"
        ]
        for idx in indexes:
            self.assertIn(idx, self.sql_content, f"Missing index {idx} in migration 003")

    def test_updated_at_trigger_for_scrape_jobs(self):
        self.assertIn("update_scrape_jobs_updated_at()", self.sql_content)
        self.assertIn("trigger_scrape_jobs_updated_at", self.sql_content)

    def test_row_level_security_enabled_on_scrape_jobs(self):
        self.assertRegex(
            self.sql_content,
            r"ALTER\s+TABLE\s+public\.scrape_jobs\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
            "RLS must be enabled on public.scrape_jobs"
        )

    def test_least_privilege_grants_and_policies(self):
        # Permissions revoked from anon and authenticated
        self.assertRegex(
            self.sql_content,
            r"REVOKE\s+ALL\s+ON\s+TABLE\s+public\.scrape_jobs\s+FROM\s+anon,\s*authenticated",
            "Permissions must be revoked from anon and authenticated on scrape_jobs"
        )
        # Permissions granted strictly to service_role
        self.assertRegex(
            self.sql_content,
            r"GRANT\s+ALL\s+ON\s+TABLE\s+public\.scrape_jobs\s+TO\s+service_role",
            "Full table permissions must be granted to service_role on scrape_jobs"
        )
        # Service role full access policy
        self.assertRegex(
            self.sql_content,
            r"CREATE\s+POLICY\s+\"Service role full access on scrape_jobs\"\s+ON\s+public\.scrape_jobs",
            "Missing service_role policy on public.scrape_jobs"
        )

        # No broad policies granted to anon or authenticated
        sql_lower = self.sql_content.lower()
        self.assertNotIn("to anon", sql_lower)
        self.assertNotIn("to authenticated", sql_lower)

    def test_additive_enhancements_to_leads_table(self):
        # ALTER TABLE IF EXISTS public.leads ADD COLUMN IF NOT EXISTS
        self.assertRegex(
            self.sql_content,
            r"ALTER\s+TABLE\s+IF\s+EXISTS\s+public\.leads",
            "Missing ALTER TABLE statement on public.leads"
        )

        added_columns = ["source", "place_id", "cid", "analysis", "telegram_message_id"]
        for col in added_columns:
            self.assertRegex(
                self.sql_content,
                rf"ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+{col}",
                f"Missing ADD COLUMN IF NOT EXISTS {col} on public.leads"
            )

        # Indexes for added columns
        self.assertIn("idx_leads_place_id", self.sql_content)
        self.assertIn("idx_leads_source", self.sql_content)


if __name__ == "__main__":
    unittest.main()

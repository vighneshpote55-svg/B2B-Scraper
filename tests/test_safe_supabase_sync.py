#!/usr/bin/env python3
"""Comprehensive Mocked Test Suite for LeadMap Pro — Phase 4: Safe Supabase Sync.

Covers:
1. Empty job sync: no network requests, no deletions, returns 0 confirmed count.
2. In-memory deduplication: duplicate business listings within job deduplicated before POST.
3. Idempotent re-sync: no premature pre-deletion; uses POST with on_conflict=id.
4. Failure on first batch: zero writes confirmed, failed_batch_index=0, halts subsequent batches.
5. Failure on middle batch: partial confirmed count matches batch 1, batch 3 halted, failed_batch_index=1.
6. Failure on final batch: batches 1 & 2 confirmed, batch 3 reported failed, failed_batch_index=2.
7. Transient retry success: recovers on retry after HTTP 503/500, full count confirmed.
8. Non-retryable error abort: HTTP 400 aborts immediately without redundant retries.
9. Local archive preservation: local data/leads_*.json file remains 100% intact if cloud sync fails.
"""
import io
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.supabase_client import sync_leads, format_lead_for_supabase


class MockHTTPResponse:
    """Helper to mock urllib.request.urlopen response context manager."""

    def __init__(self, status=201, json_data=None):
        self.status = status
        self.data_bytes = json.dumps(json_data or []).encode("utf-8")

    def read(self):
        return self.data_bytes

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class TestSafeSupabaseSync(unittest.TestCase):
    """Unit and mock integration tests for Phase 4 Safe Supabase Sync."""

    def setUp(self):
        self.fake_url = "https://safe-sync-test.supabase.co"
        self.fake_key = "sb_secret_test_mock_service_role_key"
        self.table = "leads"

    def _sample_leads(self, count=10, prefix="biz"):
        leads = []
        for i in range(count):
            leads.append({
                "title": f"{prefix}_{i} Inc",
                "clean_phone": f"+1555000{i:04d}",
                "phone": f"+1 555-000-{i:04d}",
                "domain": f"{prefix}_{i}.com",
                "website": f"https://{prefix}_{i}.com",
                "address": f"{i} Market St, City, ST",
                "lead_score": 85,
                "lead_tier": "HOT"
            })
        return leads

    @patch("urllib.request.urlopen")
    def test_empty_job_sync_no_network_no_deletions(self, mock_urlopen):
        """Verify empty job makes ZERO network calls and issues no deletes."""
        res = sync_leads([], job_id="job_empty", supabase_url=self.fake_url, supabase_key=self.fake_key)
        self.assertTrue(res["success"])
        self.assertEqual(res["confirmed_count"], 0)
        self.assertEqual(res["count"], 0)
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_in_memory_duplicate_deduplication(self, mock_urlopen):
        """Verify duplicates within the job array are deduplicated by id before POST."""
        leads = [
            {"title": "Acme Plumbing", "clean_phone": "+15551234567", "domain": "acme.com"},
            {"title": "Acme Plumbing Duplicate", "clean_phone": "+15551234567", "domain": "acme.com"},  # duplicate
            {"title": "Beta Electric", "clean_phone": "+15559876543", "domain": "beta.com"},
        ]

        def side_effect(req, timeout=30):
            payload = json.loads(req.data.decode("utf-8"))
            return MockHTTPResponse(201, payload)

        mock_urlopen.side_effect = side_effect

        res = sync_leads(leads, job_id="job_dups", supabase_url=self.fake_url, supabase_key=self.fake_key)
        self.assertTrue(res["success"])
        # Exactly 2 unique leads should be sent, not 3
        self.assertEqual(res["total_records"], 2)
        self.assertEqual(res["confirmed_count"], 2)

        # Inspect the request made
        req = mock_urlopen.call_args[0][0]
        self.assertIn("?on_conflict=id", req.full_url)
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.headers.get("Prefer"), "resolution=merge-duplicates,return=representation")

    @patch("urllib.request.urlopen")
    def test_idempotent_resync_no_predelete(self, mock_urlopen):
        """Verify re-syncing a job never issues a premature DELETE request."""
        leads = self._sample_leads(count=5)

        def side_effect(req, timeout=30):
            payload = json.loads(req.data.decode("utf-8"))
            return MockHTTPResponse(201, payload)

        mock_urlopen.side_effect = side_effect

        res = sync_leads(leads, job_id="job_resync_safe", supabase_url=self.fake_url, supabase_key=self.fake_key)
        self.assertTrue(res["success"])
        self.assertEqual(res["confirmed_count"], 5)

        # Verify no call used DELETE
        for call in mock_urlopen.call_args_list:
            req = call[0][0]
            self.assertNotEqual(req.get_method(), "DELETE", "Unsafe pre-delete request detected!")
            self.assertEqual(req.get_method(), "POST")
            self.assertIn("?on_conflict=id", req.full_url)

    @patch("urllib.request.urlopen")
    def test_failure_on_first_batch(self, mock_urlopen):
        """Verify failure on batch 1 halts immediately with 0 confirmed writes."""
        leads = self._sample_leads(count=120)  # 3 batches of 50 (50, 50, 20)

        # First batch fails with HTTP 500
        mock_urlopen.side_effect = urllib.error.HTTPError(
            self.fake_url, 500, "Internal Server Error", {}, io.BytesIO(b'{"message":"Database down"}')
        )

        res = sync_leads(leads, job_id="job_fail_b1", supabase_url=self.fake_url, supabase_key=self.fake_key,
                         batch_size=50, max_retries=1)

        self.assertFalse(res["success"])
        self.assertEqual(res["confirmed_count"], 0)
        self.assertEqual(res["synced_before_error"], 0)
        self.assertEqual(res["failed_batch_index"], 0)
        self.assertEqual(res["total_batches"], 3)
        self.assertEqual(res["total_records"], 120)
        # Should only have attempted batch 1 once
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("urllib.request.urlopen")
    def test_failure_on_middle_batch(self, mock_urlopen):
        """Verify failure on batch 2 halts batch 3, and accurately confirms batch 1 writes."""
        leads = self._sample_leads(count=120)  # 3 batches (50, 50, 20)
        call_count = [0]

        def side_effect(req, timeout=30):
            call_count[0] += 1
            if call_count[0] == 1:
                # Batch 1 succeeds
                payload = json.loads(req.data.decode("utf-8"))
                return MockHTTPResponse(201, payload)
            # Batch 2 fails
            raise urllib.error.HTTPError(
                self.fake_url, 500, "Internal Server Error", {}, io.BytesIO(b'{"message":"Disk full"}')
            )

        mock_urlopen.side_effect = side_effect

        res = sync_leads(leads, job_id="job_fail_b2", supabase_url=self.fake_url, supabase_key=self.fake_key,
                         batch_size=50, max_retries=1)

        self.assertFalse(res["success"])
        self.assertEqual(res["confirmed_count"], 50)
        self.assertEqual(res["synced_before_error"], 50)
        self.assertEqual(res["failed_batch_index"], 1)
        self.assertEqual(res["total_batches"], 3)
        # Batch 3 was never attempted
        self.assertEqual(call_count[0], 2)

    @patch("urllib.request.urlopen")
    def test_failure_on_final_batch(self, mock_urlopen):
        """Verify failure on batch 3 accurately reports batch 1 & 2 confirmed writes."""
        leads = self._sample_leads(count=120)  # 3 batches (50, 50, 20)
        call_count = [0]

        def side_effect(req, timeout=30):
            call_count[0] += 1
            if call_count[0] in (1, 2):
                # Batches 1 & 2 succeed
                payload = json.loads(req.data.decode("utf-8"))
                return MockHTTPResponse(201, payload)
            # Batch 3 fails
            raise urllib.error.HTTPError(
                self.fake_url, 502, "Bad Gateway", {}, io.BytesIO(b'{"message":"Upstream timeout"}')
            )

        mock_urlopen.side_effect = side_effect

        res = sync_leads(leads, job_id="job_fail_b3", supabase_url=self.fake_url, supabase_key=self.fake_key,
                         batch_size=50, max_retries=1)

        self.assertFalse(res["success"])
        self.assertEqual(res["confirmed_count"], 100)
        self.assertEqual(res["synced_before_error"], 100)
        self.assertEqual(res["failed_batch_index"], 2)
        self.assertEqual(res["total_batches"], 3)
        self.assertTrue(res["retryable"])

    @patch("urllib.request.urlopen")
    def test_transient_retry_success(self, mock_urlopen):
        """Verify transient 503 error is automatically retried and succeeds."""
        leads = self._sample_leads(count=25)
        call_count = [0]

        def side_effect(req, timeout=30):
            call_count[0] += 1
            if call_count[0] == 1:
                # First attempt fails with transient 503
                raise urllib.error.HTTPError(
                    self.fake_url, 503, "Service Unavailable", {}, io.BytesIO(b'{"message":"Server overloaded"}')
                )
            # Second attempt succeeds
            payload = json.loads(req.data.decode("utf-8"))
            return MockHTTPResponse(201, payload)

        mock_urlopen.side_effect = side_effect

        res = sync_leads(leads, job_id="job_retry", supabase_url=self.fake_url, supabase_key=self.fake_key,
                         batch_size=50, max_retries=3, retry_delay=0.01)

        self.assertTrue(res["success"])
        self.assertEqual(res["confirmed_count"], 25)
        self.assertEqual(call_count[0], 2)

    @patch("urllib.request.urlopen")
    def test_non_retryable_error_aborts_immediately(self, mock_urlopen):
        """Verify 400 Bad Request aborts immediately without wasting retries."""
        leads = self._sample_leads(count=10)
        mock_urlopen.side_effect = urllib.error.HTTPError(
            self.fake_url, 400, "Bad Request", {}, io.BytesIO(b'{"message":"Invalid column"}')
        )

        res = sync_leads(leads, job_id="job_non_retry", supabase_url=self.fake_url, supabase_key=self.fake_key,
                         batch_size=50, max_retries=3, retry_delay=0.01)

        self.assertFalse(res["success"])
        self.assertFalse(res["retryable"])
        self.assertEqual(res["confirmed_count"], 0)
        # Exactly 1 attempt made
        self.assertEqual(mock_urlopen.call_count, 1)

    def test_local_archive_preserved_on_sync_failure(self):
        """Verify local JSON archive file remains 100% intact if cloud sync fails."""
        data_dir = os.path.join(REPO_ROOT, "data")
        os.makedirs(data_dir, exist_ok=True)
        test_job_id = f"safe_local_test_{int(time.time() if 'time' in globals() else 12345)}"
        archive_path = os.path.join(data_dir, f"leads_{test_job_id}.json")

        initial_data = {
            "job_id": test_job_id,
            "leads": self._sample_leads(count=5, prefix="preserve"),
            "params": {"keyword": "Dentists", "city": "Austin"}
        }

        # Write local archive first (as scraper does)
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(initial_data, f)

        try:
            # Simulate failed cloud sync
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.side_effect = urllib.error.HTTPError(
                    self.fake_url, 500, "Server Error", {}, io.BytesIO(b'{"message":"Fail"}')
                )
                res = sync_leads(initial_data["leads"], job_id=test_job_id, supabase_url=self.fake_url,
                                 supabase_key=self.fake_key, max_retries=1)
                self.assertFalse(res["success"])

            # Verify local file is completely intact
            self.assertTrue(os.path.isfile(archive_path), "Local archive was deleted!")
            with open(archive_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(len(loaded["leads"]), 5)
            self.assertEqual(loaded["leads"][0]["title"], "preserve_0 Inc")
        finally:
            if os.path.exists(archive_path):
                os.remove(archive_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)

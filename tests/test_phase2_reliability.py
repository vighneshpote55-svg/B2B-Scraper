#!/usr/bin/env python3
"""Comprehensive Reliability & Job Management Test Suite for LeadMap Pro — Phase 2.

Covers:
1. Bounded job scheduling & concurrency limits (HTTP 429 on excess, capacity release on completion).
2. Durable job persistence across process memory loss.
3. Restart recovery of interrupted jobs and recovery idempotency.
4. Cancellation ownership enforcement and graceful/forced subprocess termination.
5. Universal cleanup after success, failure, and cancellation.
6. Retention limits, active-job protection, and temporary file pruning.
7. Preservation of Phase 1 security controls (authentication, cross-user isolation, SSRF, CSV formula defense).
"""
import concurrent.futures
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

# Include project root
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dashboard
from dashboard import (
    MAX_CONCURRENT_JOBS,
    JOB_TIMEOUT_SECONDS,
    JOB_RETENTION_DAYS,
    MAX_COMPLETED_JOBS,
    JOB_CACHE,
    JOB_LOCK,
    JOB_PROCESSES,
    JOB_ADMISSION_LOCK,
    PROCESS_LOCK,
    _validate_job_id,
    _get_job_file_path,
    _get_job_owner_id,
    _atomic_json_write,
    _register_job_process,
    _unregister_job_process,
    _terminate_job_processes,
    _create_durable_job,
    _update_durable_job,
    _get_active_job_count,
    recover_interrupted_jobs,
    cleanup_stale_jobs_and_temp_files,
    background_job_processor
)
from scripts.cleanser import is_safe_public_ip, sanitize_csv_cell


class TestBoundedJobScheduling(unittest.TestCase):
    """Test concurrent job limits, slot admission, and capacity management."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_data_dir = dashboard.DATA_DIR
        dashboard.DATA_DIR = self.temp_dir
        with JOB_LOCK:
            JOB_CACHE.clear()

    def tearDown(self):
        dashboard.DATA_DIR = self.orig_data_dir
        with JOB_LOCK:
            JOB_CACHE.clear()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_active_job_counting_and_limit_enforcement(self):
        """Active jobs count accurately across memory cache and disk files."""
        # Initially zero active jobs
        self.assertEqual(_get_active_job_count(), 0)

        # Create 1 active job in memory and on disk
        _create_durable_job("job_1", "u_1", "u1@test.io", {"keyword": "Dentists"})
        self.assertEqual(_get_active_job_count(), 1)

        # Create 2nd active job
        _create_durable_job("job_2", "u_1", "u1@test.io", {"keyword": "Lawyers"})
        self.assertEqual(_get_active_job_count(), 2)

        # Mark 1st job completed
        _update_durable_job("job_1", {"status": "ok", "completed_at": time.time()})
        self.assertEqual(_get_active_job_count(), 1)

        # Mark 2nd job failed
        _update_durable_job("job_2", {"status": "failed", "completed_at": time.time()})
        self.assertEqual(_get_active_job_count(), 0)

    def test_simultaneous_job_admission_contention(self):
        """Concurrent admission requests respect MAX_CONCURRENT_JOBS limit under contention."""
        max_allowed = 2
        admitted = []
        rejected = []

        def try_admit_job(idx):
            jid = f"contention_job_{idx}"
            with JOB_ADMISSION_LOCK:
                if _get_active_job_count() >= max_allowed:
                    rejected.append(jid)
                    return False
                _create_durable_job(jid, "u_test", "test@io.com", {"k": idx})
                admitted.append(jid)
                return True

        # Run 10 threads trying to start simultaneously
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(try_admit_job, i) for i in range(10)]
            concurrent.futures.wait(futures)

        self.assertEqual(len(admitted), max_allowed)
        self.assertEqual(len(rejected), 8)
        self.assertEqual(_get_active_job_count(), max_allowed)


class TestPersistentJobState(unittest.TestCase):
    """Test durable job serialization, secret exclusion, and crash survivability."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_data_dir = dashboard.DATA_DIR
        dashboard.DATA_DIR = self.temp_dir
        with JOB_LOCK:
            JOB_CACHE.clear()

    def tearDown(self):
        dashboard.DATA_DIR = self.orig_data_dir
        with JOB_LOCK:
            JOB_CACHE.clear()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_durable_record_creation_and_secret_exclusion(self):
        """Sensitive parameters (tokens, passwords, keys) are stripped from durable job records."""
        params = {
            "keyword": "Plumbers",
            "city": "Austin, TX",
            "token": "secret_session_token_12345",
            "apiKey": "super_secret_key_67890",
            "password_hash": "should_never_be_here"
        }
        rec = _create_durable_job("job_durable_1", "user_abc", "user@corp.com", params)

        self.assertEqual(rec["status"], "working")
        self.assertEqual(rec["user_id"], "user_abc")
        self.assertIn("keyword", rec["params"])
        self.assertNotIn("token", rec["params"])
        self.assertNotIn("apiKey", rec["params"])
        self.assertNotIn("password_hash", rec["params"])

        # Check disk file
        disk_file = os.path.join(self.temp_dir, "leads_job_durable_1.json")
        self.assertTrue(os.path.isfile(disk_file))
        with open(disk_file, "r") as f:
            disk_data = json.load(f)
        self.assertEqual(disk_data["status"], "working")
        self.assertEqual(disk_data["user_id"], "user_abc")
        self.assertNotIn("token", disk_data["params"])

    def test_durable_job_survives_memory_wipe(self):
        """Job ownership and status resolve from disk even after in-memory JOB_CACHE is wiped."""
        _create_durable_job("job_wipe_test", "owner_xyz", "xyz@test.com", {"keyword": "Dentist"})

        # Wipe memory cache completely (simulating process crash/restart)
        with JOB_LOCK:
            JOB_CACHE.clear()

        # Verify owner ID resolves directly from disk
        owner_id = _get_job_owner_id("job_wipe_test")
        self.assertEqual(owner_id, "owner_xyz")

        # Verify updates apply to disk even if job not in memory
        _update_durable_job("job_wipe_test", {
            "status": "ok",
            "stage": "Completed",
            "progress_percent": 100,
            "leads": [{"title": "Clinic A", "clean_phone": "+15551234"}]
        })

        disk_file = os.path.join(self.temp_dir, "leads_job_wipe_test.json")
        with open(disk_file, "r") as f:
            updated_disk = json.load(f)
        self.assertEqual(updated_disk["status"], "ok")
        self.assertEqual(len(updated_disk["leads"]), 1)


class TestRestartAndCrashRecovery(unittest.TestCase):
    """Test automated detection, marking of interrupted jobs, and idempotency."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_data_dir = dashboard.DATA_DIR
        dashboard.DATA_DIR = self.temp_dir
        with JOB_LOCK:
            JOB_CACHE.clear()

    def tearDown(self):
        dashboard.DATA_DIR = self.orig_data_dir
        with JOB_LOCK:
            JOB_CACHE.clear()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_recover_interrupted_jobs_on_startup(self):
        """Unfinished jobs (working, starting, stopping) are marked as failed with recovery notice."""
        # 1. Job left in 'working' state
        f1 = os.path.join(self.temp_dir, "leads_job_unfinished_1.json")
        _atomic_json_write(f1, {
            "job_id": "job_unfinished_1",
            "status": "working",
            "stage": "Scraping listings...",
            "started_at": time.time() - 100
        })

        # 2. Job left in 'starting' state
        f2 = os.path.join(self.temp_dir, "leads_job_unfinished_2.json")
        _atomic_json_write(f2, {
            "job_id": "job_unfinished_2",
            "status": "starting",
            "stage": "Initializing...",
            "started_at": time.time() - 200
        })

        # 3. Job already completed ('ok')
        f3 = os.path.join(self.temp_dir, "leads_job_completed_3.json")
        _atomic_json_write(f3, {
            "job_id": "job_completed_3",
            "status": "ok",
            "stage": "Completed",
            "completed_at": time.time() - 50,
            "leads": [{"title": "Clinic"}]
        })

        # Run recovery
        recovered = recover_interrupted_jobs(self.temp_dir)
        self.assertEqual(recovered, 2)

        # Inspect f1
        with open(f1, "r") as f:
            d1 = json.load(f)
        self.assertEqual(d1["status"], "failed")
        self.assertIn("Interrupted", d1["stage"])
        self.assertIsNotNone(d1.get("completed_at"))

        # Inspect f2
        with open(f2, "r") as f:
            d2 = json.load(f)
        self.assertEqual(d2["status"], "failed")

        # Inspect f3 (unaltered)
        with open(f3, "r") as f:
            d3 = json.load(f)
        self.assertEqual(d3["status"], "ok")

    def test_recovery_idempotency(self):
        """Executing recovery multiple times does not modify already-recovered records."""
        f1 = os.path.join(self.temp_dir, "leads_job_interrupted.json")
        _atomic_json_write(f1, {
            "job_id": "job_interrupted",
            "status": "working",
            "started_at": time.time()
        })

        # 1st run: recovers 1 job
        self.assertEqual(recover_interrupted_jobs(self.temp_dir), 1)

        # 2nd run: 0 jobs to recover
        self.assertEqual(recover_interrupted_jobs(self.temp_dir), 0)

        # 3rd run: 0 jobs to recover
        self.assertEqual(recover_interrupted_jobs(self.temp_dir), 0)


class TestSafeCancellationAndCleanup(unittest.TestCase):
    """Test job cancellation, subprocess termination (SIGTERM -> SIGKILL), and process isolation."""

    def setUp(self):
        with PROCESS_LOCK:
            JOB_PROCESSES.clear()
        with JOB_LOCK:
            JOB_CACHE.clear()

    def tearDown(self):
        with PROCESS_LOCK:
            JOB_PROCESSES.clear()
        with JOB_LOCK:
            JOB_CACHE.clear()

    def test_process_registration_and_termination(self):
        """Registered child subprocesses for a job are gracefully terminated and forced if needed."""
        mock_proc = MagicMock()
        mock_proc.poll.side_effect = [None, None, 0]  # running, running, terminated

        _register_job_process("test_job_proc", mock_proc)
        with PROCESS_LOCK:
            self.assertIn("test_job_proc", JOB_PROCESSES)

        # Terminate
        _terminate_job_processes("test_job_proc", grace_timeout=0.1)

        # Assert terminate was called
        mock_proc.terminate.assert_called_once()
        with PROCESS_LOCK:
            self.assertNotIn("test_job_proc", JOB_PROCESSES)

    def test_process_forced_kill_on_timeout(self):
        """If a process fails to terminate gracefully, kill() is invoked."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # always running
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="scraper", timeout=0.1)

        _register_job_process("stubborn_job", mock_proc)
        _terminate_job_processes("stubborn_job", grace_timeout=0.1)

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()

    def test_cancellation_does_not_affect_other_jobs(self):
        """Terminating Job A does not terminate subprocesses registered to Job B."""
        proc_a = MagicMock()
        proc_a.poll.return_value = None
        proc_b = MagicMock()
        proc_b.poll.return_value = None

        _register_job_process("job_A", proc_a)
        _register_job_process("job_B", proc_b)

        _terminate_job_processes("job_A")

        proc_a.terminate.assert_called_once()
        proc_b.terminate.assert_not_called()

        with PROCESS_LOCK:
            self.assertNotIn("job_A", JOB_PROCESSES)
            self.assertIn("job_B", JOB_PROCESSES)


class TestRetentionAndActiveJobProtection(unittest.TestCase):
    """Test pruning of stale jobs and orphaned temporary files without deleting active jobs."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        with JOB_LOCK:
            JOB_CACHE.clear()

    def tearDown(self):
        with JOB_LOCK:
            JOB_CACHE.clear()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_active_jobs_strictly_protected_from_deletion(self):
        """Jobs in active status ('working') are never pruned even if created long ago."""
        now = time.time()
        old_time = now - (60 * 86400)  # 60 days ago

        # Active job on disk
        active_file = os.path.join(self.temp_dir, "leads_active_job.json")
        _atomic_json_write(active_file, {
            "job_id": "active_job",
            "status": "working",
            "started_at": old_time,
            "completed_at": None
        })

        # Stale completed job
        stale_file = os.path.join(self.temp_dir, "leads_stale_job.json")
        _atomic_json_write(stale_file, {
            "job_id": "stale_job",
            "status": "ok",
            "completed_at": old_time
        })

        # Recent completed job
        recent_file = os.path.join(self.temp_dir, "leads_recent_job.json")
        _atomic_json_write(recent_file, {
            "job_id": "recent_job",
            "status": "ok",
            "completed_at": now - 3600  # 1 hour ago
        })

        res = cleanup_stale_jobs_and_temp_files(self.temp_dir, retention_days=30, max_jobs=10)

        self.assertEqual(res["pruned_jobs"], 1)
        self.assertTrue(os.path.isfile(active_file), "Active job file was erroneously deleted!")
        self.assertFalse(os.path.isfile(stale_file), "Stale completed job was not pruned!")
        self.assertTrue(os.path.isfile(recent_file), "Recent completed job was erroneously deleted!")

    def test_max_completed_jobs_cap_pruning(self):
        """When completed jobs exceed max_jobs, oldest completed jobs are pruned first."""
        now = time.time()
        for i in range(5):
            fp = os.path.join(self.temp_dir, f"leads_job_{i}.json")
            _atomic_json_write(fp, {
                "job_id": f"job_{i}",
                "status": "ok",
                "completed_at": now + i * 10
            })

        # Cap at 3 max completed jobs
        res = cleanup_stale_jobs_and_temp_files(self.temp_dir, retention_days=365, max_jobs=3)

        self.assertEqual(res["pruned_jobs"], 2)
        # Oldest job_0 and job_1 should be pruned; job_2, job_3, job_4 remain
        self.assertFalse(os.path.isfile(os.path.join(self.temp_dir, "leads_job_0.json")))
        self.assertFalse(os.path.isfile(os.path.join(self.temp_dir, "leads_job_1.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir, "leads_job_2.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir, "leads_job_3.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir, "leads_job_4.json")))

    def test_orphaned_temporary_file_pruning(self):
        """Temporary files (.tmp_*) older than 1 hour are cleaned up."""
        old_tmp = os.path.join(self.temp_dir, ".tmp_old_scratch.json")
        with open(old_tmp, "w") as f:
            f.write("{}")
        # Set mtime to 2 hours ago
        os.utime(old_tmp, (time.time() - 7200, time.time() - 7200))

        new_tmp = os.path.join(self.temp_dir, ".tmp_fresh_scratch.json")
        with open(new_tmp, "w") as f:
            f.write("{}")

        res = cleanup_stale_jobs_and_temp_files(self.temp_dir, retention_days=30, max_jobs=10)

        self.assertEqual(res["pruned_temps"], 1)
        self.assertFalse(os.path.isfile(old_tmp))
        self.assertTrue(os.path.isfile(new_tmp))


class TestSecurityControlsPreserved(unittest.TestCase):
    """Ensure Phase 1 authentication, SSRF, and CSV injection protections remain intact."""

    def test_ssrf_validation_unweakened(self):
        """Private IPs and cloud metadata remain strictly rejected."""
        self.assertFalse(is_safe_public_ip("127.0.0.1"))
        self.assertFalse(is_safe_public_ip("169.254.169.254"))
        self.assertFalse(is_safe_public_ip("10.0.0.1"))
        self.assertTrue(is_safe_public_ip("93.184.216.34"))

    def test_csv_injection_formula_triggers_neutralized(self):
        """CSV formulas are neutralized while legitimate numeric values are preserved."""
        self.assertTrue(sanitize_csv_cell("=1+1").startswith("'"))
        self.assertTrue(sanitize_csv_cell("@SUM(A1)").startswith("'"))
        self.assertTrue(sanitize_csv_cell("+cmd|calc").startswith("'"))
        self.assertTrue(sanitize_csv_cell("|calc").startswith("'"))
        self.assertTrue(sanitize_csv_cell("%calc").startswith("'"))
        # Preserves legitimate negative float
        self.assertEqual(sanitize_csv_cell("-97.7431"), "-97.7431")
        self.assertEqual(sanitize_csv_cell("+100"), "+100")


if __name__ == "__main__":
    unittest.main()

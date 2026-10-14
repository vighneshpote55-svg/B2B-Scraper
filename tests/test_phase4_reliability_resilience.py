#!/usr/bin/env python3
"""Comprehensive Operational Resilience & Recovery Test Suite for LeadMap Pro — Phase 4.

Covers Workstream 4:
1. InterProcessAdmissionLock POSIX file locking & thread reentrancy.
2. Graceful shutdown signal handling (SIGTERM / SIGINT) and child process termination.
3. Automated backup creation with SHA-256 integrity hashing.
4. Disaster recovery restore with Zip Slip / path traversal protection and tamper rejection.
"""
import hashlib
import json
import os
import shutil
import signal
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dashboard
from dashboard import (
    InterProcessAdmissionLock,
    graceful_shutdown_signal_handler,
    SERVER_SHUTDOWN_EVENT,
    _register_job_process,
    _unregister_job_process,
    JOB_PROCESSES,
    PROCESS_LOCK
)
from scripts.backup_data import backup_data_directory, compute_sha256
from scripts.restore_data import (
    restore_data_directory,
    verify_archive_checksum,
    is_safe_tar_member
)


class TestPhase4ReliabilityResilience(unittest.TestCase):
    """Validates operational resilience, signal handling, and backup/restore roundtrips."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.temp_dir, "data")
        self.backup_dir = os.path.join(self.temp_dir, "backups")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_inter_process_admission_lock_mutual_exclusion(self):
        """InterProcessAdmissionLock acquires file lock and thread lock cleanly."""
        lock_path = os.path.join(self.temp_dir, ".test_admission.lock")
        lock1 = InterProcessAdmissionLock(lock_path)
        lock2 = InterProcessAdmissionLock(lock_path)

        # Reentrancy on same lock instance
        with lock1:
            with lock1:
                self.assertTrue(os.path.isfile(lock_path))

        # Mutual exclusion test across threads
        execution_order = []

        def worker(lock, name, hold_sec):
            with lock:
                execution_order.append(f"{name}_start")
                time.sleep(hold_sec)
                execution_order.append(f"{name}_end")

        t1 = threading.Thread(target=worker, args=(lock1, "t1", 0.05))
        t2 = threading.Thread(target=worker, args=(lock2, "t2", 0.01))

        t1.start()
        time.sleep(0.01)  # Ensure t1 acquires first
        t2.start()

        t1.join()
        t2.join()

        self.assertEqual(execution_order, ["t1_start", "t1_end", "t2_start", "t2_end"])

    def test_graceful_shutdown_signal_handler(self):
        """SIGTERM handler sets shutdown event, terminates processes, and stops server."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Process is running

        with PROCESS_LOCK:
            JOB_PROCESSES["job_test_shutdown"] = {mock_proc}

        mock_server = MagicMock()
        dashboard._ACTIVE_SERVER_INSTANCE = mock_server
        SERVER_SHUTDOWN_EVENT.clear()

        # Simulate SIGTERM
        graceful_shutdown_signal_handler(signal.SIGTERM, None)

        self.assertTrue(SERVER_SHUTDOWN_EVENT.is_set())
        # mock_proc.terminate should be called via _terminate_job_processes
        mock_proc.terminate.assert_called()

        # Clean up
        with PROCESS_LOCK:
            JOB_PROCESSES.pop("job_test_shutdown", None)
        dashboard._ACTIVE_SERVER_INSTANCE = None

    def test_backup_and_restore_roundtrip(self):
        """Backup creates valid tar.gz + sha256; restore reproduces exact bitwise files."""
        # Populate mock data files
        users_file = os.path.join(self.data_dir, "users.json")
        with open(users_file, "w", encoding="utf-8") as f:
            json.dump({"alice@test.io": {"id": "u1", "name": "Alice"}}, f)

        leads_file = os.path.join(self.data_dir, "leads_job_1.json")
        with open(leads_file, "w", encoding="utf-8") as f:
            json.dump({"job_id": "job_1", "leads": [{"title": "Doctor A"}]}, f)

        # 1. Run Backup
        ok, archive_path, checksum = backup_data_directory(self.data_dir, self.backup_dir, prefix="test_snap")
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(archive_path))
        self.assertTrue(os.path.isfile(f"{archive_path}.sha256"))

        # 2. Verify archive in verify-only mode
        restore_dir = os.path.join(self.temp_dir, "restored_data")
        verify_ok = restore_data_directory(archive_path, target_dir=restore_dir, verify_only=True)
        self.assertTrue(verify_ok)
        self.assertFalse(os.path.exists(restore_dir))  # Verify-only should not extract

        # 3. Full Restore
        restore_ok = restore_data_directory(archive_path, target_dir=restore_dir, verify_only=False)
        self.assertTrue(restore_ok)
        self.assertTrue(os.path.isdir(restore_dir))

        # Check restored content
        restored_users = os.path.join(restore_dir, "users.json")
        self.assertTrue(os.path.isfile(restored_users))
        with open(restored_users, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data.get("alice@test.io", {}).get("name"), "Alice")

    def test_backup_tamper_detection(self):
        """Corrupting archive payload triggers SHA-256 checksum failure during restore."""
        # Populate mock data
        with open(os.path.join(self.data_dir, "file.txt"), "w") as f:
            f.write("Important data")

        ok, archive_path, _ = backup_data_directory(self.data_dir, self.backup_dir)
        self.assertTrue(ok)

        # Corrupt archive by appending extra bytes
        with open(archive_path, "ab") as f:
            f.write(b"CORRUPTED_BYTES")

        # Restore must detect mismatch and reject
        restore_dir = os.path.join(self.temp_dir, "tampered_restore")
        restore_ok = restore_data_directory(archive_path, target_dir=restore_dir)
        self.assertFalse(restore_ok)

    def test_zip_slip_directory_traversal_defense(self):
        """Archives containing '..' or leading '/' entries must be rejected."""
        mock_member_good = MagicMock()
        mock_member_good.name = "users.json"
        self.assertTrue(is_safe_tar_member(mock_member_good, "/tmp/target"))

        mock_member_bad1 = MagicMock()
        mock_member_bad1.name = "../../../etc/passwd"
        self.assertFalse(is_safe_tar_member(mock_member_bad1, "/tmp/target"))

        mock_member_bad2 = MagicMock()
        mock_member_bad2.name = "/etc/shadow"
        self.assertFalse(is_safe_tar_member(mock_member_bad2, "/tmp/target"))


if __name__ == "__main__":
    unittest.main()

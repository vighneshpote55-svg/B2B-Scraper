#!/usr/bin/env python3
"""Automated Disaster Recovery & Restore Utility for LeadMap Pro Data Directory.

Verifies SHA-256 integrity, inspects archive for directory traversal, and restores data safely.
Usage:
    python3 scripts/restore_data.py --archive PATH [--target-dir PATH] [--verify-only]
"""
import argparse
import hashlib
import os
import sys
import tarfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DATA_DIR = os.path.join(REPO_ROOT, "data")


def compute_sha256(file_path):
    """Compute hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_archive_checksum(archive_path):
    """Validate archive against .sha256 checksum file if present."""
    checksum_file = f"{archive_path}.sha256"
    if not os.path.isfile(checksum_file):
        print(f"[*] Notice: No .sha256 file found for '{archive_path}'. Skipping checksum verification.")
        return True

    expected = None
    with open(checksum_file, "r", encoding="utf-8") as f:
        line = f.readline().strip()
        if line:
            expected = line.split()[0].lower()

    if not expected:
        print(f"❌ Checksum file '{checksum_file}' is empty or malformed.")
        return False

    actual = compute_sha256(archive_path).lower()
    if actual != expected:
        print(f"❌ Checksum mismatch!")
        print(f"    Expected: {expected}")
        print(f"    Actual:   {actual}")
        return False

    print(f"✔ Checksum verified successfully: {actual}")
    return True


def is_safe_tar_member(member, target_dir):
    """Prevent Zip Slip / directory traversal vulnerabilities."""
    member_path = member.name
    if member_path.startswith("/") or member_path.startswith("\\"):
        return False
    # Check for directory traversal sequences
    parts = os.path.normpath(member_path).split(os.sep)
    if ".." in parts:
        return False

    resolved_target = os.path.realpath(target_dir)
    resolved_dest = os.path.realpath(os.path.join(target_dir, member_path))
    return resolved_dest.startswith(resolved_target)


def restore_data_directory(archive_path, target_dir=DEFAULT_DATA_DIR, verify_only=False):
    if not os.path.isfile(archive_path):
        print(f"❌ Error: Archive '{archive_path}' does not exist.")
        return False

    print(f"[*] Inspecting archive: {archive_path}")

    # 1. Verify SHA-256 Checksum
    if not verify_archive_checksum(archive_path):
        return False

    # 2. Inspect archive members
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            members = tar.getmembers()
            for m in members:
                if not is_safe_tar_member(m, target_dir):
                    print(f"❌ Unsafe archive member detected (Zip Slip vulnerability): '{m.name}'")
                    return False

            print(f"✔ Archive contains {len(members)} safe entry/entries.")

            if verify_only:
                print("✔ Verify-only mode: archive is valid and safe to restore.")
                return True

            # 3. Extract to target directory
            os.makedirs(target_dir, exist_ok=True)
            print(f"[*] Restoring entries to: {target_dir}")
            tar.extractall(path=target_dir)
            print("✔ Restore completed successfully.")
            return True
    except Exception as e:
        print(f"❌ Restore failed with error: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Restore LeadMap Pro Data Directory")
    parser.add_argument("--archive", required=True, help="Path to backup tar.gz archive")
    parser.add_argument("--target-dir", default=DEFAULT_DATA_DIR, help="Path to restore data directory")
    parser.add_argument("--verify-only", action="store_true", help="Only verify integrity without extracting")
    args = parser.parse_args()

    success = restore_data_directory(args.archive, args.target_dir, args.verify_only)
    sys.exit(0 if success else 1)

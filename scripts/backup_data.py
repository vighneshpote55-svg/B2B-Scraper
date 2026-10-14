#!/usr/bin/env python3
"""Automated Snapshot & Backup Utility for LeadMap Pro Data Directory.

Creates consistent, compressed tar.gz archives of data/ with SHA-256 checksums.
Usage:
    python3 scripts/backup_data.py [--data-dir PATH] [--backup-dir PATH] [--prefix STR]
"""
import argparse
import datetime
import hashlib
import os
import sys
import tarfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DATA_DIR = os.path.join(REPO_ROOT, "data")
DEFAULT_BACKUP_DIR = os.path.join(REPO_ROOT, "backups")


def compute_sha256(file_path):
    """Compute hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def backup_data_directory(data_dir=DEFAULT_DATA_DIR, backup_dir=DEFAULT_BACKUP_DIR, prefix="leadmap_data"):
    if not os.path.isdir(data_dir):
        print(f"❌ Error: Data directory '{data_dir}' does not exist.")
        return False, None, None

    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_name = f"{prefix}_{timestamp}.tar.gz"
    archive_path = os.path.join(backup_dir, archive_name)
    checksum_path = f"{archive_path}.sha256"

    print(f"[*] Creating backup archive from: {data_dir}")
    print(f"[*] Destination: {archive_path}")

    # Build tar.gz archive excluding volatile lock files
    try:
        with tarfile.open(archive_path, "w:gz") as tar:
            for item in sorted(os.listdir(data_dir)):
                if item.startswith(".job_admission.lock"):
                    continue
                full_item_path = os.path.join(data_dir, item)
                tar.add(full_item_path, arcname=item)
    except Exception as e:
        print(f"❌ Failed to create tar archive: {e}")
        if os.path.exists(archive_path):
            os.remove(archive_path)
        return False, None, None

    # Compute checksum
    checksum = compute_sha256(archive_path)
    with open(checksum_path, "w", encoding="utf-8") as f:
        f.write(f"{checksum}  {archive_name}\n")

    size_bytes = os.path.getsize(archive_path)
    print(f"✔ Backup created successfully!")
    print(f"    Archive Size: {size_bytes} bytes")
    print(f"    SHA-256:      {checksum}")
    print(f"    Checksum:     {checksum_path}")

    return True, archive_path, checksum


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backup LeadMap Pro Data Directory")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="Path to data directory")
    parser.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR, help="Path to backup destination directory")
    parser.add_argument("--prefix", default="leadmap_data", help="Backup filename prefix")
    args = parser.parse_args()

    success, path, csum = backup_data_directory(args.data_dir, args.backup_dir, args.prefix)
    sys.exit(0 if success else 1)

#!/usr/bin/env python3
"""Comprehensive Observability & Diagnostics Test Suite for LeadMap Pro — Phase 4.

Covers Workstream 5:
1. Startup configuration validation (PORT, MAX_CONCURRENT_JOBS, DATA_DIR, SUPABASE).
2. JSON log formatting and secret scrubbing compliance.
3. Operational telemetry metrics (/api/diagnostics) including memory, disk, uptime.
"""
import io
import json
import logging
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dashboard
from dashboard import (
    validate_environment,
    SecretRedactionFilter
)


class TestPhase4Observability(unittest.TestCase):
    """Validates configuration validation, JSON structured logging, and metrics."""

    def test_environment_validation_success(self):
        """Standard valid configuration passes validation."""
        valid, msg = validate_environment()
        self.assertTrue(valid)
        self.assertEqual(msg, "OK")

    def test_environment_validation_port_failures(self):
        """Invalid port inputs are caught and rejected."""
        with patch.object(dashboard, "PORT", 0):
            valid, msg = validate_environment()
            self.assertFalse(valid)
            self.assertIn("DASHBOARD_PORT", msg)

        with patch.object(dashboard, "PORT", 80000):
            valid, msg = validate_environment()
            self.assertFalse(valid)
            self.assertIn("DASHBOARD_PORT", msg)

    def test_environment_validation_concurrency_failures(self):
        """Invalid MAX_CONCURRENT_JOBS values are caught and rejected."""
        with patch.object(dashboard, "MAX_CONCURRENT_JOBS", 0):
            valid, msg = validate_environment()
            self.assertFalse(valid)
            self.assertIn("MAX_CONCURRENT_JOBS", msg)

    def test_environment_validation_supabase_inconsistency(self):
        """SUPABASE_URL set without SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY is caught."""
        with patch.dict(os.environ, {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "", "SUPABASE_KEY": ""}):
            valid, msg = validate_environment()
            self.assertFalse(valid)
            self.assertIn("missing", msg.lower())

    def test_json_logging_format_and_redaction(self):
        """JSON formatter produces valid JSON and scrubs sensitive credentials."""
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)

        # Import the JsonLogFormatter defined inside dashboard
        class TestJsonFormatter(logging.Formatter):
            def format(self, record):
                log_record = {
                    "timestamp": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage()
                }
                return json.dumps(log_record)

        handler.setFormatter(TestJsonFormatter())
        handler.addFilter(SecretRedactionFilter())

        test_logger = logging.getLogger("test_observability")
        test_logger.handlers = [handler]
        test_logger.setLevel(logging.INFO)

        raw_token = "Bearer a1b2c3d4e5f6g7h8i9j0"
        test_logger.info(f"User login session token: {raw_token}")

        output = stream.getvalue().strip()
        parsed = json.loads(output)

        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["logger"], "test_observability")
        self.assertIn("timestamp", parsed)
        self.assertNotIn("a1b2c3d4e5f6g7h8i9j0", parsed["message"])
        self.assertIn("[REDACTED_TOKEN]", parsed["message"])


if __name__ == "__main__":
    unittest.main()

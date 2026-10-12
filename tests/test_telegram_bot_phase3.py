#!/usr/bin/env python3
"""Dedicated Phase 3 Test Suite for LeadMap Pro Telegram Bot Integration.

Tests:
1. User authorization guard & numeric ID whitelist security (TELEGRAM_ALLOWED_USER_IDS).
2. Missing configuration handling (missing token or empty allowed IDs).
3. Command parsing for /scrape queries, quoted arguments, length limits, and depth bounds.
4. Command dispatching (/start, /help, /scrape, /status, /recent, /cancel, /stop, /leads, /export).
5. Duplicate update idempotency (prevents starting multiple jobs on repeated Telegram updates).
6. Scraper offline pre-flight checks and job creation failure handling.
7. Job state persistence & chat association (data/telegram_state.json).
8. Lead document export generation, multipart payloads, and CSV formula injection neutralization.
9. Error sanitization ensuring tokens, Supabase keys, and stack traces are never leaked.
10. Telegram API failure handling (network errors, HTTP errors, timeouts).
"""
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import scripts.telegram_bot as tb
import scripts.scraper_engine as se


class MockTelegramClient(tb.TelegramClient):
    """Mock TelegramClient for testing without external network access."""

    def __init__(self, token="123456789:MockTelegramTokenForTests"):
        super().__init__(token=token, api_base="http://127.0.0.1:9999")
        self.sent_messages = []
        self.sent_documents = []

    def send_message(self, chat_id: int, text: str, reply_to_message_id: int = None) -> dict:
        self.sent_messages.append({
            "chat_id": chat_id,
            "text": text,
            "reply_to_message_id": reply_to_message_id
        })
        return {"ok": True, "result": {"message_id": len(self.sent_messages)}}

    def send_document(self, chat_id: int, file_bytes: bytes, filename: str, caption: str = "") -> dict:
        self.sent_documents.append({
            "chat_id": chat_id,
            "file_bytes": file_bytes,
            "filename": filename,
            "caption": caption
        })
        return {"ok": True, "result": {"message_id": len(self.sent_documents)}}


class TestTelegramAuthorization(unittest.TestCase):
    """Verify Telegram user authorization middleware and numeric ID whitelist enforcement."""

    def setUp(self):
        self._orig_allowed = os.environ.get("TELEGRAM_ALLOWED_USER_IDS")

    def tearDown(self):
        if self._orig_allowed is not None:
            os.environ["TELEGRAM_ALLOWED_USER_IDS"] = self._orig_allowed
        else:
            os.environ.pop("TELEGRAM_ALLOWED_USER_IDS", None)

    def test_authorized_user_is_allowed(self):
        os.environ["TELEGRAM_ALLOWED_USER_IDS"] = "111222333, 444555666"
        self.assertTrue(tb.is_user_authorized(111222333))
        self.assertTrue(tb.is_user_authorized(444555666))
        self.assertTrue(tb.is_user_authorized("111222333"))

    def test_unauthorized_user_is_denied(self):
        os.environ["TELEGRAM_ALLOWED_USER_IDS"] = "111222333, 444555666"
        self.assertFalse(tb.is_user_authorized(999888777))
        self.assertFalse(tb.is_user_authorized(12345))

    def test_empty_or_missing_whitelist_denies_all_users(self):
        os.environ.pop("TELEGRAM_ALLOWED_USER_IDS", None)
        self.assertFalse(tb.is_user_authorized(111222333))

        os.environ["TELEGRAM_ALLOWED_USER_IDS"] = "   "
        self.assertFalse(tb.is_user_authorized(111222333))

    def test_non_numeric_user_id_rejected(self):
        os.environ["TELEGRAM_ALLOWED_USER_IDS"] = "111222333"
        self.assertFalse(tb.is_user_authorized("admin_user"))
        self.assertFalse(tb.is_user_authorized(None))


class TestMissingConfiguration(unittest.TestCase):
    """Verify safe behavior when configuration variables are missing."""

    def setUp(self):
        self._orig_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self._orig_allowed = os.environ.get("TELEGRAM_ALLOWED_USER_IDS")

    def tearDown(self):
        if self._orig_token is not None:
            os.environ["TELEGRAM_BOT_TOKEN"] = self._orig_token
        else:
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        if self._orig_allowed is not None:
            os.environ["TELEGRAM_ALLOWED_USER_IDS"] = self._orig_allowed
        else:
            os.environ.pop("TELEGRAM_ALLOWED_USER_IDS", None)

    def test_missing_token_stops_polling_safely(self):
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        # run_bot_polling should safely log and exit without exception
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            tb.run_bot_polling()
            self.assertIn("TELEGRAM_BOT_TOKEN is not configured", mock_stderr.getvalue())

    def test_empty_allowed_users_denies_all(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "fake_token_123"
        os.environ["TELEGRAM_ALLOWED_USER_IDS"] = ""
        allowed = tb.get_allowed_user_ids()
        self.assertEqual(len(allowed), 0)
        self.assertFalse(tb.is_user_authorized(12345))


class TestCommandParsing(unittest.TestCase):
    """Verify parsing and bounding of /scrape command inputs."""

    def test_quoted_keyword_and_city_with_depth(self):
        k, c, d, err = tb.parse_scrape_command('/scrape "dentists" "Austin, TX" 10')
        self.assertEqual(err, "")
        self.assertEqual(k, "dentists")
        self.assertEqual(c, "Austin, TX")
        self.assertEqual(d, 10)

    def test_in_syntax_with_depth(self):
        k, c, d, err = tb.parse_scrape_command('/scrape cafes in Miami, FL 8')
        self.assertEqual(err, "")
        self.assertEqual(k, "cafes")
        self.assertEqual(c, "Miami, FL")
        self.assertEqual(d, 8)

    def test_depth_flag_parsing(self):
        k, c, d, err = tb.parse_scrape_command('/scrape gyms in Denver --depth 15')
        self.assertEqual(err, "")
        self.assertEqual(k, "gyms")
        self.assertEqual(c, "Denver")
        self.assertEqual(d, 15)

    def test_missing_query_returns_error(self):
        k, c, d, err = tb.parse_scrape_command('/scrape')
        self.assertIn("keyword", err.lower())

    def test_depth_bounds_enforced(self):
        # Upper bound 50
        _, _, d_max, _ = tb.parse_scrape_command('/scrape lawyers in Chicago 999')
        self.assertEqual(d_max, 50)

        # Lower bound 1
        _, _, d_min, _ = tb.parse_scrape_command('/scrape lawyers in Chicago 0')
        self.assertEqual(d_min, 1)

    def test_excessively_long_inputs_rejected(self):
        long_kw = "A" * (tb.MAX_KEYWORD_LENGTH + 10)
        _, _, _, err_kw = tb.parse_scrape_command(f'/scrape "{long_kw}" "Denver"')
        self.assertIn("too long", err_kw.lower())

        long_loc = "B" * (tb.MAX_LOCATION_LENGTH + 10)
        _, _, _, err_loc = tb.parse_scrape_command(f'/scrape "dentists" "{long_loc}"')
        self.assertIn("too long", err_loc.lower())


class TestTelegramBotHandler(unittest.TestCase):
    """Verify command dispatching, state management, and safety controls."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_data_dir = se.DATA_DIR
        se.DATA_DIR = self.temp_dir
        tb.DATA_DIR = self.temp_dir
        tb.STATE_FILE = os.path.join(self.temp_dir, "telegram_state.json")
        tb.PROCESSED_UPDATES_FILE = os.path.join(self.temp_dir, "telegram_processed_updates.json")
        tb.PROCESSED_UPDATES_CACHE.clear()

        self.mock_client = MockTelegramClient()
        self.handler = tb.TelegramBotHandler(self.mock_client)

        os.environ["TELEGRAM_ALLOWED_USER_IDS"] = "1001, 1002"

    def tearDown(self):
        se.DATA_DIR = self.orig_data_dir
        tb.DATA_DIR = self.orig_data_dir
        tb.PROCESSED_UPDATES_CACHE.clear()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_unauthorized_user_message_rejected_with_403(self):
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 9999},
                "from": {"id": 99999},  # Unauthorized
                "text": "/help"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("Access Denied", self.mock_client.sent_messages[0]["text"])

    def test_start_and_help_command(self):
        for cmd in ("/start", "/help"):
            self.mock_client.sent_messages.clear()
            update = {
                "update_id": 2,
                "message": {
                    "chat": {"id": 1001},
                    "from": {"id": 1001},  # Authorized
                    "text": cmd
                }
            }
            # Clear duplicate cache so update_id 2 works twice in loop
            tb.PROCESSED_UPDATES_CACHE.clear()
            self.handler.handle_update(update)
            self.assertEqual(len(self.mock_client.sent_messages), 1)
            self.assertIn("LeadMap Pro", self.mock_client.sent_messages[0]["text"])
            self.assertIn("/scrape", self.mock_client.sent_messages[0]["text"])

    @patch("scripts.telegram_bot.check_scraper_health", return_value=True)
    @patch("scripts.telegram_bot.initiate_scrape_job")
    @patch("scripts.telegram_bot.poll_and_process_job")
    def test_scrape_command_starts_job(self, mock_poll, mock_init, mock_health):
        mock_init.return_value = (True, "job_test_123", {
            "keyword": "dentists",
            "city": "Austin, TX",
            "full_query": "dentists in Austin, TX",
            "depth": 5
        })
        mock_poll.return_value = {
            "id": "job_test_123",
            "status": "ok",
            "metrics": {
                "total": 12,
                "hot": 5,
                "warm": 4,
                "cold": 3,
                "with_email": 10,
                "with_phone": 12,
                "supabase_synced": 12
            }
        }

        update = {
            "update_id": 3,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/scrape dentists in Austin, TX"
            }
        }
        self.handler.handle_update(update)

        self.assertTrue(len(self.mock_client.sent_messages) >= 2)
        texts = [m["text"] for m in self.mock_client.sent_messages]
        self.assertTrue(any("job_test_123" in t for t in texts))

        last_job = tb.get_user_last_job(1001)
        self.assertEqual(last_job, "job_test_123")

    @patch("scripts.telegram_bot.check_scraper_health", return_value=False)
    def test_scrape_command_fails_when_scraper_offline(self, mock_health):
        update = {
            "update_id": 4,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/scrape dentists in Austin, TX"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("unreachable", self.mock_client.sent_messages[0]["text"].lower())

    @patch("scripts.telegram_bot.check_scraper_health", return_value=True)
    @patch("scripts.telegram_bot.initiate_scrape_job")
    def test_scrape_command_initiate_job_failure_handled(self, mock_init, mock_health):
        mock_init.return_value = (False, "Connection to Docker backend failed", {})
        update = {
            "update_id": 5,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/scrape dentists in Austin, TX"
            }
        }
        self.handler.handle_update(update)
        texts = [m["text"] for m in self.mock_client.sent_messages]
        self.assertTrue(any("Failed to start scrape" in t for t in texts))

    def test_duplicate_update_id_discarded(self):
        update = {
            "update_id": 999,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/help"
            }
        }
        # First delivery should succeed
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)

        # Second delivery with the SAME update_id should be discarded immediately
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)  # No new message

    def test_status_command_for_existing_job(self):
        job_id = "job_status_test"
        job_file = os.path.join(self.temp_dir, f"leads_{job_id}.json")
        with open(job_file, "w") as f:
            json.dump({
                "job_id": job_id,
                "completed_at": 1000,
                "leads": [{"title": "Business A"}],
                "params": {"keyword": "test"},
                "metrics": {"total": 1}
            }, f)

        update = {
            "update_id": 10,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": f"/status {job_id}"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("100%", self.mock_client.sent_messages[0]["text"])

    def test_status_command_invalid_job_id(self):
        update = {
            "update_id": 11,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/status ../../../etc/passwd"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("Invalid Job ID", self.mock_client.sent_messages[0]["text"])

    def test_status_command_missing_job_id_no_previous_job(self):
        update = {
            "update_id": 12,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/status"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("Please specify a job ID", self.mock_client.sent_messages[0]["text"])

    @patch("scripts.telegram_bot.is_supabase_configured", return_value=True)
    @patch("scripts.telegram_bot.fetch_recent_scrape_jobs_from_supabase")
    def test_recent_command_from_supabase(self, mock_fetch, mock_sb_cfg):
        mock_fetch.return_value = [
            {
                "scraper_job_id": "job_sb_001",
                "query": "Plumbers in Dallas",
                "status": "ok",
                "total_leads": 15
            }
        ]
        update = {
            "update_id": 13,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/recent"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        text = self.mock_client.sent_messages[0]["text"]
        self.assertIn("Supabase", text)
        self.assertIn("Plumbers in Dallas", text)
        self.assertIn("job_sb_001", text)

    @patch("scripts.telegram_bot.is_supabase_configured", return_value=False)
    def test_recent_command_local_fallback(self, mock_sb_cfg):
        # Create mock local file
        job_id = "job_local_001"
        job_file = os.path.join(self.temp_dir, f"leads_{job_id}.json")
        with open(job_file, "w") as f:
            json.dump({
                "job_id": job_id,
                "params": {"full_query": "Bakers in Chicago"},
                "leads": [{"title": "Chicago Bakery"}]
            }, f)

        update = {
            "update_id": 14,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/recent"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        text = self.mock_client.sent_messages[0]["text"]
        self.assertIn("Local", text)
        self.assertIn("Bakers in Chicago", text)

    @patch("scripts.telegram_bot.cancel_job", return_value=True)
    def test_cancel_command_valid_active_job(self, mock_cancel):
        update = {
            "update_id": 15,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/cancel job_cancel_123"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("Cancellation requested", self.mock_client.sent_messages[0]["text"])

    def test_cancel_command_missing_job_id(self):
        update = {
            "update_id": 16,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/cancel"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("Please specify the Job ID", self.mock_client.sent_messages[0]["text"])

    def test_cancel_command_invalid_job_id(self):
        update = {
            "update_id": 17,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/cancel ../invalid/path"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("Invalid Job ID", self.mock_client.sent_messages[0]["text"])

    def test_leads_summary_command(self):
        job_id = "job_leads_test"
        job_file = os.path.join(self.temp_dir, f"leads_{job_id}.json")
        with open(job_file, "w") as f:
            json.dump({
                "job_id": job_id,
                "leads": [
                    {
                        "title": "Dr. Smile Dental",
                        "clean_phone": "+1 (512) 555-0199",
                        "emails": "contact@drsmile.com",
                        "lead_score": 85,
                        "lead_tier": "HOT"
                    }
                ],
                "metrics": {"total": 1, "hot": 1, "warm": 0, "cold": 0, "with_email": 1, "with_phone": 1}
            }, f)

        update = {
            "update_id": 18,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": f"/leads {job_id}"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        msg_text = self.mock_client.sent_messages[0]["text"]
        self.assertIn("Dr. Smile Dental", msg_text)
        self.assertIn("HOT", msg_text)

    def test_export_document_command(self):
        job_id = "job_export_test"
        job_file = os.path.join(self.temp_dir, f"leads_{job_id}.json")
        with open(job_file, "w") as f:
            json.dump({
                "job_id": job_id,
                "leads": [
                    {
                        "title": "=SUM(1,2)",  # Formula injection attempt
                        "clean_phone": "+1 (512) 555-0199",
                        "emails": "safe@dental.com",
                        "lead_score": 75,
                        "lead_tier": "HOT"
                    }
                ]
            }, f)

        update = {
            "update_id": 19,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": f"/export {job_id} csv"
            }
        }
        self.handler.handle_update(update)

        self.assertEqual(len(self.mock_client.sent_documents), 1)
        doc = self.mock_client.sent_documents[0]
        self.assertIn("leads_job_expo_clean.csv", doc["filename"])

        csv_content = doc["file_bytes"].decode("utf-8")
        # Formula injection character '=' must be escaped with single-quote
        self.assertIn("'=SUM(1,2)", csv_content)

    def test_stop_command_alias(self):
        job_id = "job_stop_test"
        with se.JOB_LOCK:
            se.JOB_CACHE[job_id] = {
                "id": job_id,
                "status": "working",
                "progress_percent": 30
            }

        update = {
            "update_id": 20,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": f"/stop {job_id}"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("Cancellation requested", self.mock_client.sent_messages[0]["text"])
        self.assertEqual(se.JOB_CACHE[job_id]["status"], "cancelled")

    def test_unknown_command(self):
        update = {
            "update_id": 21,
            "message": {
                "chat": {"id": 1001},
                "from": {"id": 1001},
                "text": "/unknowncmd"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("Unknown command", self.mock_client.sent_messages[0]["text"])

    def test_error_sanitization_masks_token_and_keys(self):
        token = "987654321:SecretTelegramBotToken"
        os.environ["TELEGRAM_BOT_TOKEN"] = token
        os.environ["SUPABASE_KEY"] = "super-secret-supabase-service-key"

        # Leaking token in exception
        err1 = Exception(f"Failed to fetch {token} from api.telegram.org")
        sanitized1 = tb.sanitize_error_message(err1)
        self.assertNotIn(token, sanitized1)
        self.assertIn("[REDACTED_BOT_TOKEN]", sanitized1)

        # Leaking Supabase key
        err2 = Exception("Failed connecting with super-secret-supabase-service-key")
        sanitized2 = tb.sanitize_error_message(err2)
        self.assertNotIn("super-secret-supabase-service-key", sanitized2)
        self.assertIn("[REDACTED_KEY]", sanitized2)

        # Database / traceback error disclosure
        err3 = Exception("psycopg2.OperationalError: server closed the connection unexpectedly\nTraceback (most recent call last):")
        sanitized3 = tb.sanitize_error_message(err3)
        self.assertNotIn("psycopg2", sanitized3)
        self.assertIn("internal service error", sanitized3)


class TestTelegramClientApiFailures(unittest.TestCase):
    """Verify that network errors, HTTP error statuses, and timeouts are safely caught."""

    def test_send_message_http_error_handled(self):
        client = tb.TelegramClient("test_token", api_base="http://127.0.0.1:9999")
        # Mock urllib.request.urlopen raising HTTPError
        with patch("urllib.request.urlopen") as mock_url:
            mock_err = urllib.error.HTTPError(
                url="http://127.0.0.1:9999/sendMessage",
                code=400,
                msg="Bad Request",
                hdrs={},
                fp=io.BytesIO(b'{"ok": false, "description": "Chat not found"}')
            )
            mock_url.side_effect = mock_err
            resp = client.send_message(chat_id=123, text="Hello")
            self.assertFalse(resp.get("ok"))
            self.assertEqual(resp.get("description"), "Chat not found")

    def test_send_document_http_error_handled(self):
        client = tb.TelegramClient("test_token", api_base="http://127.0.0.1:9999")
        with patch("urllib.request.urlopen") as mock_url:
            mock_err = urllib.error.HTTPError(
                url="http://127.0.0.1:9999/sendDocument",
                code=500,
                msg="Internal Error",
                hdrs={},
                fp=io.BytesIO(b'{"ok": false, "description": "Telegram service unavailable"}')
            )
            mock_url.side_effect = mock_err
            resp = client.send_document(chat_id=123, file_bytes=b"data", filename="leads.csv")
            self.assertFalse(resp.get("ok"))
            self.assertEqual(resp.get("description"), "Telegram service unavailable")

    def test_get_updates_timeout_handled(self):
        client = tb.TelegramClient("test_token", api_base="http://127.0.0.1:9999")
        with patch("urllib.request.urlopen") as mock_url:
            mock_url.side_effect = TimeoutError("Polling timeout")
            updates = client.get_updates(timeout=20)
            self.assertEqual(updates, [])


class TestScraperEngineUnit(unittest.TestCase):
    """Verify scraper engine export formats and validation helpers."""

    def test_validate_job_id(self):
        self.assertTrue(se.validate_job_id("valid_job_123-abc"))
        self.assertFalse(se.validate_job_id("../../../etc/passwd"))
        self.assertFalse(se.validate_job_id(""))
        self.assertFalse(se.validate_job_id("job with spaces"))

    def test_export_leads_csv_and_crm(self):
        leads = [
            {
                "title": "Alpha Tech",
                "clean_phone": "+1 (800) 555-0100",
                "emails": "hello@alphatech.io",
                "website": "https://alphatech.io",
                "lead_score": 90,
                "lead_tier": "HOT"
            }
        ]

        # Standard CSV
        csv_bytes, suffix_csv, mime_csv = se.export_leads(leads, "csv")
        self.assertEqual(suffix_csv, "_clean.csv")
        self.assertEqual(mime_csv, "text/csv")
        self.assertIn("Alpha Tech", csv_bytes.decode("utf-8"))

        # CRM CSV
        crm_bytes, suffix_crm, _ = se.export_leads(leads, "crm")
        self.assertEqual(suffix_crm, "_crm_outreach.csv")
        self.assertIn("Company Name", crm_bytes.decode("utf-8"))
        self.assertIn("Alpha Tech", crm_bytes.decode("utf-8"))

        # JSON
        json_bytes, suffix_json, mime_json = se.export_leads(leads, "json")
        self.assertEqual(suffix_json, ".json")
        self.assertEqual(mime_json, "application/json")
        parsed = json.loads(json_bytes.decode("utf-8"))
        self.assertEqual(parsed[0]["title"], "Alpha Tech")


if __name__ == "__main__":
    unittest.main()

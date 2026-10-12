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
        _, _, d_max, _ = tb.parse_scrape_command('/scrape lawyers in Chicago 999')
        self.assertEqual(d_max, 50)

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
                "from": {"id": 99999},
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
                    "from": {"id": 1001},
                    "text": cmd
                }
            }
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
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)

        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)

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
                        "title": "=SUM(1,2)",
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

        err1 = Exception(f"Failed to fetch {token} from api.telegram.org")
        sanitized1 = tb.sanitize_error_message(err1)
        self.assertNotIn(token, sanitized1)
        self.assertIn("[REDACTED_BOT_TOKEN]", sanitized1)

        err2 = Exception("Failed connecting with super-secret-supabase-service-key")
        sanitized2 = tb.sanitize_error_message(err2)
        self.assertNotIn("super-secret-supabase-service-key", sanitized2)
        self.assertIn("[REDACTED_KEY]", sanitized2)

        err3 = Exception("psycopg2.OperationalError: server closed the connection unexpectedly\nTraceback (most recent call last):")
        sanitized3 = tb.sanitize_error_message(err3)
        self.assertNotIn("psycopg2", sanitized3)
        self.assertIn("internal service error", sanitized3)


class TestTelegramClientApiFailures(unittest.TestCase):
    """Verify that network errors, HTTP error statuses, and timeouts are safely caught."""

    def test_send_message_http_error_handled(self):
        client = tb.TelegramClient("test_token", api_base="http://127.0.0.1:9999")
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

        csv_bytes, suffix_csv, mime_csv = se.export_leads(leads, "csv")
        self.assertEqual(suffix_csv, "_clean.csv")
        self.assertEqual(mime_csv, "text/csv")
        self.assertIn("Alpha Tech", csv_bytes.decode("utf-8"))

        crm_bytes, suffix_crm, _ = se.export_leads(leads, "crm")
        self.assertEqual(suffix_crm, "_crm_outreach.csv")
        self.assertIn("Company Name", crm_bytes.decode("utf-8"))
        self.assertIn("Alpha Tech", crm_bytes.decode("utf-8"))

        json_bytes, suffix_json, mime_json = se.export_leads(leads, "json")
        self.assertEqual(suffix_json, ".json")
        self.assertEqual(mime_json, "application/json")
        parsed = json.loads(json_bytes.decode("utf-8"))
        self.assertEqual(parsed[0]["title"], "Alpha Tech")


class TestPhase4TelegramSecurityAndReliability(unittest.TestCase):
    """Phase 4 Security and Reliability verification."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_data_dir = tb.DATA_DIR
        self.orig_processed_file = tb.PROCESSED_UPDATES_FILE
        self.orig_state_file = tb.STATE_FILE
        self.orig_allowed = os.environ.get("TELEGRAM_ALLOWED_USER_IDS")

        tb.DATA_DIR = self.temp_dir
        tb.PROCESSED_UPDATES_FILE = os.path.join(self.temp_dir, "telegram_processed_updates.json")
        tb.STATE_FILE = os.path.join(self.temp_dir, "telegram_state.json")
        tb.PROCESSED_UPDATES_CACHE.clear()
        tb.IN_FLIGHT_UPDATES.clear()

        os.environ["TELEGRAM_ALLOWED_USER_IDS"] = "1001, 1002"
        self.mock_client = MockTelegramClient()
        self.handler = tb.TelegramBotHandler(self.mock_client)

    def tearDown(self):
        tb.DATA_DIR = self.orig_data_dir
        tb.PROCESSED_UPDATES_FILE = self.orig_processed_file
        tb.STATE_FILE = self.orig_state_file
        tb.PROCESSED_UPDATES_CACHE.clear()
        tb.IN_FLIGHT_UPDATES.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if self.orig_allowed is not None:
            os.environ["TELEGRAM_ALLOWED_USER_IDS"] = self.orig_allowed
        else:
            os.environ.pop("TELEGRAM_ALLOWED_USER_IDS", None)

    # ── 1. Private-chat-only access ──────────────────────────────────────────

    def test_unauthorized_user_in_private_chat_denied(self):
        update = {
            "update_id": 101,
            "message": {
                "chat": {"id": 9999, "type": "private"},
                "from": {"id": 9999},
                "text": "/scrape dentists in Austin, TX"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("Access Denied", self.mock_client.sent_messages[0]["text"])

    def test_group_chat_rejected_without_leaking_data(self):
        update = {
            "update_id": 102,
            "message": {
                "chat": {"id": -100123456, "type": "group"},
                "from": {"id": 1001},
                "text": "/leads job_secret_123"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        text = self.mock_client.sent_messages[0]["text"]
        self.assertIn("Access Restricted", text)
        self.assertIn("private", text.lower())
        self.assertNotIn("Lead Intelligence Summary", text)

    def test_supergroup_chat_rejected_without_leaking_data(self):
        update = {
            "update_id": 103,
            "message": {
                "chat": {"id": -100987654, "type": "supergroup"},
                "from": {"id": 1001},
                "text": "/export job_secret_123"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertEqual(len(self.mock_client.sent_documents), 0)
        text = self.mock_client.sent_messages[0]["text"]
        self.assertIn("Access Restricted", text)

    def test_unauthorized_user_in_group_denied(self):
        update = {
            "update_id": 104,
            "message": {
                "chat": {"id": -100123456, "type": "group"},
                "from": {"id": 5555},
                "text": "/status job_123"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("Access Denied", self.mock_client.sent_messages[0]["text"])

    def test_authorized_private_chat_allowed(self):
        update = {
            "update_id": 105,
            "message": {
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001},
                "text": "/help"
            }
        }
        self.handler.handle_update(update)
        self.assertEqual(len(self.mock_client.sent_messages), 1)
        self.assertIn("LeadMap Pro", self.mock_client.sent_messages[0]["text"])

    # ── 2. Crash-safe deduplication ──────────────────────────────────────────

    def test_update_deduplication_persists_immediately(self):
        update = {
            "update_id": 201,
            "message": {
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001},
                "text": "/help"
            }
        }
        self.handler.handle_update(update)
        self.assertTrue(os.path.isfile(tb.PROCESSED_UPDATES_FILE))
        with open(tb.PROCESSED_UPDATES_FILE) as f:
            data = json.load(f)
            self.assertIn(201, data.get("processed_ids", []))

    def test_deduplication_restart_recovery(self):
        tb.mark_update_processed(301)
        tb.mark_update_processed(302)
        # Clear in-memory cache to simulate restart
        tb.PROCESSED_UPDATES_CACHE.clear()
        reloaded = tb._load_processed_updates()
        self.assertIn(301, reloaded)
        self.assertIn(302, reloaded)

    def test_deduplication_malformed_state_file_recovery(self):
        with open(tb.PROCESSED_UPDATES_FILE, "w") as f:
            f.write("{malformed json corrupt content...")
        reloaded = tb._load_processed_updates()
        self.assertEqual(reloaded, set())

    def test_in_flight_concurrent_update_rejection(self):
        self.assertTrue(tb.begin_update_processing(401))
        # Second attempt while in flight
        self.assertFalse(tb.begin_update_processing(401))
        tb.finish_update_processing(401)
        # Subsequent attempt after completion
        self.assertTrue(tb.is_update_duplicate(401))
        self.assertFalse(tb.begin_update_processing(401))

    def test_persistence_failure_handled_gracefully(self):
        with patch("builtins.open", side_effect=OSError("Read-only file system")):
            # Should not raise exception
            tb.mark_update_processed(501)
            self.assertIn(501, tb.PROCESSED_UPDATES_CACHE)

    # ── 3. Prevent duplicate scraper jobs (Atomic Reservation) ───────────────

    @patch("scripts.scraper_engine.is_supabase_configured", return_value=True)
    @patch("scripts.scraper_engine.check_scraper_health", return_value=True)
    @patch("scripts.scraper_engine.geocode_place", return_value=("30.2672", "-97.7431", "Austin, TX"))
    @patch("scripts.scraper_engine.create_scrape_job_record")
    @patch("scripts.scraper_engine.fetch_scrape_job_by_update_id")
    @patch("scripts.scraper_engine.scraper_req")
    def test_duplicate_reservation_does_not_call_scraper(self, mock_scraper_req, mock_fetch_dup, mock_create_job, mock_geo, mock_health, mock_sb):
        mock_create_job.return_value = {
            "success": False,
            "is_duplicate": True,
            "error": "HTTP 409: duplicate key value violates unique constraint"
        }
        mock_fetch_dup.return_value = {"id": "res-uuid-123", "scraper_job_id": "job-existing-456"}

        params = {
            "keyword": "dentists",
            "city": "Austin, TX",
            "depth": 5,
            "telegram_update_id": 601,
            "telegram_user_id": 1001,
            "telegram_chat_id": 1001
        }
        success, msg, resolved = se.initiate_scrape_job(params)
        self.assertFalse(success)
        self.assertTrue(resolved.get("duplicate"))
        self.assertEqual(resolved.get("job_id"), "job-existing-456")
        # Scraper container must NOT have been called
        mock_scraper_req.assert_not_called()

    @patch("scripts.scraper_engine.is_supabase_configured", return_value=True)
    @patch("scripts.scraper_engine.check_scraper_health", return_value=True)
    @patch("scripts.scraper_engine.geocode_place", return_value=("30.2672", "-97.7431", "Austin, TX"))
    @patch("scripts.scraper_engine.create_scrape_job_record")
    @patch("scripts.scraper_engine.scraper_req")
    def test_database_outage_distinct_from_duplicate(self, mock_scraper_req, mock_create_job, mock_geo, mock_health, mock_sb):
        mock_create_job.return_value = {
            "success": False,
            "is_duplicate": False,
            "error": "HTTP 503: Service Unavailable"
        }

        params = {
            "keyword": "dentists",
            "city": "Austin, TX",
            "depth": 5,
            "telegram_update_id": 602,
            "telegram_user_id": 1001,
            "telegram_chat_id": 1001
        }
        success, msg, resolved = se.initiate_scrape_job(params)
        self.assertFalse(success)
        self.assertTrue(resolved.get("db_error"))
        self.assertFalse(resolved.get("duplicate", False))
        self.assertIn("Database error reserving job", msg)
        mock_scraper_req.assert_not_called()

    @patch("scripts.scraper_engine.is_supabase_configured", return_value=True)
    @patch("scripts.scraper_engine.check_scraper_health", return_value=True)
    @patch("scripts.scraper_engine.geocode_place", return_value=("30.2672", "-97.7431", "Austin, TX"))
    @patch("scripts.scraper_engine.create_scrape_job_record", return_value={"success": True, "is_duplicate": False})
    @patch("scripts.scraper_engine.update_scrape_job_record")
    @patch("scripts.scraper_engine.scraper_req", side_effect=Exception("Connection reset by peer"))
    def test_scraper_submission_failure_updates_db_to_failed(self, mock_scraper_req, mock_update_job, mock_create_job, mock_geo, mock_health, mock_sb):
        params = {
            "keyword": "dentists",
            "city": "Austin, TX",
            "depth": 5,
            "telegram_update_id": 603,
            "telegram_user_id": 1001,
            "telegram_chat_id": 1001
        }
        success, msg, resolved = se.initiate_scrape_job(params)
        self.assertFalse(success)
        self.assertTrue(resolved.get("submission_failed"))
        mock_update_job.assert_called_once()
        args, kwargs = mock_update_job.call_args
        updates = args[1]
        self.assertEqual(updates.get("status"), "failed")
        self.assertIn("Failed to submit", updates.get("stage"))

    @patch("scripts.scraper_engine.is_supabase_configured", return_value=True)
    @patch("scripts.scraper_engine.check_scraper_health", return_value=True)
    @patch("scripts.scraper_engine.geocode_place", return_value=("30.2672", "-97.7431", "Austin, TX"))
    @patch("scripts.scraper_engine.create_scrape_job_record", return_value={"success": True, "is_duplicate": False})
    @patch("scripts.scraper_engine.update_scrape_job_record")
    @patch("scripts.scraper_engine.scraper_req", return_value=(200, b'{"id": "job-successful-789"}'))
    def test_successful_reservation_followed_by_submission(self, mock_scraper_req, mock_update_job, mock_create_job, mock_geo, mock_health, mock_sb):
        params = {
            "keyword": "dentists",
            "city": "Austin, TX",
            "depth": 5,
            "telegram_update_id": 604,
            "telegram_user_id": 1001,
            "telegram_chat_id": 1001
        }
        success, job_id, resolved = se.initiate_scrape_job(params)
        self.assertTrue(success)
        self.assertEqual(job_id, "job-successful-789")
        mock_update_job.assert_called_once()
        args, _ = mock_update_job.call_args
        updates = args[1]
        self.assertEqual(updates.get("status"), "running")
        self.assertEqual(updates.get("scraper_job_id"), "job-successful-789")

    # ── 4. Query Encoding and Status Reporting ────────────────────────────────

    def test_malformed_job_ids_rejected_in_supabase_client(self):
        from scripts.supabase_client import update_scrape_job_record, fetch_scrape_job_by_id, validate_job_id_format
        self.assertFalse(validate_job_id_format("../../../etc/passwd"))
        self.assertFalse(validate_job_id_format("id with spaces"))
        self.assertFalse(validate_job_id_format(""))

        res = update_scrape_job_record("../../../evil", {"status": "failed"}, supabase_url="http://mock", supabase_key="key")
        self.assertFalse(res.get("success"))
        self.assertIn("Invalid job_id", res.get("error"))

        res2 = fetch_scrape_job_by_id("../../../evil", supabase_url="http://mock", supabase_key="key")
        self.assertEqual(res2, {})

    def test_status_reporting_estimated_vs_actual_progress(self):
        with se.JOB_LOCK:
            se.JOB_CACHE["job-working-est"] = {
                "id": "job-working-est",
                "status": "working",
                "progress_percent": 35,
                "stage": "Scraping listings..."
            }
            se.JOB_CACHE["job-complete-act"] = {
                "id": "job-complete-act",
                "status": "ok",
                "progress_percent": 100,
                "stage": "Completed"
            }

        update1 = {
            "update_id": 701,
            "message": {
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001},
                "text": "/status job-working-est"
            }
        }
        self.handler.handle_update(update1)
        msg1 = self.mock_client.sent_messages[-1]["text"]
        self.assertIn("~35% (Estimated)", msg1)
        self.assertIn("WORKING", msg1)

        update2 = {
            "update_id": 702,
            "message": {
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001},
                "text": "/status job-complete-act"
            }
        }
        self.handler.handle_update(update2)
        msg2 = self.mock_client.sent_messages[-1]["text"]
        self.assertIn("100% (Complete)", msg2)
        self.assertIn("OK", msg2)

    @patch("scripts.scraper_engine.is_supabase_configured", return_value=True)
    @patch("scripts.scraper_engine.fetch_scrape_job_by_id")
    def test_status_supabase_fallback_when_not_in_memory_or_local(self, mock_fetch_job, mock_sb):
        mock_fetch_job.return_value = {
            "id": "res-uuid-999",
            "scraper_job_id": "job-cloud-999",
            "status": "ok",
            "stage": "Completed in cloud",
            "progress": 100,
            "query": "gyms in Denver",
            "location": "Denver, CO",
            "requested_limit": 10,
            "total_leads": 10
        }

        info = se.get_job_info("job-cloud-999")
        self.assertEqual(info.get("id"), "job-cloud-999")
        self.assertEqual(info.get("status"), "ok")
        self.assertEqual(info.get("stage"), "Completed in cloud")
        self.assertTrue(info.get("is_fallback"))

    def test_status_missing_job_returns_empty(self):
        info = se.get_job_info("job-non-existent-xyz")
        self.assertEqual(info, {})


if __name__ == "__main__":
    unittest.main()


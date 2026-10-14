#!/usr/bin/env python3
"""Targeted Test Suite for LeadMap Pro Telegram Natural Language Processing & Clarification.

Tests:
1. Zero-dependency natural language query parsing (count, keyword, city, depth bounds).
2. Missing parameter detection & conversational clarification prompting.
3. Multi-turn conversation state persistence, retrieval, and TTL expiry.
4. TelegramBotHandler natural language dispatching & interactive clarification flow.
5. Real counts verification & database receipt reporting.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import scripts.telegram_bot as tb


class MockTelegramClient(tb.TelegramClient):
    """Mock TelegramClient capturing sent messages without network calls."""

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


class TestTelegramNaturalLanguageParsing(unittest.TestCase):
    """Test natural language entity extraction."""

    def test_count_first_queries(self):
        res = tb.parse_natural_language_query("10 IT companies in Pune")
        self.assertEqual(res["status"], "complete")
        self.assertEqual(res["keyword"], "IT companies")
        self.assertEqual(res["city"], "Pune")
        self.assertEqual(res["count"], 10)

        res2 = tb.parse_natural_language_query("20 digital marketing agencies in Bangalore")
        self.assertEqual(res2["status"], "complete")
        self.assertEqual(res2["keyword"], "digital marketing agencies")
        self.assertEqual(res2["city"], "Bangalore")
        self.assertEqual(res2["count"], 20)

    def test_action_verb_with_count(self):
        res = tb.parse_natural_language_query("Find 50 dentists in Mumbai")
        self.assertEqual(res["status"], "complete")
        self.assertEqual(res["keyword"], "dentists")
        self.assertEqual(res["city"], "Mumbai")
        self.assertEqual(res["count"], 50)

        res2 = tb.parse_natural_language_query("Scrape 15 cafes in Panjim, Goa")
        self.assertEqual(res2["status"], "complete")
        self.assertEqual(res2["keyword"], "cafes")
        self.assertEqual(res2["city"], "Panjim, Goa")
        self.assertEqual(res2["count"], 15)

    def test_default_count_when_omitted(self):
        res = tb.parse_natural_language_query("Find cafes in Delhi")
        self.assertEqual(res["status"], "complete")
        self.assertEqual(res["keyword"], "cafes")
        self.assertEqual(res["city"], "Delhi")
        self.assertEqual(res["count"], 5)

        res2 = tb.parse_natural_language_query("lawyers in Chicago")
        self.assertEqual(res2["status"], "complete")
        self.assertEqual(res2["keyword"], "lawyers")
        self.assertEqual(res2["city"], "Chicago")
        self.assertEqual(res2["count"], 5)

    def test_suffix_count(self):
        res = tb.parse_natural_language_query("restaurants in Goa 25")
        self.assertEqual(res["status"], "complete")
        self.assertEqual(res["keyword"], "restaurants")
        self.assertEqual(res["city"], "Goa")
        self.assertEqual(res["count"], 25)

    def test_depth_flag_modifier(self):
        res = tb.parse_natural_language_query("dentists in Mumbai --depth 30")
        self.assertEqual(res["status"], "complete")
        self.assertEqual(res["keyword"], "dentists")
        self.assertEqual(res["city"], "Mumbai")
        self.assertEqual(res["count"], 30)

    def test_count_clamping_bounds(self):
        res_high = tb.parse_natural_language_query("Find 200 dentists in Mumbai")
        self.assertEqual(res_high["count"], 50)  # clamped to MAX_DEPTH 50

        res_zero = tb.parse_natural_language_query("0 cafes in Pune")
        self.assertEqual(res_zero["count"], 5)  # 0 or negative clamped to MIN/DEFAULT

    def test_missing_location_needs_clarification(self):
        res = tb.parse_natural_language_query("Find dentists")
        self.assertEqual(res["status"], "needs_clarification")
        self.assertEqual(res["missing_field"], "city")
        self.assertEqual(res["keyword"], "dentists")
        self.assertIn("Which city or location", res["prompt"])

    def test_missing_keyword_needs_clarification(self):
        res = tb.parse_natural_language_query("50 in Pune")
        self.assertEqual(res["status"], "needs_clarification")
        self.assertEqual(res["missing_field"], "keyword")
        self.assertEqual(res["city"], "Pune")
        self.assertEqual(res["count"], 50)


class TestConversationalClarificationState(unittest.TestCase):
    """Test state persistence and expiration for multi-turn Telegram dialogs."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.orig_state_file = tb.STATE_FILE
        tb.STATE_FILE = os.path.join(self.test_dir, "telegram_state.json")

    def tearDown(self):
        tb.STATE_FILE = self.orig_state_file
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_set_and_get_clarification(self):
        chat_id = 12345
        user_id = 99999
        pending = {"missing_field": "city", "keyword": "dentists", "count": 10}

        tb.set_pending_clarification(chat_id, user_id, pending)
        retrieved = tb.get_pending_clarification(chat_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["keyword"], "dentists")
        self.assertEqual(retrieved["count"], 10)

    def test_clear_clarification(self):
        chat_id = 12345
        user_id = 99999
        pending = {"missing_field": "city", "keyword": "dentists", "count": 10}

        tb.set_pending_clarification(chat_id, user_id, pending)
        tb.clear_pending_clarification(chat_id)
        self.assertIsNone(tb.get_pending_clarification(chat_id))

    def test_clarification_ttl_expiry(self):
        chat_id = 12345
        user_id = 99999
        pending = {"missing_field": "city", "keyword": "dentists", "count": 10}

        tb.set_pending_clarification(chat_id, user_id, pending)
        # Check with 0-second TTL
        self.assertIsNone(tb.get_pending_clarification(chat_id, max_age_seconds=0))


class TestTelegramBotHandlerNaturalLanguage(unittest.TestCase):
    """Test TelegramBotHandler end-to-end with natural language updates."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.orig_state_file = tb.STATE_FILE
        self.orig_updates_file = tb.PROCESSED_UPDATES_FILE
        self.orig_allowed = os.environ.get("TELEGRAM_ALLOWED_USER_IDS")

        tb.STATE_FILE = os.path.join(self.test_dir, "telegram_state.json")
        tb.PROCESSED_UPDATES_FILE = os.path.join(self.test_dir, "telegram_processed_updates.json")
        tb.PROCESSED_UPDATES_CACHE = set()
        os.environ["TELEGRAM_ALLOWED_USER_IDS"] = "1001"

        self.client = MockTelegramClient()
        self.handler = tb.TelegramBotHandler(self.client)

    def tearDown(self):
        tb.STATE_FILE = self.orig_state_file
        tb.PROCESSED_UPDATES_FILE = self.orig_updates_file
        if self.orig_allowed is not None:
            os.environ["TELEGRAM_ALLOWED_USER_IDS"] = self.orig_allowed
        else:
            os.environ.pop("TELEGRAM_ALLOWED_USER_IDS", None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("scripts.telegram_bot.check_scraper_health", return_value=True)
    @patch("scripts.telegram_bot.initiate_scrape_job")
    def test_natural_language_complete_query_triggers_scrape(self, mock_init, mock_health):
        mock_init.return_value = (True, "job_test_123", {"full_query": "IT companies in Pune"})

        update = {
            "update_id": 501,
            "message": {
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001},
                "text": "10 IT companies in Pune"
            }
        }
        self.handler.handle_update(update)

        # Verify bot sent confirmation
        texts = [m["text"] for m in self.client.sent_messages]
        self.assertTrue(any("Initiating scrape" in t for t in texts))
        self.assertTrue(any("job_test_123" in t for t in texts))
        mock_init.assert_called_once()
        params = mock_init.call_args[0][0]
        self.assertEqual(params["keyword"], "IT companies")
        self.assertEqual(params["city"], "Pune")
        self.assertEqual(params["depth"], 10)

    @patch("scripts.telegram_bot.check_scraper_health", return_value=True)
    @patch("scripts.telegram_bot.initiate_scrape_job")
    def test_multi_turn_clarification_flow(self, mock_init, mock_health):
        mock_init.return_value = (True, "job_test_clarified", {"full_query": "dentists in Mumbai"})

        # Step 1: User sends incomplete query without city
        update1 = {
            "update_id": 502,
            "message": {
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001},
                "text": "Find 50 dentists"
            }
        }
        self.handler.handle_update(update1)

        # Bot should prompt for location
        texts = [m["text"] for m in self.client.sent_messages]
        self.assertTrue(any("Which city or location" in t for t in texts))
        mock_init.assert_not_called()

        # Step 2: User replies with location
        update2 = {
            "update_id": 503,
            "message": {
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001},
                "text": "Mumbai"
            }
        }
        self.handler.handle_update(update2)

        # Bot should now initiate scrape
        texts2 = [m["text"] for m in self.client.sent_messages]
        self.assertTrue(any("job_test_clarified" in t for t in texts2))
        mock_init.assert_called_once()
        params = mock_init.call_args[0][0]
        self.assertEqual(params["keyword"], "dentists")
        self.assertEqual(params["city"], "Mumbai")
        self.assertEqual(params["depth"], 50)

    def test_unrecognized_message_shows_guidance(self):
        update = {
            "update_id": 504,
            "message": {
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001},
                "text": "what is the weather today?"
            }
        }
        self.handler.handle_update(update)

        texts = [m["text"] for m in self.client.sent_messages]
        self.assertTrue(any("I didn't recognize that search request" in t for t in texts))


class TestWhatsAppCleansingBridge(unittest.TestCase):
    """Test E.164 phone normalization and WhatsApp eligibility tagging."""

    def test_e164_formatting_various_formats(self):
        from scripts.cleanser import format_e164_phone, is_whatsapp_eligible

        # 10-digit Indian mobile
        self.assertEqual(format_e164_phone("9876543210", default_country="91"), "+919876543210")
        self.assertTrue(is_whatsapp_eligible("+919876543210"))

        # Formatted Indian phone with spaces/dashes
        self.assertEqual(format_e164_phone("+91 98765-43210"), "+919876543210")
        self.assertTrue(is_whatsapp_eligible("+919876543210"))

        # Leading zero format (09876543210)
        self.assertEqual(format_e164_phone("09876543210", default_country="91"), "+919876543210")

        # US/Canada standard phone
        self.assertEqual(format_e164_phone("+1 (555) 123-4567"), "+15551234567")
        self.assertTrue(is_whatsapp_eligible("+15551234567"))

        # Too short / invalid phone
        self.assertEqual(format_e164_phone("12345"), "")
        self.assertFalse(is_whatsapp_eligible("12345"))
        self.assertFalse(is_whatsapp_eligible(""))

    def test_clean_lead_attaches_whatsapp_fields(self):
        from scripts.cleanser import clean_lead

        lead_data = {
            "title": "Dr. Sharma Dental Clinic",
            "phone": "09876543210",
            "address": "MG Road, Pune",
            "category": "Dentist"
        }
        cleaned = clean_lead(lead_data)
        self.assertEqual(cleaned["whatsapp_phone_e164"], "+919876543210")
        self.assertEqual(cleaned["whatsapp_status"], "eligible")
        self.assertEqual(cleaned["consent_source"], "public_directory")

    def test_export_leads_whatsapp_format(self):
        from scripts.scraper_engine import export_leads

        leads = [
            {
                "title": "=Dangerous Name",
                "clean_phone": "+91 98765 43210",
                "whatsapp_phone_e164": "+919876543210",
                "category": "Dentist",
                "review_rating": 4.8,
                "address": "Pune, MH",
                "website": "https://dental.in",
                "whatsapp_status": "eligible",
                "consent_source": "public_directory"
            },
            {
                "title": "No Phone Clinic",
                "clean_phone": "",
                "whatsapp_phone_e164": "",
                "whatsapp_status": "invalid_phone"
            }
        ]
        csv_bytes, suffix, mime = export_leads(leads, fmt="whatsapp")
        self.assertEqual(suffix, "_whatsapp_portal.csv")
        self.assertEqual(mime, "text/csv")
        text = csv_bytes.decode("utf-8")
        # Formula injection should be escaped with leading quote
        self.assertIn("'=Dangerous Name", text)
        # Should include eligible phone number
        self.assertIn("+919876543210", text)
        # Lead without phone should be filtered out from WhatsApp outreach
        self.assertNotIn("No Phone Clinic", text)

    @patch("scripts.telegram_bot.get_job_info")
    def test_handle_whatsapp_command_exports_file(self, mock_info):
        mock_info.return_value = {
            "leads": [
                {
                    "title": "Pune Tech Hub",
                    "clean_phone": "+91 98765 43210",
                    "whatsapp_phone_e164": "+919876543210",
                    "category": "Software",
                    "review_rating": 4.9,
                    "address": "Hinjewadi, Pune",
                    "whatsapp_status": "eligible"
                }
            ]
        }
        client = MockTelegramClient()
        handler = tb.TelegramBotHandler(client)
        os.environ["TELEGRAM_ALLOWED_USER_IDS"] = "1001"

        update = {
            "update_id": 601,
            "message": {
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001},
                "text": "/whatsapp job_pune_123"
            }
        }
        handler.handle_update(update)

        self.assertEqual(len(client.sent_documents), 1)
        doc = client.sent_documents[0]
        self.assertIn("_whatsapp_portal.csv", doc["filename"])
        self.assertIn("WHATSAPP", doc["caption"].upper())



if __name__ == "__main__":
    unittest.main()



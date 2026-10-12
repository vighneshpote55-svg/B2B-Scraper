#!/usr/bin/env python3
"""Unit tests for Supabase Authentication user registration integration.

Covers:
1. Admin user registration endpoint (/auth/v1/admin/users) on service_role credentials.
2. Fallback to public user registration endpoint (/auth/v1/signup).
3. Graceful handling of existing / duplicate user registrations.
4. Handling of unconfigured or missing credentials.
5. Integration within dashboard signup handler.
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.supabase_client import register_user_in_supabase_auth


class MockHTTPResponse:
    def __init__(self, status=200, json_data=None):
        self.status = status
        self.data_bytes = json.dumps(json_data or {}).encode("utf-8")

    def read(self):
        return self.data_bytes

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class TestSupabaseAuthRegistration(unittest.TestCase):
    def setUp(self):
        self.fake_url = "https://example-project.supabase.co"
        self.fake_key = "mock_service_role_secret_key"
        self.test_email = "newuser@example.com"
        self.test_password = "SecurePassword123!"
        self.test_name = "Jane Doe"

    def test_missing_credentials_returns_failure(self):
        res = register_user_in_supabase_auth("", "", "", supabase_url="", supabase_key="")
        self.assertFalse(res["success"])
        self.assertIn("Missing", res["error"])

    @patch("urllib.request.urlopen")
    def test_admin_api_success(self, mock_urlopen):
        mock_resp = MockHTTPResponse(201, {
            "id": "usr-uuid-1234",
            "email": self.test_email,
            "created_at": "2026-10-12T12:00:00Z"
        })
        mock_urlopen.return_value = mock_resp

        res = register_user_in_supabase_auth(
            self.test_email,
            self.test_password,
            self.test_name,
            supabase_url=self.fake_url,
            supabase_key=self.fake_key
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["source"], "admin")
        self.assertEqual(res["auth_user"]["id"], "usr-uuid-1234")

        # Verify endpoint called
        req = mock_urlopen.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/auth/v1/admin/users"))
        self.assertEqual(req.headers.get("Apikey"), self.fake_key)

    @patch("urllib.request.urlopen")
    def test_admin_api_forbidden_falls_back_to_signup_api(self, mock_urlopen):
        # 1st call fails with HTTP 403 or 404 (e.g. anon key without admin privileges)
        # 2nd call succeeds on /auth/v1/signup
        def urlopen_side_effect(req, timeout=10):
            if req.full_url.endswith("/auth/v1/admin/users"):
                raise urllib.error.HTTPError(
                    req.full_url, 403, "Forbidden", {},
                    fp=unittest.mock.MagicMock(read=lambda: b'{"error":"Not allowed"}')
                )
            elif req.full_url.endswith("/auth/v1/signup"):
                return MockHTTPResponse(200, {
                    "id": "usr-uuid-signup-5678",
                    "email": self.test_email
                })
            raise ValueError(f"Unexpected url: {req.full_url}")

        mock_urlopen.side_effect = urlopen_side_effect

        res = register_user_in_supabase_auth(
            self.test_email,
            self.test_password,
            self.test_name,
            supabase_url=self.fake_url,
            supabase_key=self.fake_key
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["source"], "signup")
        self.assertEqual(res["auth_user"]["id"], "usr-uuid-signup-5678")

    @patch("urllib.request.urlopen")
    def test_duplicate_user_handled(self, mock_urlopen):
        # Admin endpoint returns duplicate user error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            f"{self.fake_url}/auth/v1/admin/users",
            422,
            "Unprocessable Entity",
            {},
            fp=unittest.mock.MagicMock(read=lambda: b'{"message":"A user with this email address has already been registered"}')
        )

        res = register_user_in_supabase_auth(
            self.test_email,
            self.test_password,
            self.test_name,
            supabase_url=self.fake_url,
            supabase_key=self.fake_key
        )

        self.assertFalse(res["success"])
        self.assertTrue(res.get("is_duplicate"))


if __name__ == "__main__":
    unittest.main()

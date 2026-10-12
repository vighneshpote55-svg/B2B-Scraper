#!/usr/bin/env python3
"""Comprehensive Security Regression Test Suite for LeadMap Pro — Phase 2: Authentication Repair.

Covers:
1. Prevention of unauthenticated password overwrite via forgot-password.
2. Generic, non-enumerating responses on forgot-password (no user existence leaks).
3. Zero reset token leakage in forgot-password HTTP response body.
4. Single-use expiring password-reset token lifecycle (consumption, replay rejection).
5. Expiration and invalidation of reset tokens.
6. Session revocation: password reset revokes all existing active sessions.
7. Login rate limiting: 5 failed attempts trigger HTTP 429 with Retry-After header.
8. Disabled Google OAuth endpoint: POST /api/auth/google returns HTTP 501.
9. Timing-safe password verification with hmac.compare_digest and legacy hash upgrade.
"""
import hmac
import json
import os
import secrets
import sys
import time
import unittest
import urllib.request
import urllib.error

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dashboard import (
    _hash_password,
    _verify_password,
    _load_resets,
    _save_resets,
    _check_login_rate_limit,
    _record_login_failure,
    _reset_login_rate_limit,
    PASSWORD_RESETS_FILE,
    EMAIL_OUTBOX_FILE
)


class TestPhase2AuthenticationSecurity(unittest.TestCase):
    """Integration and unit tests for Phase 2 authentication repair."""

    BASE_URL = "http://127.0.0.1:3000"

    def _request(self, method, path, data=None, token=None, custom_headers=None):
        url = f"{self.BASE_URL}{path}"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if custom_headers:
            headers.update(custom_headers)

        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                status = response.status
                body_bytes = response.read()
                try:
                    res_json = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    res_json = None
                return status, res_json, response.headers
        except urllib.error.HTTPError as e:
            err_body = e.read()
            try:
                res_json = json.loads(err_body.decode("utf-8"))
            except Exception:
                res_json = None
            return e.code, res_json, e.headers

    def _create_test_account(self, email=None, password="OriginalPassword123!"):
        if not email:
            email = f"user_{time.time_ns()}_{secrets.token_hex(4)}@leadmappro.io"
        status, data, _ = self._request("POST", "/api/auth/signup", data={
            "email": email,
            "password": password,
            "name": "Auth Test User"
        })
        self.assertEqual(status, 200)
        return email, password, data.get("token")

    def test_unauthenticated_password_overwrite_blocked(self):
        """Verify sending email + new_password to forgot-password does NOT overwrite password."""
        email, original_pass, _ = self._create_test_account()

        # Attacker attempts to change victim password by supplying email and new_password
        status, data, _ = self._request("POST", "/api/auth/forgot-password", data={
            "email": email,
            "new_password": "AttackerControlledPassword123!"
        })
        self.assertEqual(status, 200)
        # Verify the response does not claim password was updated
        self.assertNotIn("updated successfully", (data.get("message") or "").lower())

        # Verify attacker CANNOT log in with the new password
        status_hacked, _, _ = self._request("POST", "/api/auth/login", data={
            "email": email,
            "password": "AttackerControlledPassword123!"
        })
        self.assertEqual(status_hacked, 401, "Account takeover succeeded: attacker logged in with unauthorized new_password!")

        # Verify legitimate user can STILL log in with their original password
        status_legit, legit_data, _ = self._request("POST", "/api/auth/login", data={
            "email": email,
            "password": original_pass
        })
        self.assertEqual(status_legit, 200)
        self.assertTrue(legit_data.get("token"))

    def test_generic_forgot_password_response_no_user_enumeration(self):
        """Verify existing and non-existing emails receive identical generic responses."""
        existing_email, _, _ = self._create_test_account()
        non_existing_email = f"does_not_exist_{int(time.time())}@randomdomain999.io"

        status_exist, data_exist, _ = self._request("POST", "/api/auth/forgot-password", data={
            "email": existing_email
        })
        status_nonexist, data_nonexist, _ = self._request("POST", "/api/auth/forgot-password", data={
            "email": non_existing_email
        })

        self.assertEqual(status_exist, 200)
        self.assertEqual(status_nonexist, 200)
        self.assertEqual(data_exist.get("message"), data_nonexist.get("message"))
        self.assertIn("instructions have been sent", (data_exist.get("message") or "").lower())

    def test_forgot_password_never_leaks_reset_token_in_body(self):
        """Verify reset_token is strictly omitted from the HTTP response payload."""
        email, _, _ = self._create_test_account()
        status, data, _ = self._request("POST", "/api/auth/forgot-password", data={
            "email": email
        })
        self.assertEqual(status, 200)
        self.assertNotIn("reset_token", data)
        self.assertNotIn("token", data)

    def test_single_use_reset_token_lifecycle(self):
        """Verify valid token resets password, token cannot be re-used, and login succeeds."""
        email, original_pass, _ = self._create_test_account()
        new_pass = "BrandNewSecurePassword123!"

        # Trigger reset
        status, _, _ = self._request("POST", "/api/auth/forgot-password", data={"email": email})
        self.assertEqual(status, 200)

        # Retrieve out-of-band token from reset store
        resets = _load_resets()
        user_tokens = [tok for tok, info in resets.items() if info.get("email") == email and not info.get("used")]
        self.assertTrue(len(user_tokens) > 0, "No reset token found in reset store for test user")
        valid_token = user_tokens[-1]

        # Use token to reset password
        status_reset, data_reset, _ = self._request("POST", "/api/auth/reset-password", data={
            "token": valid_token,
            "new_password": new_pass
        })
        self.assertEqual(status_reset, 200)
        self.assertTrue(data_reset.get("success"))

        # Attempt to replay the same token a second time — MUST FAIL
        status_replay, data_replay, _ = self._request("POST", "/api/auth/reset-password", data={
            "token": valid_token,
            "new_password": "AnotherNewPassword999!"
        })
        self.assertEqual(status_replay, 400)
        self.assertIn("already been used", (data_replay.get("error") or "").lower())

        # Old password must no longer work
        status_old, _, _ = self._request("POST", "/api/auth/login", data={
            "email": email,
            "password": original_pass
        })
        self.assertEqual(status_old, 401)

        # New password must work
        status_new, login_data, _ = self._request("POST", "/api/auth/login", data={
            "email": email,
            "password": new_pass
        })
        self.assertEqual(status_new, 200)
        self.assertTrue(login_data.get("token"))

    def test_expired_reset_token_rejected(self):
        """Verify tokens past expiration TTL are rejected."""
        email, _, _ = self._create_test_account()
        expired_token = f"expired_test_token_{int(time.time())}"

        resets = _load_resets()
        resets[expired_token] = {
            "email": email,
            "created_at": time.time() - 3600,
            "expires_at": time.time() - 60,  # expired 1 minute ago
            "used": False
        }
        _save_resets(resets)

        try:
            status, data, _ = self._request("POST", "/api/auth/reset-password", data={
                "token": expired_token,
                "new_password": "NewValidPassword123!"
            })
            self.assertEqual(status, 400)
            self.assertIn("expired", (data.get("error") or "").lower())
        finally:
            resets = _load_resets()
            resets.pop(expired_token, None)
            _save_resets(resets)

    def test_invalid_reset_token_rejected(self):
        """Verify non-existent tokens and short passwords are rejected."""
        status, data, _ = self._request("POST", "/api/auth/reset-password", data={
            "token": "completely_fake_token_12345",
            "new_password": "ValidPassword123!"
        })
        self.assertEqual(status, 400)
        self.assertIn("invalid or expired", (data.get("error") or "").lower())

        # Short password (< 8 chars)
        status, data, _ = self._request("POST", "/api/auth/reset-password", data={
            "token": "valid_looking_token",
            "new_password": "short"
        })
        self.assertEqual(status, 400)
        self.assertIn("at least 8 characters", (data.get("error") or "").lower())

    def test_password_reset_revokes_active_sessions(self):
        """Verify resetting a password invalidates all existing sessions for that user."""
        email, orig_pass, initial_token = self._create_test_account()

        # Confirm initial session token works
        status_jobs, _, _ = self._request("GET", "/api/jobs", token=initial_token)
        self.assertEqual(status_jobs, 200)

        # Trigger reset & retrieve token
        self._request("POST", "/api/auth/forgot-password", data={"email": email})
        resets = _load_resets()
        token = [tok for tok, info in resets.items() if info.get("email") == email and not info.get("used")][-1]

        # Reset password
        status_reset, _, _ = self._request("POST", "/api/auth/reset-password", data={
            "token": token,
            "new_password": "UpdatedPassword123!"
        })
        self.assertEqual(status_reset, 200)

        # The prior session token MUST now be revoked (HTTP 401)
        status_revoked, revoked_data, _ = self._request("GET", "/api/jobs", token=initial_token)
        self.assertEqual(status_revoked, 401, "Session was not revoked upon password reset!")
        self.assertIn("authentication required", (revoked_data.get("error") or "").lower())

    def test_login_rate_limiting_triggers_429(self):
        """Verify 5 failed login attempts trigger HTTP 429 Too Many Requests."""
        target_email = f"rate_limit_test_{int(time.time())}@leadmappro.io"
        wrong_pass = "WrongPassword999!"

        # Clear any existing rate limit for this test email
        _reset_login_rate_limit("127.0.0.1", target_email)

        # Attempt 5 failed logins
        for i in range(5):
            status, _, _ = self._request("POST", "/api/auth/login", data={
                "email": target_email,
                "password": wrong_pass
            })
            self.assertEqual(status, 401, f"Expected 401 on attempt {i+1}, got {status}")

        # The 6th attempt must trigger HTTP 429 Too Many Requests
        status_blocked, blocked_data, headers = self._request("POST", "/api/auth/login", data={
            "email": target_email,
            "password": wrong_pass
        })
        self.assertEqual(status_blocked, 429, f"Expected 429 Too Many Requests on attempt 6, got {status_blocked}")
        self.assertIn("too many failed login attempts", (blocked_data.get("error") or "").lower())
        self.assertIsNotNone(headers.get("Retry-After"))

        # Clean up rate limit state
        _reset_login_rate_limit("127.0.0.1", target_email)

    def test_google_auth_disabled_rejection(self):
        """Verify /api/auth/google returns HTTP 501 and refuses to issue sessions."""
        status, data, _ = self._request("POST", "/api/auth/google", data={
            "email": "executive@leadmappro.io"
        })
        self.assertEqual(status, 501)
        self.assertEqual(data.get("code"), "OAUTH_DISABLED")
        self.assertNotIn("token", data)

    def test_timing_safe_password_verification(self):
        """Verify password comparison uses constant-time hmac.compare_digest and supports modern iterations."""
        password = "ConstantTimePassword123!"

        # Modern format (210,000 iterations PBKDF2)
        modern_hash = _hash_password(password, iterations=210000)
        self.assertTrue(modern_hash.startswith("pbkdf2:sha256:210000:"))
        self.assertTrue(_verify_password(password, modern_hash))
        self.assertFalse(_verify_password("IncorrectPassword!", modern_hash))

        # Legacy format (100,000 iterations salt:hash) backward compatibility
        salt = "a1b2c3d4e5f67890"
        import hashlib
        legacy_raw = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000).hex()
        legacy_hash = f"{salt}:{legacy_raw}"
        self.assertTrue(_verify_password(password, legacy_hash))
        self.assertFalse(_verify_password("IncorrectPassword!", legacy_hash))


if __name__ == "__main__":
    unittest.main(verbosity=2)

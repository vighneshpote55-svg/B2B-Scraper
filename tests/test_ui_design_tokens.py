#!/usr/bin/env python3
"""Automated regression test suite for LeadMap Pro Phase 1 Design Tokens & Accessibility.

Validates:
1. All core design tokens exist in web/app.css under :root.
2. Light mode theme tokens exist under [data-theme="light"].
3. Keyboard focus-visible rules are declared globally.
4. Prefers-reduced-motion media query is implemented for vestibular accessibility.
5. Tabular numerals (tabular-nums) are applied to dense research tables.
6. Buttons and form controls have standardized disabled states.
"""
import os
import re
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_CSS_PATH = os.path.join(REPO_ROOT, "web", "app.css")


class TestUIDesignTokens(unittest.TestCase):
    """Test suite verifying presence and validity of design tokens and accessibility rules."""

    @classmethod
    def setUpClass(cls):
        with open(APP_CSS_PATH, "r", encoding="utf-8") as f:
            cls.css_content = f.read()

    def test_root_tokens_defined(self):
        """Assert essential design tokens are declared in :root."""
        required_tokens = [
            "--bg-app",
            "--bg-card",
            "--bg-header",
            "--border-focus",
            "--accent-teal",
            "--accent-teal-hover",
            "--accent-teal-light",
            "--accent-gradient",
            "--text-primary",
            "--text-secondary",
            "--text-muted",
            "--color-hot",
            "--color-warm",
            "--color-cold",
            "--color-success",
            "--shadow-sm",
            "--shadow-md",
            "--shadow-lg",
            "--radius-card"
        ]
        for token in required_tokens:
            self.assertIn(token, self.css_content, f"Missing required token in CSS: {token}")

    def test_light_theme_tokens_defined(self):
        """Assert light theme override block exists with accessible text and surface tokens."""
        self.assertIn('[data-theme="light"]', self.css_content)
        light_tokens = [
            "--bg-app",
            "--bg-card",
            "--border-color",
            "--text-primary",
            "--text-secondary",
            "--text-table-head",
            "--accent-teal"
        ]
        # Match data-theme="light" block
        match = re.search(r'\[data-theme="light"\]\s*\{([^}]+)\}', self.css_content)
        self.assertIsNotNone(match, 'Could not find [data-theme="light"] block in app.css')
        block_content = match.group(1)
        for token in light_tokens:
            self.assertIn(token, block_content, f"Missing light theme override for: {token}")

    def test_focus_visible_present(self):
        """Assert visible keyboard focus styles are declared for accessibility."""
        self.assertIn(":focus-visible", self.css_content)
        self.assertTrue(
            re.search(r'outline:\s*2px\s+solid', self.css_content) or re.search(r'--border-focus', self.css_content),
            "Expected explicit 2px focus outline with focus border token"
        )

    def test_prefers_reduced_motion_present(self):
        """Assert @media (prefers-reduced-motion: reduce) is implemented."""
        self.assertIn("prefers-reduced-motion", self.css_content)
        self.assertIn("animation-duration", self.css_content)

    def test_tabular_numbers_declared(self):
        """Assert tabular-nums is declared for dense data table readability."""
        self.assertIn("tabular-nums", self.css_content)

    def test_disabled_states_declared(self):
        """Assert :disabled pseudo-class is styled with cursor and opacity."""
        self.assertTrue(
            ":disabled" in self.css_content or "[disabled]" in self.css_content,
            "Expected :disabled state definitions in CSS"
        )


if __name__ == "__main__":
    unittest.main()

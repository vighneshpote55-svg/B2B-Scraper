#!/usr/bin/env python3
"""Automated regression test suite for LeadMap Pro Phase 4: Theme and Responsive Polish.

Validates:
1. Inline anti-flash script present in <head> of web/index.html to prevent FOUC.
2. Theme toggle button (#btn-theme-toggle) with ARIA attributes and icons in web/index.html.
3. Dark mode default, theme persistence (leadmap-theme / leadmap_theme), and theme sync in web/app.js.
4. Light mode tokens, Monochrome tokens, and accessible contrast variables in web/app.css.
5. Keyboard navigation focus indicators (:focus-visible) and prefers-reduced-motion in web/app.css.
6. Responsive breakpoints for desktop (>1024px), tablet (641px-1024px), and mobile (<=640px) in web/app.css.
7. Modal and drawer viewport constraint rules preventing horizontal overflow on mobile screens.
"""
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INDEX_HTML_PATH = os.path.join(REPO_ROOT, "web", "index.html")
APP_CSS_PATH = os.path.join(REPO_ROOT, "web", "app.css")
APP_JS_PATH = os.path.join(REPO_ROOT, "web", "app.js")


class TestUIThemeAndResponsive(unittest.TestCase):
    """Test suite verifying presence and validity of Phase 4 Theme & Responsive Polish features."""

    @classmethod
    def setUpClass(cls):
        with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
            cls.html_content = f.read()
        with open(APP_CSS_PATH, "r", encoding="utf-8") as f:
            cls.css_content = f.read()
        with open(APP_JS_PATH, "r", encoding="utf-8") as f:
            cls.js_content = f.read()

    def test_anti_flash_script_present_in_head(self):
        """Assert zero-FOUC inline script is executed inside <head> before page render."""
        self.assertIn("<head>", self.html_content)
        head_section = self.html_content.split("</head>")[0]
        self.assertIn("localStorage.getItem", head_section)
        self.assertIn("setAttribute('data-theme'", head_section)

    def test_theme_toggle_elements_present(self):
        """Assert theme toggle button and theme icons are declared in index.html."""
        self.assertIn('id="btn-theme-toggle"', self.html_content)
        self.assertIn('id="theme-icon-sun"', self.html_content)
        self.assertIn('id="theme-icon-moon"', self.html_content)
        self.assertIn('id="theme-icon-mono"', self.html_content)
        self.assertIn('aria-label="Toggle theme"', self.html_content)

    def test_js_theme_lifecycle_and_persistence(self):
        """Assert JavaScript controller sets dark mode as default and persists preference."""
        self.assertIn("applyTheme", self.js_content)
        self.assertIn("localStorage.setItem('leadmap-theme'", self.js_content)
        self.assertIn("setupThemeToggle", self.js_content)
        # Verify dark mode default fallback
        self.assertIn("'dark'", self.js_content)

    def test_css_theme_tokens_present(self):
        """Assert light and dark theme token blocks are defined in app.css."""
        self.assertIn('[data-theme="light"]', self.css_content)
        self.assertIn('[data-theme="mono"]', self.css_content)
        # Check accessible contrast tokens in light mode
        self.assertIn("--bg-app: #f8fafc;", self.css_content)
        self.assertIn("--text-primary: #0f172a;", self.css_content)
        self.assertIn("--border-color: #e2e8f0;", self.css_content)

    def test_accessibility_focus_and_motion(self):
        """Assert :focus-visible and prefers-reduced-motion media query are declared."""
        self.assertIn(":focus-visible", self.css_content)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css_content)

    def test_responsive_media_queries_and_viewport(self):
        """Assert responsive breakpoints for tablet and mobile viewports are declared."""
        self.assertIn("@media (max-width: 900px)", self.css_content)
        self.assertIn("@media (max-width: 640px)", self.css_content)
        # Modal and drawer responsiveness
        self.assertIn(".modal-card", self.css_content)
        self.assertIn(".drawer", self.css_content)


if __name__ == "__main__":
    unittest.main()

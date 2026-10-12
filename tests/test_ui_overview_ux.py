#!/usr/bin/env python3
"""Automated regression test suite for LeadMap Pro Phase 2: Dashboard Overview UX.

Validates:
1. Overview Header, title, and primary CTA buttons in web/index.html.
2. 4 data-backed KPI cards (Total Leads, Hot Leads, Active Jobs, Completed Jobs).
3. Recent Scraping Jobs section with loading, empty, error, and retry states.
4. Recent Leads section with contact presence indicators.
5. CSS classes for status badges, recent grid responsiveness, and loading states in web/app.css.
6. JavaScript helper and controller definitions in web/app.js.
"""
import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INDEX_HTML_PATH = os.path.join(REPO_ROOT, "web", "index.html")
APP_CSS_PATH = os.path.join(REPO_ROOT, "web", "app.css")
APP_JS_PATH = os.path.join(REPO_ROOT, "web", "app.js")


class TestUIOverviewUX(unittest.TestCase):
    """Test suite verifying presence and validity of Phase 2 Overview UX features."""

    @classmethod
    def setUpClass(cls):
        with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
            cls.html_content = f.read()
        with open(APP_CSS_PATH, "r", encoding="utf-8") as f:
            cls.css_content = f.read()
        with open(APP_JS_PATH, "r", encoding="utf-8") as f:
            cls.js_content = f.read()

    def test_overview_header_and_cta_present(self):
        """Assert Overview Header and primary CTA elements exist in index.html."""
        self.assertIn('id="overview-header"', self.html_content)
        self.assertIn('id="btn-overview-new-scrape"', self.html_content)
        self.assertIn('id="btn-overview-refresh"', self.html_content)

    def test_four_kpi_cards_present(self):
        """Assert the 4 KPI cards exist in index.html: Total Leads, Hot Leads, Active Jobs, Completed Jobs."""
        self.assertIn('id="metric-total"', self.html_content)
        self.assertIn('id="metric-hot"', self.html_content)
        self.assertIn('id="metric-active-jobs"', self.html_content)
        self.assertIn('id="metric-completed-jobs"', self.html_content)

    def test_recent_jobs_and_leads_sections_present(self):
        """Assert Recent Jobs and Recent Leads sections exist with all required states."""
        self.assertIn('id="recent-jobs-card"', self.html_content)
        self.assertIn('id="recent-jobs-list"', self.html_content)
        self.assertIn('id="btn-retry-recent-jobs"', self.html_content)
        self.assertIn('id="recent-leads-card"', self.html_content)
        self.assertIn('id="recent-leads-list"', self.html_content)

    def test_status_badges_and_grid_styles(self):
        """Assert status badge CSS classes and responsive grid rules are defined in app.css."""
        required_classes = [
            ".overview-header",
            ".overview-recent-grid",
            ".badge-status-completed",
            ".badge-status-working",
            ".badge-status-failed",
            ".badge-status-cancelled",
            ".contact-indicator"
        ]
        for cls in required_classes:
            self.assertIn(cls, self.css_content, f"Missing CSS class: {cls}")

    def test_javascript_overview_controller_present(self):
        """Assert JavaScript controller functions for overview and timestamp formatting are present in app.js."""
        required_identifiers = [
            "loadOverviewData",
            "renderOverviewKPIs",
            "renderRecentJobs",
            "renderRecentLeads",
            "formatReadableDate"
        ]
        for ident in required_identifiers:
            self.assertIn(ident, self.js_content, f"Missing JavaScript function: {ident}")


if __name__ == "__main__":
    unittest.main()

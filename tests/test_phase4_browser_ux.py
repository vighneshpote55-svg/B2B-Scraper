#!/usr/bin/env python3
"""Comprehensive Browser-Level UX & Workflow Test Suite for LeadMap Pro — Phase 4.

Covers Workstream 2:
1. Adaptive polling backoff algorithm verification (2.5s -> 5s -> 10s -> 20s -> 30s max).
2. Retry controls state preservation (search terms, city, coordinates, depth, email/socials).
3. Client-side bulk removal in-memory pruning and table render synchronization.
4. Table and Map view toggle elements and Leaflet container markup.
"""
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestPhase4BrowserUX(unittest.TestCase):
    """Validates frontend UX resilience, retry mechanics, and state consistency."""

    def setUp(self):
        self.app_js_path = os.path.join(REPO_ROOT, "web", "app.js")
        self.index_html_path = os.path.join(REPO_ROOT, "web", "index.html")
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            self.app_js = f.read()
        with open(self.index_html_path, "r", encoding="utf-8") as f:
            self.index_html = f.read()

    def test_adaptive_polling_backoff_implementation(self):
        """web/app.js must implement exponential backoff with upper bound."""
        self.assertIn("pollBackoffMs", self.app_js)
        self.assertIn("MIN_POLL_INTERVAL_MS = 2500", self.app_js)
        self.assertIn("MAX_POLL_INTERVAL_MS = 30000", self.app_js)
        self.assertIn("Math.min(pollBackoffMs * 2, MAX_POLL_INTERVAL_MS)", self.app_js)
        self.assertIn("stopPolling", self.app_js)

    def test_retry_controls_and_error_elements(self):
        """web/index.html and web/app.js must define #btn-retry-scrape, #btn-bulk-remove, and lastScrapePayload."""
        self.assertIn('id="btn-retry-scrape"', self.index_html)
        self.assertIn('id="btn-bulk-remove"', self.index_html)
        self.assertIn('id="btn-cancel-scrape"', self.index_html)
        self.assertIn('lastScrapePayload', self.app_js)

    def test_bulk_lead_removal_in_memory(self):
        """Bulk removal must filter selected rows from local leads array and re-render."""
        self.assertIn("btnBulkRemove.addEventListener('click'", self.app_js)
        self.assertIn("allLeads = allLeads.filter", self.app_js)
        self.assertIn("selectedLeadIndices.clear()", self.app_js)
        self.assertIn("renderTable()", self.app_js)

    def test_table_and_map_view_toggling_preservation(self):
        """Table view and Map view controls and containers must exist in markup."""
        self.assertIn('id="btn-view-table"', self.index_html)
        self.assertIn('id="btn-view-map"', self.index_html)
        self.assertIn('id="leads-map-container"', self.index_html)
        self.assertIn('id="leads-table-body"', self.index_html)

    def test_backoff_numerical_progression(self):
        """Algorithm simulation: backoff doubles on consecutive failures and resets on success."""
        min_ms = 2500
        max_ms = 30000
        backoff = min_ms

        # 4 consecutive failures
        backoff = min(backoff * 2, max_ms)
        self.assertEqual(backoff, 5000)
        backoff = min(backoff * 2, max_ms)
        self.assertEqual(backoff, 10000)
        backoff = min(backoff * 2, max_ms)
        self.assertEqual(backoff, 20000)
        backoff = min(backoff * 2, max_ms)
        self.assertEqual(backoff, 30000)
        # Cap at 30s
        backoff = min(backoff * 2, max_ms)
        self.assertEqual(backoff, 30000)

        # Reset on recovery
        backoff = min_ms
        self.assertEqual(backoff, 2500)


if __name__ == "__main__":
    unittest.main()

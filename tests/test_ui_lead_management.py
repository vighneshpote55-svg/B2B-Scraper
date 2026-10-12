#!/usr/bin/env python3
"""Automated regression test suite for LeadMap Pro Phase 3: Lead Management UX.

Validates:
1. Presence of advanced filter bar elements in web/index.html (category, location, contacts, tier, reset).
2. Review count sortable header in web/index.html.
3. Lead dossier source & geocoding metadata card in web/index.html.
4. Filter bar, sort indicators, and table truncation CSS styles in web/app.css.
5. JavaScript functions for dynamic filter population, multi-field search, sorting, and CSV formula defense in web/app.js.
"""
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INDEX_HTML_PATH = os.path.join(REPO_ROOT, "web", "index.html")
APP_CSS_PATH = os.path.join(REPO_ROOT, "web", "app.css")
APP_JS_PATH = os.path.join(REPO_ROOT, "web", "app.js")


class TestUILeadManagement(unittest.TestCase):
    """Test suite verifying presence and validity of Phase 3 Lead Management UX features."""

    @classmethod
    def setUpClass(cls):
        with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
            cls.html_content = f.read()
        with open(APP_CSS_PATH, "r", encoding="utf-8") as f:
            cls.css_content = f.read()
        with open(APP_JS_PATH, "r", encoding="utf-8") as f:
            cls.js_content = f.read()

    def test_filter_bar_elements_present(self):
        """Assert filter bar and all filter dropdowns exist in index.html."""
        self.assertIn('id="leads-filter-bar"', self.html_content)
        self.assertIn('id="filter-category"', self.html_content)
        self.assertIn('id="filter-location"', self.html_content)
        self.assertIn('id="filter-contact-type"', self.html_content)
        self.assertIn('id="filter-tier-select"', self.html_content)
        self.assertIn('id="btn-reset-filters"', self.html_content)

    def test_sortable_columns_present(self):
        """Assert table headers declare sort capabilities including reviews."""
        self.assertIn('data-sort="score"', self.html_content)
        self.assertIn('data-sort="title"', self.html_content)
        self.assertIn('data-sort="rating"', self.html_content)
        self.assertIn('data-sort="reviews"', self.html_content)

    def test_dossier_source_metadata_present(self):
        """Assert dossier drawer includes source and geocoding metadata fields."""
        self.assertIn('id="dossier-source-card"', self.html_content)
        self.assertIn('id="dossier-coords"', self.html_content)
        self.assertIn('id="dossier-maps-link"', self.html_content)
        self.assertIn('id="dossier-timestamp"', self.html_content)

    def test_css_filter_bar_and_table_styles(self):
        """Assert CSS rules for filter bar and sort states are defined in app.css."""
        required_classes = [
            ".leads-filter-bar",
            ".filter-group",
            ".filter-select",
            ".btn-reset-filters",
            ".th-sort-active",
            ".address-cell"
        ]
        for cls in required_classes:
            self.assertIn(cls, self.css_content, f"Missing CSS class: {cls}")

    def test_js_filter_and_formula_defense_present(self):
        """Assert JavaScript controller has dynamic filter population and formula injection defense."""
        required_identifiers = [
            "populateFilterDropdowns",
            "filterCategory",
            "filterLocation",
            "filterContactType",
            "filterTierSelect",
            "getLeadLocation"
        ]
        for ident in required_identifiers:
            self.assertIn(ident, self.js_content, f"Missing JavaScript identifier: {ident}")

        # Assert CSV formula injection pattern is defended against
        self.assertIn("/^[=+\\-@\\t\\r]/.test(val)", self.js_content)

    def test_safe_contact_actions_and_sanitization(self):
        """Assert phone, email, and website actions adhere to security and honesty guidelines."""
        # Must sanitize external URLs and use noopener noreferrer
        self.assertIn('target="_blank"', self.js_content)
        self.assertIn('rel="noopener noreferrer"', self.js_content)
        self.assertIn('tel:', self.js_content)
        self.assertIn('mailto:', self.js_content)

    def test_multi_field_search_and_sorting_logic(self):
        """Assert search encompasses name, city, phone, email, category, address, website and sorting includes reviews."""
        self.assertIn("sortField === 'reviews'", self.js_content)
        self.assertIn("Number(a.review_count || 0)", self.js_content)
        self.assertIn("title.includes(q)", self.js_content)
        self.assertIn("emails.includes(q)", self.js_content)
        self.assertIn("phone.includes(q)", self.js_content)
        self.assertIn("website.includes(q)", self.js_content)
        self.assertIn("city.includes(q)", self.js_content)

    def test_pagination_and_empty_states(self):
        """Assert sliced pagination state and responsive empty states are present."""
        self.assertIn('selectPageSize', self.js_content)
        self.assertIn('tablePagination', self.js_content)
        self.assertIn('No Matching Leads', self.js_content)
        self.assertIn('No New Leads in This Run', self.js_content)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Phase 6: Lead Quality, Deduplication, and Regression Tests.

Validates:
1. Multi-criteria deduplication without false-positive discarding of distinct businesses sharing a phone or domain.
2. Country-aware international phone normalization without telecom verification claims.
3. Lead scoring boundaries (0, 35, 40, 65, 70, 100) and rating/review transitions.
4. Junk email prefix/domain/asset discarding and domain-matching prioritization.
5. Missing fields and malformed type robustness in lead cleansing.
6. Social profile extraction, handle filtering, and tracking query parameter stripping.
7. Repeat scrape filtering and accurate duplicate classification.
8. CSV/CRM exports and formula injection protection.
9. Dashboard job history, stop controls, and dark/light/monochrome UI assets.
"""

import csv
import io
import json
import os
import re
import sys
import unittest
from unittest.mock import patch, MagicMock

# Include project root
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from scripts.cleanser import (
    clean_phone,
    clean_emails,
    clean_lead,
    score_lead,
    clean_social_url,
    find_socials_in_html,
    deduplicate_leads,
    filter_previously_seen_leads,
    normalize_business_name,
    normalize_address,
    sanitize_csv_cell,
    sanitize_csv_row,
    GENERIC_DOMAINS
)


class TestIntelligentDeduplication(unittest.TestCase):
    """Test multi-criteria deduplication and protection against false discards."""

    def test_distinct_businesses_sharing_phone_are_preserved(self):
        """Two different businesses in the same building/medical plaza sharing a phone must NOT be discarded."""
        leads = [
            {
                "title": "Downtown Dental Clinic",
                "phone": "+1 512-555-0100",
                "clean_phone": "+1 (512) 555-0100",
                "website": "https://downtowndental.com",
                "domain": "downtowndental.com",
                "address": "100 Congress Ave Ste 100, Austin, TX"
            },
            {
                "title": "Austin Eye Specialists",
                "phone": "+1 512-555-0100",
                "clean_phone": "+1 (512) 555-0100",
                "website": "https://austineyes.com",
                "domain": "austineyes.com",
                "address": "100 Congress Ave Ste 200, Austin, TX"
            }
        ]
        unique = deduplicate_leads(leads)
        self.assertEqual(len(unique), 2, "Distinct businesses sharing a phone number must both be preserved")

    def test_distinct_businesses_sharing_domain_are_preserved(self):
        """Two different businesses sharing a domain (e.g. mall directory, agency portal, or generic domain) must NOT be discarded."""
        leads = [
            {
                "title": "Artisan Coffee Bar",
                "phone": "+1 512-555-0201",
                "clean_phone": "+1 (512) 555-0201",
                "website": "https://austin-arcade.com/coffee",
                "domain": "austin-arcade.com",
                "address": "200 South Congress Ave, Austin, TX"
            },
            {
                "title": "Vintage Vinyl Records",
                "phone": "+1 512-555-0202",
                "clean_phone": "+1 (512) 555-0202",
                "website": "https://austin-arcade.com/records",
                "domain": "austin-arcade.com",
                "address": "200 South Congress Ave, Austin, TX"
            }
        ]
        unique = deduplicate_leads(leads)
        self.assertEqual(len(unique), 2, "Distinct businesses sharing a domain must both be preserved")

    def test_exact_place_id_duplicate_discarded(self):
        """Duplicate leads with identical Google Place IDs must be deduplicated."""
        leads = [
            {"title": "Cafe Del Sol", "place_id": "ChIJN1t_tDeuEmsRUsoyG83frY4", "address": "123 Ocean Blvd"},
            {"title": "Cafe Del Sol (Repeat)", "place_id": "ChIJN1t_tDeuEmsRUsoyG83frY4", "address": "123 Ocean Blvd"}
        ]
        unique = deduplicate_leads(leads)
        self.assertEqual(len(unique), 1)

    def test_exact_cid_duplicate_discarded(self):
        """Duplicate leads with identical non-zero Google CIDs must be deduplicated."""
        leads = [
            {"title": "Metro Law Group", "cid": "1029384756", "address": "500 5th Ave"},
            {"title": "Metro Law Group LLC", "cid": "1029384756", "address": "500 5th Ave"}
        ]
        unique = deduplicate_leads(leads)
        self.assertEqual(len(unique), 1)

    def test_normalized_title_and_address_duplicate_discarded(self):
        """Slight punctuation and legal suffix variations at the same address must be deduplicated."""
        leads = [
            {"title": "Apex Fitness, LLC.", "address": "404 Main St., Ste. 10"},
            {"title": "Apex Fitness", "address": "404 Main Street, Suite 10"}
        ]
        unique = deduplicate_leads(leads)
        self.assertEqual(len(unique), 1)

    def test_corroborated_title_and_phone_duplicate_discarded(self):
        """Matching title + matching phone indicates a duplicate listing."""
        leads = [
            {"title": "Barton Creek Bakery", "clean_phone": "+15125550300", "address": "Site A"},
            {"title": "Barton Creek Bakery", "clean_phone": "+15125550300", "address": "Site B"}
        ]
        unique = deduplicate_leads(leads)
        self.assertEqual(len(unique), 1)

    def test_filter_previously_seen_preserves_new_businesses_sharing_phone(self):
        """filter_previously_seen_leads must not flag a new business as duplicate solely because of a shared phone."""
        historical = [
            {
                "title": "Central Wellness Clinic",
                "phone": "+1 (512) 555-9000",
                "clean_phone": "+1 (512) 555-9000",
                "address": "700 Medical Pkwy, Austin, TX"
            }
        ]
        new_batch = [
            {
                "title": "Dr. Sarah Johnson Pediatrics",
                "phone": "+1 (512) 555-9000",
                "clean_phone": "+1 (512) 555-9000",
                "address": "700 Medical Pkwy Ste 3, Austin, TX"
            },
            {
                "title": "Central Wellness Clinic",
                "phone": "+1 (512) 555-9000",
                "clean_phone": "+1 (512) 555-9000",
                "address": "700 Medical Pkwy, Austin, TX"
            }
        ]
        new_leads, skipped_leads = filter_previously_seen_leads(new_batch, historical)
        self.assertEqual(len(new_leads), 1)
        self.assertEqual(new_leads[0]["title"], "Dr. Sarah Johnson Pediatrics")
        self.assertEqual(len(skipped_leads), 1)
        self.assertEqual(skipped_leads[0]["title"], "Central Wellness Clinic")
        self.assertIn("HIGH confidence", skipped_leads[0]["duplicate_reason"])


class TestInternationalPhoneNormalization(unittest.TestCase):
    """Test country-aware phone formatting across major international dial codes."""

    def test_us_and_canada_nanp(self):
        self.assertEqual(clean_phone("+1 (512) 555-0199"), "+1 (512) 555-0199")
        self.assertEqual(clean_phone("15125550199"), "+1 (512) 555-0199")
        self.assertEqual(clean_phone("+15125550199"), "+1 (512) 555-0199")

    def test_uk_phone_numbers(self):
        self.assertEqual(clean_phone("+44 20 7946 0958"), "+44 2079 460958")
        self.assertEqual(clean_phone("0044 20 7946 0958"), "+44 2079 460958")

    def test_india_phone_numbers(self):
        self.assertEqual(clean_phone("+91 98765 43210"), "+91 98765 43210")
        self.assertEqual(clean_phone("09876543210"), "+91 98765 43210")
        self.assertEqual(clean_phone("9876543210", default_country="IN"), "+91 98765 43210")

    def test_australia_phone_numbers(self):
        self.assertEqual(clean_phone("+61 412 345 678"), "+61 4 1234 5678")

    def test_germany_phone_numbers(self):
        self.assertEqual(clean_phone("+49 30 1234567"), "+49 301 234567")

    def test_france_phone_numbers(self):
        self.assertEqual(clean_phone("+33 1 42 68 55 55"), "+33 1 42 68 55 55")

    def test_uae_phone_numbers(self):
        self.assertEqual(clean_phone("+971 50 123 4567"), "+971 50 123 4567")

    def test_singapore_phone_numbers(self):
        self.assertEqual(clean_phone("+65 6789 0123"), "+65 6789 0123")

    def test_malformed_and_short_inputs(self):
        self.assertEqual(clean_phone(""), "")
        self.assertEqual(clean_phone("   "), "")
        self.assertEqual(clean_phone("12345"), "")  # under 7 digits
        self.assertEqual(clean_phone(None), "")


class TestScoringBoundaries(unittest.TestCase):
    """Test exact scoring thresholds: 0 COLD, 35 COLD, 40 WARM, 65 WARM, 70 HOT, 100 capped."""

    def test_score_zero_empty_lead(self):
        res = score_lead({})
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["tier"], "COLD")
        self.assertEqual(res["tier_label"], "COLD ❄️")

    def test_score_35_email_only_cold_boundary(self):
        res = score_lead({"emails_list": ["contact@business.com"]})
        self.assertEqual(res["score"], 35)
        self.assertEqual(res["tier"], "COLD")

    def test_score_40_warm_boundary(self):
        # Email (35) + Social (5) = 40
        res = score_lead({
            "emails_list": ["contact@business.com"],
            "instagram": "https://instagram.com/biz"
        })
        self.assertEqual(res["score"], 40)
        self.assertEqual(res["tier"], "WARM")
        self.assertEqual(res["tier_label"], "WARM ⚡")

    def test_score_65_warm_high_boundary(self):
        # Email (35) + Phone (25) + Social (5) = 65
        res = score_lead({
            "emails_list": ["contact@business.com"],
            "clean_phone": "+1 (512) 555-0199",
            "facebook": "https://facebook.com/biz"
        })
        self.assertEqual(res["score"], 65)
        self.assertEqual(res["tier"], "WARM")

    def test_score_70_hot_boundary(self):
        # Email (35) + Phone (25) + Rating >= 4.5 (10) = 70
        res = score_lead({
            "emails_list": ["contact@business.com"],
            "clean_phone": "+1 (512) 555-0199",
            "review_rating": 4.6
        })
        self.assertEqual(res["score"], 70)
        self.assertEqual(res["tier"], "HOT")
        self.assertEqual(res["tier_label"], "HOT 🔥")

    def test_score_100_all_signals_and_capped(self):
        # Email (35) + Phone (25) + Website (20) + Rating>=4.5 (10) + Reviews>=20 (5) + Social (5) = 100
        res = score_lead({
            "emails_list": ["contact@business.com"],
            "clean_phone": "+1 (512) 555-0199",
            "website": "https://business.com",
            "review_rating": 4.9,
            "review_count": 55,
            "linkedin": "https://linkedin.com/company/biz"
        })
        self.assertEqual(res["score"], 100)
        self.assertEqual(res["tier"], "HOT")

    def test_rating_thresholds(self):
        # Below 4.0 gives 0
        r_low = score_lead({"review_rating": 3.9})
        self.assertEqual(r_low["score"], 0)

        # 4.0 - 4.4 gives +5
        r_mid = score_lead({"review_rating": 4.2})
        self.assertEqual(r_mid["score"], 5)

        # >= 4.5 gives +10
        r_high = score_lead({"review_rating": 4.5})
        self.assertEqual(r_high["score"], 10)

    def test_review_count_thresholds(self):
        # Under 20 gives 0
        cnt_low = score_lead({"review_count": 19})
        self.assertEqual(cnt_low["score"], 0)

        # >= 20 gives +5
        cnt_high = score_lead({"review_count": 20})
        self.assertEqual(cnt_high["score"], 5)


class TestJunkEmailCleansing(unittest.TestCase):
    """Test filtering of bot, CMS, and asset placeholder emails."""

    def test_junk_prefixes_discarded(self):
        junk_list = "sentry@mybusiness.com, noreply@mybusiness.com, mailer-daemon@mybusiness.com, support@wix.com, real@mybusiness.com"
        cleaned = clean_emails(junk_list, "https://mybusiness.com")
        self.assertEqual(cleaned, ["real@mybusiness.com"])

    def test_junk_domains_discarded(self):
        junk_domains = "alert@sentry.io, dummy@wixpress.com, sample@example.com, valid@company.com"
        cleaned = clean_emails(junk_domains, "https://company.com")
        self.assertEqual(cleaned, ["valid@company.com"])

    def test_asset_extensions_discarded(self):
        bad_assets = "icon@2x.png, logo@footer.jpg, contact@studio.com"
        cleaned = clean_emails(bad_assets, "https://studio.com")
        self.assertEqual(cleaned, ["contact@studio.com"])

    def test_website_domain_emails_sorted_first(self):
        mixed = "sales@gmail.com, hello@austinroasters.com, info@yahoo.com"
        cleaned = clean_emails(mixed, "https://austinroasters.com")
        self.assertEqual(cleaned[0], "hello@austinroasters.com")


class TestMissingFieldsAndRobustness(unittest.TestCase):
    """Test lead cleansing with missing, null, or malformed data."""

    def test_empty_record(self):
        lead = clean_lead({})
        self.assertEqual(lead["clean_phone"], "")
        self.assertEqual(lead["emails"], "")
        self.assertEqual(lead["domain"], "")
        self.assertEqual(lead["lead_score"], 0)
        self.assertEqual(lead["lead_tier"], "COLD")

    def test_null_values_for_all_fields(self):
        lead = clean_lead({
            "title": None,
            "phone": None,
            "website": None,
            "emails": None,
            "review_rating": None,
            "review_count": None,
            "category": None,
            "address": None
        })
        self.assertEqual(lead["clean_phone"], "")
        self.assertEqual(lead["domain"], "")
        self.assertEqual(lead["lead_score"], 0)

    def test_malformed_types_handled_gracefully(self):
        lead = clean_lead({
            "title": 12345,
            "phone": ["512", "555", "0199"],
            "review_rating": "not_a_rating",
            "review_count": "invalid"
        })
        self.assertIsInstance(lead["lead_score"], int)
        self.assertEqual(lead["lead_score"], 0)


class TestSocialExtractionAndCleansing(unittest.TestCase):
    """Test HTML social handle extraction and tracking parameter stripping."""

    def test_find_socials_in_html(self):
        html = """
        <html>
        <body>
            <a href="https://instagram.com/atx_coffee?igshid=xyz123">Instagram</a>
            <a href="https://www.facebook.com/atxcoffeeroasters">Facebook</a>
            <a href="https://www.linkedin.com/company/atx-coffee-co">LinkedIn</a>
        </body>
        </html>
        """
        socials = find_socials_in_html(html)
        self.assertIn("atx_coffee", socials["instagram"])
        self.assertIn("atxcoffeeroasters", socials["facebook"])
        self.assertIn("atx-coffee-co", socials["linkedin"])

    def test_skip_non_profile_handles(self):
        html = """
        <html>
        <body>
            <a href="https://instagram.com/reels/xyz">Reel</a>
            <a href="https://facebook.com/sharer/sharer.php">Share</a>
            <a href="https://facebook.com/1234">Invalid short ID</a>
            <a href="https://instagram.com/myrealprofile">Real Profile</a>
        </body>
        </html>
        """
        socials = find_socials_in_html(html)
        self.assertIn("myrealprofile", socials["instagram"])
        self.assertEqual(socials["facebook"], "")

    def test_clean_social_url_strips_tracking_params(self):
        url = "https://www.instagram.com/mybrand/?igshid=abc1234&utm_source=ig_web_button"
        cleaned = clean_social_url(url)
        self.assertEqual(cleaned, "https://www.instagram.com/mybrand")


class TestExportsAndFormulaInjection(unittest.TestCase):
    """Test CSV, CRM CSV, and formula injection sanitization."""

    def test_formula_injection_cells_escaped(self):
        self.assertEqual(sanitize_csv_cell("=1+1"), "'=1+1")
        self.assertEqual(sanitize_csv_cell("@SUM(A1:A5)"), "'@SUM(A1:A5)")
        self.assertEqual(sanitize_csv_cell("+cmd|' /C calc'!A0"), "'+cmd|' /C calc'!A0")
        self.assertEqual(sanitize_csv_cell("-2+3"), "'-2+3")

    def test_valid_numbers_not_escaped(self):
        self.assertEqual(sanitize_csv_cell(4.8), 4.8)
        self.assertEqual(sanitize_csv_cell(120), 120)
        self.assertEqual(sanitize_csv_cell("-97.7431"), "-97.7431")
        self.assertEqual(sanitize_csv_cell("+100"), "+100")

    def test_sanitize_csv_row(self):
        row = {
            "title": "=cmd|' /C calc'!A0",
            "phone": "+1 512 555 0199",
            "lead_score": 85
        }
        sanitized = sanitize_csv_row(row)
        self.assertEqual(sanitized["title"], "'=cmd|' /C calc'!A0")
        self.assertEqual(sanitized["lead_score"], 85)


class TestDashboardStopControlsAndJobHistory(unittest.TestCase):
    """Test dashboard job stop controls and history API contracts."""

    def test_dashboard_scraper_base_url_default(self):
        import dashboard
        self.assertIn("8085", dashboard.SCRAPER_BASE)

    def test_stop_job_handles_missing_job_id(self):
        """Verify dashboard _validate_job_id rejects malicious or empty job IDs."""
        import dashboard
        self.assertFalse(dashboard._validate_job_id(""))
        self.assertFalse(dashboard._validate_job_id("../../etc/passwd"))
        self.assertTrue(dashboard._validate_job_id("job_20261012_abcdef12"))


class TestDarkLightMonochromeUIAssets(unittest.TestCase):
    """Verify frontend UI assets support dark, light, and monochrome themes."""

    def test_index_html_contains_theme_toggle_elements(self):
        index_html = os.path.join(_repo_root, "web", "index.html")
        with open(index_html, "r") as f:
            content = f.read()
        self.assertIn('id="btn-theme-toggle"', content)
        self.assertIn('id="theme-icon-sun"', content)
        self.assertIn('id="theme-icon-moon"', content)
        self.assertIn('id="theme-icon-mono"', content)

    def test_styles_css_defines_all_three_themes(self):
        app_css = os.path.join(_repo_root, "web", "app.css")
        with open(app_css, "r") as f:
            content = f.read()
        self.assertIn('[data-theme="dark"]', content)
        self.assertIn('[data-theme="light"]', content)
        self.assertIn('[data-theme="mono"]', content)

    def test_app_js_listens_to_gate_theme_toggle(self):
        app_js = os.path.join(_repo_root, "web", "app.js")
        with open(app_js, "r") as f:
            content = f.read()
        self.assertIn("btnGateThemeToggle.addEventListener('click', triggerThemeToggle)", content)
        self.assertIn("localStorage.setItem('leadmap-theme'", content)


if __name__ == "__main__":
    unittest.main()

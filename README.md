# 🗺️ LeadMap Pro — Local B2B Lead Extraction & Intelligence Suite

<p align="center">
  <img src="assets/banner.svg" alt="LeadMap Pro — Local Google Maps Lead Extraction, Cleansing & Quality Scoring" width="100%">
</p>

<p align="center">
  <a href="https://github.com/vighneshpote55-svg/B2B-Scraper"><img alt="GitHub Repo" src="https://img.shields.io/badge/GitHub-B2B--Scraper-181717?style=for-the-badge&logo=github"></a>
  <img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-0ea5e9?style=for-the-badge">
  <img alt="100% Local Engine" src="https://img.shields.io/badge/Runs-100%25%20Local-10b981?style=for-the-badge">
  <img alt="Supabase Cloud" src="https://img.shields.io/badge/Supabase-Cloud%20Sync-3ecf8e?style=for-the-badge&logo=supabase">
  <img alt="Zero API Costs" src="https://img.shields.io/badge/Costs-$0%20Zero%20API%20Fees-8b5cf6?style=for-the-badge">
</p>

---

## ⚡ Overview

**LeadMap Pro** is a complete, self-hosted B2B lead generation and intelligence platform that extracts verified local business data directly from **Google Maps** — with **no paid APIs, no recurring SaaS subscriptions, and no credit limits**.

Equipped with an interactive web dashboard, automated data cleansing, phone normalization, lead quality scoring (HOT / WARM / COLD), social profile discovery (Instagram, Facebook, LinkedIn), and real-time Supabase cloud sync, LeadMap Pro turns raw map listings into outreach-ready prospects in seconds.

---

## 🚀 Key Features

### 1. 🖥️ Interactive Web Dashboard (`http://localhost:3000`)
- **Modern SaaS Experience:** Powered by a 3D digital wavefield background (Vanta Net), Anime.js staggered micro-animations, glassmorphism cards, and dark/light themes.
- **Real-Time Job Telemetry:** Live progress bar with stage-by-stage pipeline feedback, elapsed timer, and auto-refreshing archives.
- **Search Controls:** Instant Nominatim geocoding, keyword presets, and an interactive scroll depth slider (1–20).

### 2. 🛡️ Smart Lead Cleansing & Deduplication
- **Spam & Bot Email Filter:** Strips out image filenames (`.png`, `.svg`), placeholder addresses (`user@domain.com`), and system/sentry emails (`sentry@wix.com`, `noreply@`).
- **Standardized Phone Normalization:** Cleans and formats local and international numbers (e.g. `+91 XXXXX XXXXX` for India) with one-click copy buttons.
- **Zero-Duplicate Engine:** Automatically deduplicates entries matching verified phones, root domains, or duplicate business title/address combinations.

### 3. 🔥 Lead Quality Scoring (HOT / WARM / COLD)
Every prospect is evaluated on a 0–100 point scale:
- ✉️ **Direct Email Available:** `+35 pts`
- 📞 **Verified Phone Number:** `+25 pts`
- 🌐 **Active Website:** `+20 pts`
- ⭐ **High Rating (≥ 4.5★):** `+10 pts`
- 💬 **Established Review Count (≥ 20):** `+5 pts`
- 📸 **Verified Social Profiles:** `+5 pts`

Leads are automatically tiered into **HOT 🔥** (Score ≥ 70), **WARM ⚡** (40–69), or **COLD ❄️** (<40).

### 4. 📸 Website Social Profile Discovery
Visits each business website concurrently to extract direct social channels:
- 📸 **Instagram Handles:** `@username`
- 👥 **Facebook Pages:** `facebook.com/<page>`
- 💼 **LinkedIn Profiles:** `linkedin.com/company/<name>` or `linkedin.com/in/<profile>`

### 5. ☁️ Live Supabase Cloud Database Sync
- Seamless two-way integration with your PostgreSQL database on **Supabase**.
- Includes pre-built schema with indexes and Row-Level Security (`supabase_schema.sql`).
- Automatic background sync upon scrape completion, plus manual one-click sync.

### 6. 📊 Instant CRM & CSV Export
- One-click exports to structured **CSV** or **JSON** formatted for HubSpot, Salesforce, Apollo, Instantly, or Cold Email tools.

---

## 🛠️ Architecture

```
                                  ┌───────────────────────────────┐
                                  │      LeadMap Pro UI           │
                                  │    (Vanilla JS + Anime.js)    │
                                  └──────────────┬────────────────┘
                                                 │ HTTP / REST
                                                 ▼
┌───────────────────────────────┐  Proxy / Jobs  ┌───────────────────────────────┐
│     gmaps-scraper (Docker)    │◄──────────────┤       dashboard.py            │
│  Playwright / Chromium Engine │               │   (Python Async Server)       │
│  127.0.0.1:8085               │──────────────►│   Geocoding + Job Manager     │
└───────────────────────────────┘  Raw Listings └───────┬──────────────┬────────┘
                                                        │              │
                                          Clean & Score │              │ Cloud Sync
                                                        ▼              ▼
                                        ┌──────────────────┐ ┌──────────────────┐
                                        │ scripts/cleanser │ │ scripts/supabase │
                                        │ Phone / Email /  │ │ PostgreSQL Rest  │
                                        │ Social Profiles  │ │ Cloud Database   │
                                        └──────────────────┘ └──────────────────┘
```

---

## 🏁 Quick Start

### Prerequisites
- [Docker](https://docs.docker.com/get-docker/) & Docker Compose
- [Python 3.10+](https://www.python.org/)

### 1. Clone the Repository
```bash
git clone https://github.com/vighneshpote55-svg/B2B-Scraper.git
cd B2B-Scraper
```

### 2. Configure Environment Variables
Copy the example configuration:
```bash
cp .env.example .env
```
*(Optional: Add your `SUPABASE_URL` and `SUPABASE_KEY` if you wish to use Supabase cloud storage).*

### 3. Launch with One Command
Run the launcher script:
```bash
chmod +x run_dashboard.sh
./run_dashboard.sh
```

Or start manually:
```bash
# Start Docker scraper backend
docker compose up -d

# Start LeadMap Pro Web Dashboard
python3 dashboard.py
```

Open your browser and navigate to:  
👉 **`http://localhost:3000`**

---

## 💻 CLI Usage (Without Web UI)

You can also run headless extractions directly from your terminal:

```bash
# Extract 20 dentists with email cleansing & lead scoring
python3 scripts/scrape.py --query "Dentists in Pune" --depth 5 --email --socials

# Output to JSON format
python3 scripts/scrape.py --query "Cafes in Austin TX" --depth 3 --json --out cafes.json

# Simple single-query bash wrapper
./scripts/scrape.sh "Coffee shops in Seattle WA" 47.6062 -122.3321 5
```

---

## 🎚️ Understanding Scroll Depth

Google Maps displays only 5–7 places per screen in its sidebar. **Scroll Depth** specifies how many times the automated crawler scrolls down to uncover additional businesses:

| Scroll Depth | Approximate Leads | Extraction Speed | Recommended Use Case |
| :---: | :---: | :---: | :--- |
| **1 – 3** | ~5 – 15 leads | **Ultra Fast** (10–15s) | Quick tests and top local listings |
| **5 (Default)** | **~20 – 25 leads** | **Balanced** (20–35s) | **Best sweet spot for daily prospecting** |
| **10** | ~40 – 50 leads | Moderate (~1m) | Targeted outreach campaigns |
| **20 (Max)** | ~80 – 120+ leads | Deep (~2–3m) | Massive database building & CRM seeding |

---

## 🗄️ Supabase Cloud Integration Setup

1. Log into your [Supabase Dashboard](https://supabase.com).
2. Open the **SQL Editor**, paste the contents of [`supabase_schema.sql`](supabase_schema.sql), and click **Run**.
3. In your `.env` file, configure your project credentials:
   ```env
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_KEY=your-supabase-publishable-or-service-role-key
   SUPABASE_TABLE=leads
   SUPABASE_AUTO_SYNC=true
   ```
4. All completed scrapes will now sync automatically to your PostgreSQL database!

---

## 📁 Repository Structure

```
B2B-Scraper/
├── dashboard.py               # Full backend server: proxy, REST API, geocoding & scoring
├── run_dashboard.sh           # One-command health-check and dashboard launcher
├── docker-compose.yml         # Containerized Chromium scraper with 15m timeout & 4 workers
├── supabase_schema.sql        # Supabase PostgreSQL schema with RLS & indexes
├── .env.example               # Config template
│
├── web/                       # LeadMap Pro Frontend Suite
│   ├── index.html             # Dashboard markup & interactive controls
│   ├── app.css                # Premium responsive design system & glassmorphism
│   ├── app.js                 # UI logic, live progress, anime.js animations
│   ├── anime.min.js           # Micro-interaction animation engine
│   ├── three.r134.min.js      # Three.js 3D library
│   └── vanta.net.min.js       # Digital wavefield canvas background
│
├── scripts/                   # Core Python Intelligence Modules
│   ├── cleanser.py            # Phone normalization, spam email filter & social scraper
│   ├── supabase_client.py     # PostgREST Supabase client & upsert engine
│   ├── scrape.py              # Standalone Python CLI runner
│   └── scrape.sh              # Lightweight Bash extraction script
│
└── examples/                  # Sample search queries and batch configurations
```

---

## 🛡️ Responsible Scraping & Best Practices

- **Proxies for Heavy Volume:** For continuous bulk queries (>500 leads/day), configure rotating proxies in `.env` or job options to prevent temporary Google IP rate-limiting.
- **Data Compliance:** Scraped contact data should be used responsibly in compliance with regional regulations (GDPR, CAN-SPAM, CCPA).

---

## 📜 License

Created and maintained by **[Vighnesh Pote](https://github.com/vighneshpote55-svg)**.  
Licensed under the [MIT License](LICENSE).

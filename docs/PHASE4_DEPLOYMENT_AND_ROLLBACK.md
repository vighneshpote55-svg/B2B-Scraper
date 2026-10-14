# LeadMap Pro — Production Deployment, Operations & Rollback Runbook

**Version:** 1.0.0  
**Target Environment:** Linux (Ubuntu 22.04 LTS / Debian 12)  
**Security Boundary:** 127.0.0.1 Internal Loopback with Upstream TLS Reverse Proxy  

---

## 1. System Architecture & Topology

```
   [ Client Browser ]
           │
           │ HTTPS (443 / TLS 1.3)
           ▼
┌──────────────────────────────────────────────┐
│ Upstream Reverse Proxy (Caddy / Nginx)       │
│ - Automated TLS termination                  │
│ - Strict HSTS, CSP, and X-Frame-Options      │
│ - Zstd / Gzip compression                    │
└──────────────────────┬───────────────────────┘
                       │ HTTP (127.0.0.1:3000)
                       ▼
┌──────────────────────────────────────────────┐
│ LeadMap Pro Dashboard (dashboard.py)         │
│ - Unprivileged user: leadmap                 │
│ - Systemd process supervision                │
│ - POSIX file locking (fcntl)                 │
│ - In-memory & atomic JSON state              │
└──────────────────────┬───────────────────────┘
                       │ HTTP (127.0.0.1:8085)
                       ▼
┌──────────────────────────────────────────────┐
│ Scraper Backend Container (Docker)           │
│ - Image: gosom/google-maps-scraper:v1.18.1   │
│ - Loopback bound: 127.0.0.1:8085             │
│ - CPU / Memory limits enforced               │
└──────────────────────────────────────────────┘
```

---

## 2. Host Prerequisites

1. **Operating System:** Linux host (Ubuntu 22.04 LTS or Debian 12 recommended).
2. **Runtime Dependencies:**
   - Python 3.10+ (Standard library only; no external pip dependencies needed for core dashboard).
   - Docker Engine & Docker Compose v2.
   - Reverse Proxy: Caddy v2 or Nginx 1.20+.
   - Systemd for process supervision.
3. **Firewall & Network:**
   - Allow Port 80 (HTTP) and Port 443 (HTTPS) from external traffic.
   - Deny or block external access to Port 3000 (Dashboard) and Port 8085 (Scraper). These MUST remain bound strictly to `127.0.0.1`.

---

## 3. Step-by-Step Production Deployment

### Step 1: Create Dedicated System User
Create an unprivileged system user and group with a locked shell:
```bash
sudo useradd --system --user-group --shell /usr/sbin/nologin --home-dir /opt/leadmap leadmap
```

### Step 2: Install Application Files
Clone or copy the application to `/opt/leadmap`:
```bash
sudo mkdir -p /opt/leadmap
sudo cp -r . /opt/leadmap/
sudo mkdir -p /opt/leadmap/data /opt/leadmap/backups
sudo chown -R leadmap:leadmap /opt/leadmap
sudo chmod 700 /opt/leadmap/data /opt/leadmap/backups
```

### Step 3: Configure Environment Variables
Create `/opt/leadmap/.env` with secure file permissions:
```bash
sudo cp /opt/leadmap/.env.example /opt/leadmap/.env
sudo chmod 600 /opt/leadmap/.env
sudo chown leadmap:leadmap /opt/leadmap/.env
```
Generate high-entropy secrets and populate `.env`:
```bash
# Generate random session secret
python3 -c "import secrets; print('SESSION_SECRET=' + secrets.token_hex(32))"
```

Configure `.env`:
```ini
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=3000
SCRAPER_PORT=8085
SCRAPER_BASE_URL=http://localhost:8085
MAX_CONCURRENT_JOBS=2
JOB_TIMEOUT_SECONDS=600
JOB_RETENTION_DAYS=30
MAX_COMPLETED_JOBS=100
LOG_FORMAT=json

# Optional Supabase Integration (Leave blank to use built-in local database)
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_TABLE=leads
SUPABASE_AUTO_SYNC=true
```

### Step 4: Launch Scraper Backend Container
```bash
cd /opt/leadmap
docker compose -f deploy/docker-compose.prod.yml up -d
```
Verify the scraper container is healthy on loopback:
```bash
curl -f http://127.0.0.1:8085/
```

### Step 5: Install and Start Systemd Service
Copy the production unit file and enable it:
```bash
sudo cp /opt/leadmap/deploy/systemd/leadmap.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now leadmap
```
Check status:
```bash
sudo systemctl status leadmap
```

### Step 6: Configure Reverse Proxy (Choose Caddy or Nginx)

#### Option A: Caddy (Recommended for Automated TLS)
Copy `deploy/caddy/Caddyfile` to `/etc/caddy/Caddyfile`:
```bash
sudo cp /opt/leadmap/deploy/caddy/Caddyfile /etc/caddy/Caddyfile
# Edit /etc/caddy/Caddyfile to insert your public domain name
sudo systemctl reload caddy
```

#### Option B: Nginx
Copy `deploy/nginx/leadmap.conf` to `/etc/nginx/sites-available/leadmap.conf`:
```bash
sudo cp /opt/leadmap/deploy/nginx/leadmap.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/leadmap.conf /etc/nginx/sites-enabled/
# Obtain TLS certificate via Certbot
sudo certbot --nginx -d leadmap.example.com
sudo systemctl reload nginx
```

---

## 4. Operational Monitoring & Diagnostics

### Real-Time Logs
View structured logs via systemd journal:
```bash
journalctl -u leadmap -f -o cat
```

### Health & Connectivity Check
```bash
curl http://127.0.0.1:3000/api/status
```

### Comprehensive Metrics Endpoint
```bash
curl -H "Authorization: Bearer <ADMIN_SESSION_TOKEN>" http://127.0.0.1:3000/api/diagnostics
```

### Automated Database Verification (if Supabase configured)
```bash
python3 /opt/leadmap/scripts/verify_supabase_production.py
```

---

## 5. Backup & Disaster Recovery Runbook

### Creating a Snapshot Backup
Execute the backup utility:
```bash
sudo -u leadmap python3 /opt/leadmap/scripts/backup_data.py \
    --data-dir /opt/leadmap/data \
    --backup-dir /opt/leadmap/backups
```
This generates:
- `/opt/leadmap/backups/leadmap_data_<TIMESTAMP>.tar.gz`
- `/opt/leadmap/backups/leadmap_data_<TIMESTAMP>.tar.gz.sha256`

### Restoring from Backup
1. **Stop the dashboard service:**
   ```bash
   sudo systemctl stop leadmap
   ```
2. **Verify archive integrity without extracting:**
   ```bash
   sudo -u leadmap python3 /opt/leadmap/scripts/restore_data.py \
       --archive /opt/leadmap/backups/leadmap_data_<TIMESTAMP>.tar.gz \
       --target-dir /opt/leadmap/data \
       --verify-only
   ```
3. **Execute full restoration:**
   ```bash
   sudo -u leadmap python3 /opt/leadmap/scripts/restore_data.py \
       --archive /opt/leadmap/backups/leadmap_data_<TIMESTAMP>.tar.gz \
       --target-dir /opt/leadmap/data
   ```
4. **Restart the dashboard service:**
   ```bash
   sudo systemctl start leadmap
   ```
5. **Verify service status:**
   ```bash
   curl -f http://127.0.0.1:3000/api/health || curl -f http://127.0.0.1:3000/api/status
   ```

---

## 6. Fast Rollback Procedure (< 2 Minutes)

If a release verification fails in production, follow this sequence:

1. **Immediate Service Isolation:**
   ```bash
   sudo systemctl stop leadmap
   ```
2. **Revert Git Repository to Last Known-Good Commit:**
   ```bash
   cd /opt/leadmap
   sudo -u leadmap git checkout <LAST_KNOWN_GOOD_COMMIT_OR_TAG>
   ```
3. **Restore Last Known-Good Data Snapshot (if data corruption suspected):**
   ```bash
   sudo -u leadmap python3 scripts/restore_data.py \
       --archive /opt/leadmap/backups/<LAST_STABLE_BACKUP>.tar.gz \
       --target-dir /opt/leadmap/data
   ```
4. **Restart Service:**
   ```bash
   sudo systemctl start leadmap
   ```
5. **Confirm Health:**
   ```bash
   curl http://127.0.0.1:3000/api/status
   ```

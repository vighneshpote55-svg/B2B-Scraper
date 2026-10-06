/**
 * LeadMap Pro — Modern Lead Intelligence Dashboard
 * Enhanced with Anime.js Animations & Interactive Feedback Engine
 */

(function () {
  'use strict';

  // ── Global App State ───────────────────────────────────────────────────────
  let allLeads = [];
  let currentJobId = null;
  let activeFilter = 'all';
  let searchQuery = '';
  let sortField = 'score';
  let sortAsc = false;
  let isScraping = false;
  let pollTimer = null;
  let elapsedTimer = null;
  let scrapeStartTime = 0;

  // Additional feature state
  let selectedLeadIndices = new Set();
  let activeDossierLead = null;
  let activePitchType = 'wa';
  let leafletMap = null;
  let leafletTileLayer = null;
  let mapMarkersLayer = null;
  let currentViewMode = 'table';

  // ── DOM References ─────────────────────────────────────────────────────────
  const scraperStatusPill = document.getElementById('scraper-status-pill');
  const scraperStatusText = document.getElementById('scraper-status-text');
  const formScrape = document.getElementById('form-scrape');
  const inputKeyword = document.getElementById('input-keyword');
  const inputCity = document.getElementById('input-city');
  const inputDepth = document.getElementById('input-depth');
  const depthBadge = document.getElementById('depth-badge');
  const toggleEmail = document.getElementById('toggle-email');
  const toggleClean = document.getElementById('toggle-clean');
  const toggleScore = document.getElementById('toggle-score');
  const toggleSocials = document.getElementById('toggle-socials');
  const toggleExcludeSeen = document.getElementById('toggle-exclude-seen');
  const dedupeNoticeBanner = document.getElementById('dedupe-notice-banner');
  const btnSubmitScrape = document.getElementById('btn-submit-scrape');
  const btnSubmitText = document.getElementById('btn-submit-text');

  // Live Progress Elements
  const liveProgressBox = document.getElementById('live-progress-box');
  const progressStageTitle = document.getElementById('progress-stage-title');
  const progressSubtext = document.getElementById('progress-subtext');
  const progressTimer = document.getElementById('progress-timer');
  const progressPctBadge = document.getElementById('progress-pct-badge');
  const progressBarFill = document.getElementById('progress-bar-fill');
  const btnCancelScrape = document.getElementById('btn-cancel-scrape');

  // KPI Metrics
  const metricTotal = document.getElementById('metric-total');
  const metricHot = document.getElementById('metric-hot');
  const metricEmails = document.getElementById('metric-emails');
  const metricPhones = document.getElementById('metric-phones');

  // Table & Toolbar
  const leadsTableContainer = document.getElementById('leads-table-container');
  const leadsMapContainer = document.getElementById('leads-map-container');
  const leadsTableBody = document.getElementById('leads-table-body');
  const tableShowingText = document.getElementById('table-showing-text');
  const inputTableSearch = document.getElementById('input-table-search');
  const filterTabs = document.querySelectorAll('.tab-btn');
  const tabCountAll = document.getElementById('tab-count-all');
  const tabCountHot = document.getElementById('tab-count-hot');
  const tabCountWarm = document.getElementById('tab-count-warm');
  const tabCountEmail = document.getElementById('tab-count-email');
  const checkAllLeads = document.getElementById('check-all-leads');
  const btnViewTable = document.getElementById('btn-view-table');
  const btnViewMap = document.getElementById('btn-view-map');

  // Export
  const btnExportDropdown = document.getElementById('btn-export-dropdown');
  const exportMenu = document.getElementById('export-menu');

  // History Drawer
  const btnToggleHistory = document.getElementById('btn-toggle-history');
  const historyDrawer = document.getElementById('history-drawer');
  const drawerBackdrop = document.getElementById('drawer-backdrop');
  const btnCloseDrawer = document.getElementById('btn-close-drawer');
  const historyList = document.getElementById('history-list');

  // Lead Intelligence Dossier Drawer
  const dossierDrawer = document.getElementById('dossier-drawer');
  const btnCloseDossier = document.getElementById('btn-close-dossier');
  const dossierTierBadge = document.getElementById('dossier-tier-badge');
  const dossierTitle = document.getElementById('dossier-title');
  const dossierCategory = document.getElementById('dossier-category');
  const dossierWhatsappBtn = document.getElementById('dossier-whatsapp-btn');
  const dossierCallBtn = document.getElementById('dossier-call-btn');
  const dossierEmailBtn = document.getElementById('dossier-email-btn');
  const dossierPhone = document.getElementById('dossier-phone');
  const btnCopyDossierPhone = document.getElementById('btn-copy-dossier-phone');
  const dossierEmail = document.getElementById('dossier-email');
  const btnCopyDossierEmail = document.getElementById('btn-copy-dossier-email');
  const dossierWebsiteLink = document.getElementById('dossier-website-link');
  const dossierAddress = document.getElementById('dossier-address');
  const dossierSocialsContainer = document.getElementById('dossier-socials-container');
  const dossierRating = document.getElementById('dossier-rating');
  const dossierReviews = document.getElementById('dossier-reviews');
  const dossierScoreNum = document.getElementById('dossier-score-num');
  const dossierScoreReasons = document.getElementById('dossier-score-reasons');
  const outreachPitchText = document.getElementById('outreach-pitch-text');
  const btnCopyPitch = document.getElementById('btn-copy-pitch');
  const btnSendPitchWa = document.getElementById('btn-send-pitch-wa');
  const outreachTabs = document.querySelectorAll('.outreach-tab-btn');

  // Floating Bulk Actions Bar
  const bulkActionBar = document.getElementById('bulk-action-bar');
  const bulkSelectedCount = document.getElementById('bulk-selected-count');
  const btnBulkCopyEmails = document.getElementById('btn-bulk-copy-emails');
  const btnBulkCopyPhones = document.getElementById('btn-bulk-copy-phones');
  const btnBulkSyncSupabase = document.getElementById('btn-bulk-sync-supabase');
  const btnBulkExportCsv = document.getElementById('btn-bulk-export-csv');
  const btnBulkClear = document.getElementById('btn-bulk-clear');

  // Theme Toggle
  const btnThemeToggle = document.getElementById('btn-theme-toggle');
  const themeIconSun = document.getElementById('theme-icon-sun');
  const themeIconMoon = document.getElementById('theme-icon-moon');

  // ── Anime.js Animation Engine Integration ──────────────────────────────────
  function runAnimation(targets, params) {
    try {
      if (typeof window.anime !== 'undefined') {
        if (typeof window.anime.animate === 'function') {
          return window.anime.animate(targets, params);
        } else if (typeof window.anime === 'function') {
          return window.anime({ targets, ...params });
        }
      }
    } catch (e) {
      console.warn('Anime.js animation error:', e);
    }
  }

  function getStagger(val, options = {}) {
    try {
      if (typeof window.anime !== 'undefined' && typeof window.anime.stagger === 'function') {
        return window.anime.stagger(val, options);
      }
    } catch (e) {}
    return (t, i) => i * val;
  }

  // Smooth numeric counter animation
  function animateCounter(element, targetVal) {
    if (!element) return;
    const current = parseInt(element.textContent.replace(/[^\d]/g, ''), 10) || 0;
    const target = parseInt(targetVal, 10) || 0;
    if (current === target) {
      element.textContent = target;
      return;
    }

    const obj = { val: current };
    runAnimation(obj, {
      val: target,
      duration: 650,
      ease: 'outExpo',
      onUpdate: () => {
        element.textContent = Math.round(obj.val);
      },
      onComplete: () => {
        element.textContent = target;
      }
    });

    const card = element.closest('.metric-card');
    if (card) {
      runAnimation(card, {
        scale: [1, 1.035, 1],
        duration: 350,
        ease: 'outBack(1.3)'
      });
    }
  }

  // Micro-bounce for buttons, chips, and cards
  function addMicroBounce(el) {
    if (!el) return;
    runAnimation(el, {
      scale: [1, 0.92, 1.05, 1],
      duration: 320,
      ease: 'outBack(1.5)'
    });
  }

  // Initial page load choreography
  function animatePageEntrance() {
    const brandIcon = document.querySelector('.brand-icon');
    const brandText = document.querySelector('.brand-text');
    const controlCard = document.querySelector('.scrape-control-card');
    const metricCards = document.querySelectorAll('.metric-card');
    const tableCard = document.querySelector('.leads-table-card');

    if (brandIcon) {
      runAnimation(brandIcon, {
        scale: [0.75, 1],
        rotate: [-12, 0],
        opacity: [0, 1],
        duration: 480,
        ease: 'outBack(1.6)'
      });
    }

    if (brandText) {
      runAnimation(brandText, {
        opacity: [0, 1],
        translateX: [-10, 0],
        duration: 400,
        delay: 80,
        ease: 'outCubic'
      });
    }

    if (controlCard) {
      runAnimation(controlCard, {
        opacity: [0, 1],
        translateY: [18, 0],
        duration: 450,
        delay: 120,
        ease: 'outCubic'
      });
    }

    if (metricCards.length > 0) {
      runAnimation(Array.from(metricCards), {
        opacity: [0, 1],
        translateY: [16, 0],
        duration: 400,
        delay: (el, i) => 220 + i * 55,
        ease: 'outCubic'
      });
    }

    if (tableCard) {
      runAnimation(tableCard, {
        opacity: [0, 1],
        translateY: [20, 0],
        duration: 450,
        delay: 420,
        ease: 'outCubic'
      });
    }
  }

  // ── Digital Wave Field (Autonomous Sine Wave Mesh — No Cursor Tracking) ───
  let waveFieldInstance = null;

  class DigitalWaveField {
    constructor(canvasId) {
      this.canvas = document.getElementById(canvasId);
      if (!this.canvas) return;
      this.ctx = this.canvas.getContext('2d');
      this.theme = document.documentElement.getAttribute('data-theme') || 'dark';
      this.time = 0;
      this.scrollY = window.scrollY || 0;
      this.animId = null;
      this.waves = [
        { amplitude: 38, frequency: 0.007, speed: 0.016, offset: 0, opacity: 0.35 },
        { amplitude: 48, frequency: 0.005, speed: 0.012, offset: 2.2, opacity: 0.25 },
        { amplitude: 30, frequency: 0.009, speed: 0.020, offset: 4.1, opacity: 0.20 }
      ];

      this.resize = this.resize.bind(this);
      this.onScroll = this.onScroll.bind(this);
      this.render = this.render.bind(this);

      window.addEventListener('resize', this.resize, { passive: true });
      window.addEventListener('scroll', this.onScroll, { passive: true });

      this.resize();
      this.start();
    }

    resize() {
      if (!this.canvas) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.width = window.innerWidth;
      this.height = window.innerHeight;
      this.canvas.width = this.width * dpr;
      this.canvas.height = this.height * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    onScroll() {
      this.scrollY = window.scrollY || 0;
    }

    setTheme(theme) {
      this.theme = theme;
    }

    start() {
      if (this.animId) cancelAnimationFrame(this.animId);
      this.render();
    }

    render() {
      if (!this.ctx) return;
      this.time += 0.018;

      const isLight = this.theme === 'light';
      this.ctx.clearRect(0, 0, this.width, this.height);

      const baseCenterY = this.height * 0.55 - (this.scrollY * 0.12); // Subtle parallax

      // Render autonomous flowing wave ribbons with glowing data nodes
      for (let w = 0; w < this.waves.length; w++) {
        const wave = this.waves[w];
        this.ctx.beginPath();
        
        const step = this.width < 640 ? 32 : 24;
        let first = true;

        for (let x = 0; x <= this.width + step; x += step) {
          const y = baseCenterY + 
                    Math.sin(x * wave.frequency + this.time * wave.speed * 60 + wave.offset) * wave.amplitude +
                    Math.cos(x * 0.003 - this.time * 0.3) * (wave.amplitude * 0.4);
          
          if (first) {
            this.ctx.moveTo(x, y);
            first = false;
          } else {
            this.ctx.lineTo(x, y);
          }

          // Subtle glowing node points on wave peaks
          if ((x / step) % 4 === 0) {
            this.ctx.save();
            this.ctx.fillStyle = isLight ? 'rgba(99, 102, 241, 0.45)' : 'rgba(129, 140, 248, 0.65)';
            this.ctx.beginPath();
            this.ctx.arc(x, y, 2.5, 0, Math.PI * 2);
            this.ctx.fill();
            this.ctx.restore();
          }
        }

        this.ctx.strokeStyle = isLight 
          ? `rgba(99, 102, 241, ${wave.opacity * 0.9})` 
          : `rgba(129, 140, 248, ${wave.opacity * 1.2})`;
        this.ctx.lineWidth = 1.8;
        this.ctx.stroke();
      }

      this.animId = requestAnimationFrame(this.render);
    }
  }

  function initDigitalWaveField(theme = 'dark') {
    if (!waveFieldInstance) {
      waveFieldInstance = new DigitalWaveField('digital-wave-canvas');
    }
    if (waveFieldInstance) {
      waveFieldInstance.setTheme(theme);
    }
  }

  // ── Scroll Reveal Intersection Observer ────────────────────────────────────
  function initScrollReveal() {
    const revealElements = document.querySelectorAll('.scroll-reveal');
    if (!('IntersectionObserver' in window)) {
      revealElements.forEach(el => el.classList.add('is-revealed'));
      return;
    }

    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-revealed');
        }
      });
    }, {
      root: null,
      threshold: 0.05,
      rootMargin: '0px 0px -20px 0px'
    });

    revealElements.forEach(el => observer.observe(el));
  }

  // ── Motion Path: Extraction Pipeline Progress Nodes ─────────────────────────
  function updatePipelineNodes(pct, isComplete = false) {
    const nodeMaps = document.getElementById('p-node-maps');
    const nodeCleanse = document.getElementById('p-node-cleanse');
    const nodeCloud = document.getElementById('p-node-cloud');
    if (!nodeMaps || !nodeCleanse || !nodeCloud) return;

    if (isComplete) {
      nodeMaps.className = 'pipeline-step-node completed';
      nodeCleanse.className = 'pipeline-step-node completed';
      nodeCloud.className = 'pipeline-step-node completed';
      return;
    }

    if (pct < 35) {
      nodeMaps.className = 'pipeline-step-node active';
      nodeCleanse.className = 'pipeline-step-node';
      nodeCloud.className = 'pipeline-step-node';
    } else if (pct < 75) {
      nodeMaps.className = 'pipeline-step-node completed';
      nodeCleanse.className = 'pipeline-step-node active';
      nodeCloud.className = 'pipeline-step-node';
    } else {
      nodeMaps.className = 'pipeline-step-node completed';
      nodeCleanse.className = 'pipeline-step-node completed';
      nodeCloud.className = 'pipeline-step-node active';
    }
  }

  // ── App Initialization ─────────────────────────────────────────────────────
  function init() {
    setupThemeToggle();
    initDigitalWaveField();
    initScrollReveal();
    checkBackendStatus();
    setInterval(checkBackendStatus, 10000);

    setupFormControls();
    setupTableEvents();
    setupExportEvents();
    setupHistoryDrawer();
    setupDossierEvents();
    setupBulkActionBar();
    setupMapView();
    setupSupabaseIntegration();

    // Run entrance choreography
    animatePageEntrance();

    // Check if there are past jobs to load initially
    loadInitialJobIfAvailable();
  }

  // ── Theme Toggle (Dark / Light Mode) ───────────────────────────────────────
  function setupThemeToggle() {
    const urlParams = new URLSearchParams(window.location.search);
    const themeParam = urlParams.get('theme');
    const savedTheme = themeParam || localStorage.getItem('leadmap-theme') || 'dark';
    applyTheme(savedTheme, false);

    if (btnThemeToggle) {
      btnThemeToggle.addEventListener('click', () => {
        const current = document.documentElement.getAttribute('data-theme') || 'dark';
        const nextTheme = current === 'dark' ? 'light' : 'dark';
        
        // Tactile spin animation
        const activeIcon = nextTheme === 'light' ? themeIconMoon : themeIconSun;
        runAnimation(activeIcon, {
          rotate: [0, 360],
          scale: [0.6, 1.15, 1],
          duration: 420,
          ease: 'outBack(1.5)'
        });

        applyTheme(nextTheme, true);
      });
    }
  }

  function applyTheme(theme, showNotification = false) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('leadmap-theme', theme);

    if (theme === 'light') {
      if (themeIconSun) themeIconSun.classList.add('is-hidden');
      if (themeIconMoon) themeIconMoon.classList.remove('is-hidden');
      if (btnThemeToggle) btnThemeToggle.setAttribute('title', 'Switch to Dark Mode');
      if (showNotification) showToast('Switched to Light Mode ☀️', 'success');
    } else {
      if (themeIconMoon) themeIconMoon.classList.add('is-hidden');
      if (themeIconSun) themeIconSun.classList.remove('is-hidden');
      if (btnThemeToggle) btnThemeToggle.setAttribute('title', 'Switch to Light Mode');
      if (showNotification) showToast('Switched to Dark Mode 🌙', 'success');
    }

    // Synchronize Digital Wave Field colors with current theme
    initDigitalWaveField(theme);
  }

  // ── Scraper Status Check ───────────────────────────────────────────────────
  async function checkBackendStatus() {
    try {
      const res = await fetch('/api/status');
      const data = await res.json();
      if (data.scraper_up) {
        scraperStatusPill.className = 'status-pill status-online';
        scraperStatusText.textContent = `Scraper Active (${data.scraper_base.replace('http://', '')})`;
      } else {
        scraperStatusPill.className = 'status-pill status-offline';
        scraperStatusText.textContent = 'Scraper Offline';
      }
    } catch {
      scraperStatusPill.className = 'status-pill status-offline';
      scraperStatusText.textContent = 'Server Offline';
    }
  }

  // ── Form Controls & Extraction Dispatch ────────────────────────────────────
  function setupFormControls() {
    // Slider depth update with spring bounce
    inputDepth.addEventListener('input', (e) => {
      const d = parseInt(e.target.value, 10);
      const est = d * 4;
      depthBadge.textContent = `${d} (≈ ${est} leads)`;
      addMicroBounce(depthBadge);
    });

    // Preset chips with ripple bounce
    document.querySelectorAll('.chip-preset').forEach((btn) => {
      btn.addEventListener('click', () => {
        addMicroBounce(btn);
        inputKeyword.value = btn.dataset.keyword || '';
        inputCity.value = btn.dataset.city || '';

        // Subtle focus pulse on the inputs
        runAnimation([inputKeyword, inputCity], {
          scale: [0.98, 1],
          duration: 250,
          ease: 'outQuad'
        });
        inputKeyword.focus();
      });
    });

    // Form submission
    formScrape.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (isScraping) return;

      const keyword = inputKeyword.value.trim();
      const city = inputCity.value.trim();

      if (!keyword) {
        showToast('Please enter a target business niche or keyword.', 'error');
        inputKeyword.focus();
        return;
      }

      await startScrape({
        keyword,
        city,
        depth: parseInt(inputDepth.value, 10),
        email: toggleEmail.checked,
        clean: toggleClean.checked,
        score: toggleScore.checked,
        socials: toggleSocials ? toggleSocials.checked : true,
        exclude_seen: toggleExcludeSeen ? toggleExcludeSeen.checked : true
      });
    });

    // Stop / Cancel Active Scrape Button
    if (btnCancelScrape) {
      btnCancelScrape.addEventListener('click', async () => {
        addMicroBounce(btnCancelScrape);
        if (!currentJobId) return;
        btnCancelScrape.disabled = true;
        btnCancelScrape.innerHTML = '<span>Stopping...</span>';
        try {
          const res = await fetch('/api/scrape/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_id: currentJobId })
          });
          const data = await res.json();
          clearInterval(pollTimer);
          clearInterval(elapsedTimer);
          stopScrapingState();
          progressStageTitle.textContent = 'Extraction Stopped';
          progressSubtext.textContent = 'Cancelled by user.';
          showToast('Scrape cancelled.', 'info');
          setTimeout(() => {
            runAnimation(liveProgressBox, {
              opacity: [1, 0],
              translateY: [0, -10],
              duration: 300,
              ease: 'inQuad',
              onComplete: () => {
                liveProgressBox.classList.add('is-hidden');
              }
            });
          }, 1800);
        } catch (err) {
          showToast('Failed to cancel scrape: ' + err.message, 'error');
        } finally {
          btnCancelScrape.disabled = false;
          btnCancelScrape.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect></svg><span>Stop</span>';
        }
      });
    }
  }

  // Start Scrape
  async function startScrape(payload) {
    isScraping = true;
    btnSubmitScrape.disabled = true;
    btnSubmitText.textContent = 'Extracting...';
    addMicroBounce(btnSubmitScrape);

    // Show live progress box with smooth slide-down animation
    liveProgressBox.classList.remove('is-hidden');
    runAnimation(liveProgressBox, {
      opacity: [0, 1],
      translateY: [-15, 0],
      duration: 350,
      ease: 'outCubic'
    });

    progressStageTitle.textContent = 'Resolving Location & Dispatching Job...';
    progressSubtext.textContent = `Targeting "${payload.keyword}" in ${payload.city || 'target area'}`;
    progressBarFill.style.width = '10%';
    progressPctBadge.textContent = '10%';
    updatePipelineNodes(10, false);

    scrapeStartTime = Date.now();
    startElapsedTimer();

    try {
      const res = await fetch('/api/scrape/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || 'Failed to start extraction');
      }

      currentJobId = data.job_id;
      showToast(`Extraction dispatched: ID ${currentJobId.substring(0, 8)}`, 'success');
      startPolling(currentJobId);

    } catch (err) {
      stopScrapingState();
      showToast(`Error: ${err.message}`, 'error');
    }
  }

  // Timer helper
  function startElapsedTimer() {
    clearInterval(elapsedTimer);
    elapsedTimer = setInterval(() => {
      const totalSec = Math.floor((Date.now() - scrapeStartTime) / 1000);
      const mins = String(Math.floor(totalSec / 60)).padStart(2, '0');
      const secs = String(totalSec % 60).padStart(2, '0');
      progressTimer.textContent = `${mins}:${secs}`;
    }, 1000);
  }

  // Poll Job Progress with smooth tweening
  function startPolling(jobId) {
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const res = await fetch(`/api/job/${jobId}/status`);
        if (!res.ok) return;
        const job = await res.json();

        // Update UI progress with Anime.js width tweening
        const pct = job.progress_percent || 30;
        runAnimation(progressBarFill, {
          width: `${pct}%`,
          duration: 500,
          ease: 'outCubic'
        });
        progressPctBadge.textContent = `${pct}%`;
        addMicroBounce(progressPctBadge);
        progressStageTitle.textContent = job.stage || 'Scraping Google Maps...';
        updatePipelineNodes(pct, false);

        if (job.status === 'ok') {
          clearInterval(pollTimer);
          clearInterval(elapsedTimer);
          updatePipelineNodes(100, true);
          runAnimation(progressBarFill, {
            width: '100%',
            duration: 400,
            ease: 'outCubic'
          });
          progressPctBadge.textContent = '100%';
          addMicroBounce(progressPctBadge);
          progressStageTitle.textContent = 'Extraction Complete!';
          progressSubtext.textContent = `Successfully extracted & qualified ${job.leads ? job.leads.length : 0} business leads`;

          setTimeout(() => {
            runAnimation(liveProgressBox, {
              opacity: [1, 0],
              translateY: [0, -10],
              duration: 300,
              ease: 'inQuad',
              onComplete: () => {
                liveProgressBox.classList.add('is-hidden');
                stopScrapingState();
              }
            });
          }, 3500);

          loadJobLeads(jobId);
          showToast(`Done! Extracted ${job.leads ? job.leads.length : 0} leads.`, 'success');

        } else if (job.status === 'failed') {
          clearInterval(pollTimer);
          clearInterval(elapsedTimer);
          stopScrapingState();
          showToast(job.error || 'Job failed. Google may be temporarily throttling.', 'error');
          progressStageTitle.textContent = 'Job Failed';
          progressSubtext.textContent = job.error || 'Check Docker scraper logs.';
        }
      } catch (err) {
        console.error('Poll error:', err);
      }
    }, 2500);
  }

  function stopScrapingState() {
    isScraping = false;
    btnSubmitScrape.disabled = false;
    btnSubmitText.textContent = 'Launch Extraction';
    clearInterval(elapsedTimer);
  }

  // ── Auto-load Most Recent Job on Startup ───────────────────────────────────
  async function loadInitialJobIfAvailable() {
    try {
      const res = await fetch('/api/jobs');
      if (!res.ok) return;
      const data = await res.json();
      const jobs = data.jobs || [];
      if (jobs.length > 0) {
        const latestJob = jobs[0];
        currentJobId = latestJob.id;
        await loadJobLeads(currentJobId);
      }
    } catch (err) {
      console.warn('Could not auto-load initial job:', err);
    }
  }

  // Load leads from API
  async function loadJobLeads(jobId) {
    try {
      const res = await fetch(`/api/job/${jobId}/leads`);
      const data = await res.json();
      allLeads = data.leads || [];
      currentJobId = jobId;

      // Check status to display incremental deduplication notice
      try {
        const sRes = await fetch(`/api/job/${jobId}/status`);
        if (sRes.ok) {
          const sData = await sRes.json();
          const skippedCount = sData.skipped_previous_count || (sData.metrics && sData.metrics.skipped_previous) || 0;
          if (dedupeNoticeBanner) {
            if (skippedCount > 0 && allLeads.length > 0) {
              dedupeNoticeBanner.innerHTML = `🛡️ <strong>Incremental Filter:</strong> Excluded <strong>${skippedCount}</strong> previously collected businesses from past searches. Showing <strong>${allLeads.length} brand-new</strong> leads!`;
              dedupeNoticeBanner.className = 'dedupe-notice-banner dedupe-notice-success';
              dedupeNoticeBanner.classList.remove('is-hidden');
            } else if (skippedCount > 0 && allLeads.length === 0) {
              dedupeNoticeBanner.innerHTML = `⚡ <strong>All ${skippedCount} businesses were already collected in your previous searches!</strong> No duplicate leads were added.<br>💡 <em>Tip: Increase the Scroll Depth slider (e.g. to 10 or 15) or specify a sub-neighborhood (e.g. "Kothrud, Pune") to crawl deeper for new leads.</em>`;
              dedupeNoticeBanner.className = 'dedupe-notice-banner dedupe-notice-warning';
              dedupeNoticeBanner.classList.remove('is-hidden');
            } else {
              dedupeNoticeBanner.classList.add('is-hidden');
            }
          }
        }
      } catch (e) {
        console.warn('Could not fetch status banner:', e);
      }

      updateMetrics();
      renderTable();
    } catch (err) {
      console.error('Error loading leads:', err);
    }
  }

  // Update top metrics and tab numbers with Anime.js counter animations
  function updateMetrics() {
    const total = allLeads.length;
    const hot = allLeads.filter((l) => l.lead_tier === 'HOT').length;
    const warm = allLeads.filter((l) => l.lead_tier === 'WARM').length;
    const withEmail = allLeads.filter((l) => Boolean(l.emails)).length;
    const withPhone = allLeads.filter((l) => Boolean(l.clean_phone || l.phone)).length;

    animateCounter(metricTotal, total);
    animateCounter(metricHot, hot);
    animateCounter(metricEmails, withEmail);
    animateCounter(metricPhones, withPhone);

    tabCountAll.textContent = total;
    tabCountHot.textContent = hot;
    tabCountWarm.textContent = warm;
    tabCountEmail.textContent = withEmail;
  }

  // ── Leads Data Table Rendering ─────────────────────────────────────────────
  // ── Phone & WhatsApp Helpers ──────────────────────────────────────────────
  function getCleanDigitsForWhatsApp(phone) {
    if (!phone) return '';
    let str = String(phone).trim();
    let digits = str.replace(/\D/g, '');
    if (!digits) return '';

    if (str.startsWith('+')) {
      return digits;
    }
    if (digits.length === 11 && digits.startsWith('0')) {
      digits = digits.substring(1);
    }
    if (digits.length === 10 && /^[6-9]/.test(digits)) {
      return '91' + digits;
    }
    if (digits.length === 10) {
      return '1' + digits;
    }
    return digits;
  }

  function buildWhatsAppUrl(phone, businessName, rating, address) {
    const digits = getCleanDigitsForWhatsApp(phone);
    if (!digits) return '';
    const starPart = rating ? ` (noticed your ${rating}★ profile)` : '';
    const city = (address || '').split(',').slice(-3, -1).join(', ').trim();
    const cityPart = city ? ` in ${city}` : '';
    const msg = `Hi ${businessName || 'there'}! I came across your business${cityPart}${starPart} and wanted to connect regarding a quick growth opportunity. Are you taking on new clients this month?`;
    return `https://wa.me/${digits}?text=${encodeURIComponent(msg)}`;
  }

  // ── Automated Cold Outreach Pitch Generator ─────────────────────────────────
  function generateOutreachPitch(lead, type = 'wa') {
    if (!lead) return '';
    const name = lead.title || 'there';
    const category = (lead.category || 'business').toLowerCase();
    const rawCity = (lead.address || '').split(',').slice(-3, -1).join(', ').trim() || 'your local market';
    const rating = lead.review_rating ? `${Number(lead.review_rating).toFixed(1)}★` : 'solid reputation';
    const reviews = lead.review_count ? `${lead.review_count} verified reviews` : 'positive feedback';
    const website = lead.website || '';
    const domain = lead.domain || (website ? website.replace(/^https?:\/\//i, '').replace(/^www\./i, '').split('/')[0] : '');

    if (type === 'wa') {
      return `Hi team at ${name} 👋

I was looking at top-rated ${category} providers around ${rawCity} and noticed your Google profile (${rating} with ${reviews}).

We help established businesses turn high search interest into booked appointments and qualified inquiries on autopilot.

Do you have 2 minutes this week for a quick chat, or would you prefer a 60-second video breakdown showing what we found for ${name}?

Best regards,
Lead Intelligence Team`;
    } else {
      return `Subject: Quick question regarding ${name}'s customer pipeline in ${rawCity}

Hi ${name} Team,

I came across your business while researching high-performing ${category} teams in ${rawCity}. Congratulations on maintaining ${rating} across ${reviews}—it clearly shows your commitment to quality.

While reviewing your online presence${domain ? ' at ' + domain : ''}, I noticed a few high-leverage opportunities to capture more high-intent prospects before they reach your competitors.

We specialize in helping verified local businesses scale inbound qualified inquiries without relying on expensive ad spend.

Would you be open to a 5-minute conversation on Thursday or Friday to see how we could drive 15-25 new qualified inquiries to ${name}?

Best regards,
Lead Partnerships Team`;
    }
  }

  // ── Lead Intelligence Dossier Slide-Over Drawer ─────────────────────────────
  function openDossier(lead) {
    if (!lead) return;
    activeDossierLead = lead;

    const tier = lead.lead_tier || 'COLD';
    const score = lead.lead_score || 0;
    const tierClass = tier === 'HOT' ? 'tier-hot' : tier === 'WARM' ? 'tier-warm' : 'tier-cold';
    const tierIcon = tier === 'HOT' ? '🔥' : tier === 'WARM' ? '⚡' : '❄️';

    dossierTierBadge.className = `tier-badge ${tierClass}`;
    dossierTierBadge.innerHTML = `<span>${tierIcon} ${tier}</span> <span class="lead-score-val">${score}</span>`;
    dossierTitle.textContent = lead.title || 'Unknown Business';
    dossierCategory.textContent = lead.category || 'Local Business';

    const phoneVal = lead.clean_phone || lead.phone || '';
    const emailVal = lead.emails || '';
    const webVal = lead.website || '';
    const domainVal = lead.domain || (webVal ? webVal.replace(/^https?:\/\//i, '').replace(/^www\./i, '').split('/')[0] : '');

    // Quick Action Bar
    const waUrl = buildWhatsAppUrl(phoneVal, lead.title, lead.review_rating, lead.address);
    if (waUrl) {
      dossierWhatsappBtn.href = waUrl;
      dossierWhatsappBtn.classList.remove('is-disabled');
      dossierWhatsappBtn.setAttribute('title', 'Chat directly with business on WhatsApp');
    } else {
      dossierWhatsappBtn.href = '#';
      dossierWhatsappBtn.classList.add('is-disabled');
      dossierWhatsappBtn.setAttribute('title', 'No valid phone available for WhatsApp');
    }

    if (phoneVal) {
      dossierCallBtn.href = `tel:${phoneVal}`;
      dossierCallBtn.classList.remove('is-disabled');
    } else {
      dossierCallBtn.href = '#';
      dossierCallBtn.classList.add('is-disabled');
    }

    if (emailVal) {
      dossierEmailBtn.href = `mailto:${emailVal.split(',')[0].trim()}`;
      dossierEmailBtn.classList.remove('is-disabled');
    } else {
      dossierEmailBtn.href = '#';
      dossierEmailBtn.classList.add('is-disabled');
    }

    // Detail rows
    dossierPhone.textContent = phoneVal || '—';
    btnCopyDossierPhone.dataset.copy = phoneVal || '';
    btnCopyDossierPhone.style.display = phoneVal ? 'inline-flex' : 'none';

    dossierEmail.textContent = emailVal || '—';
    btnCopyDossierEmail.dataset.copy = emailVal || '';
    btnCopyDossierEmail.style.display = emailVal ? 'inline-flex' : 'none';

    if (webVal) {
      dossierWebsiteLink.textContent = domainVal || webVal;
      dossierWebsiteLink.href = webVal;
      dossierWebsiteLink.style.display = 'inline-flex';
    } else {
      dossierWebsiteLink.textContent = '—';
      dossierWebsiteLink.removeAttribute('href');
    }

    dossierAddress.textContent = lead.address || '—';

    // Social Presence Chips
    const igVal = lead.instagram || '';
    const fbVal = lead.facebook || '';
    const liVal = lead.linkedin || '';
    if (igVal || fbVal || liVal) {
      let socHtml = '';
      if (igVal) {
        const igHandle = igVal.replace(/^https?:\/\/(?:www\.)?instagram\.com\//i, '@').replace(/\/$/, '');
        socHtml += `<a href="${escapeHtml(igVal)}" target="_blank" rel="noopener noreferrer" class="social-chip ig-chip">📸 Instagram (${escapeHtml(igHandle)})</a>`;
      }
      if (fbVal) {
        socHtml += `<a href="${escapeHtml(fbVal)}" target="_blank" rel="noopener noreferrer" class="social-chip fb-chip">👥 Facebook Profile</a>`;
      }
      if (liVal) {
        socHtml += `<a href="${escapeHtml(liVal)}" target="_blank" rel="noopener noreferrer" class="social-chip li-chip">💼 LinkedIn Page</a>`;
      }
      dossierSocialsContainer.innerHTML = socHtml;
    } else {
      dossierSocialsContainer.innerHTML = '<span style="color:var(--text-muted);font-size:0.75rem;">No verified social links detected on website.</span>';
    }

    // Reputation Grid
    const rating = lead.review_rating ? Number(lead.review_rating).toFixed(1) : '0.0';
    dossierRating.textContent = `★ ${rating}`;
    dossierReviews.textContent = lead.review_count || 0;
    dossierScoreNum.textContent = score;

    // Reasons list
    const reasons = lead.score_reasons || [];
    if (reasons.length > 0) {
      dossierScoreReasons.innerHTML = reasons.map((r) => `<span class="reason-chip">✔ ${escapeHtml(r)}</span>`).join('');
    } else {
      dossierScoreReasons.innerHTML = '<span class="reason-chip" style="color:var(--text-muted);background:transparent;">Base lead entry</span>';
    }

    // Generate outreach pitch
    updateDossierPitch();

    // Slide open drawer
    dossierDrawer.classList.remove('is-closed');
    drawerBackdrop.classList.remove('is-closed');
    runAnimation(drawerBackdrop, { opacity: [0, 1], duration: 250, ease: 'linear' });
    runAnimation(dossierDrawer, {
      translateX: ['100%', '0%'],
      duration: 380,
      ease: 'outCubic'
    });
  }

  function closeDossier() {
    runAnimation(dossierDrawer, {
      translateX: ['0%', '100%'],
      duration: 280,
      ease: 'inCubic',
      onComplete: () => {
        dossierDrawer.classList.add('is-closed');
      }
    });
    runAnimation(drawerBackdrop, {
      opacity: [1, 0],
      duration: 220,
      ease: 'linear',
      onComplete: () => {
        drawerBackdrop.classList.add('is-closed');
      }
    });
  }

  function updateDossierPitch() {
    if (!activeDossierLead) return;
    const pitch = generateOutreachPitch(activeDossierLead, activePitchType);
    outreachPitchText.value = pitch;

    const phoneVal = activeDossierLead.clean_phone || activeDossierLead.phone || '';
    const digits = getCleanDigitsForWhatsApp(phoneVal);
    if (digits) {
      btnSendPitchWa.href = `https://wa.me/${digits}?text=${encodeURIComponent(pitch)}`;
      btnSendPitchWa.classList.remove('is-disabled');
    } else {
      btnSendPitchWa.href = '#';
      btnSendPitchWa.classList.add('is-disabled');
    }
  }

  function setupDossierEvents() {
    if (btnCloseDossier) {
      btnCloseDossier.addEventListener('click', closeDossier);
    }

    if (drawerBackdrop) {
      drawerBackdrop.addEventListener('click', () => {
        if (dossierDrawer && !dossierDrawer.classList.contains('is-closed')) {
          closeDossier();
        }
      });
    }

    outreachTabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        addMicroBounce(tab);
        outreachTabs.forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        activePitchType = tab.dataset.pitch || 'wa';
        updateDossierPitch();
      });
    });

    if (btnCopyPitch) {
      btnCopyPitch.addEventListener('click', () => {
        addMicroBounce(btnCopyPitch);
        if (!outreachPitchText.value) return;
        navigator.clipboard.writeText(outreachPitchText.value);
        const orig = btnCopyPitch.textContent;
        btnCopyPitch.textContent = 'Copied! ✔';
        showToast('Outreach pitch copied to clipboard!', 'success');
        setTimeout(() => { btnCopyPitch.textContent = orig; }, 1800);
      });
    }

    if (btnCopyDossierPhone) {
      btnCopyDossierPhone.addEventListener('click', () => {
        const p = btnCopyDossierPhone.dataset.copy;
        if (p) {
          navigator.clipboard.writeText(p);
          showToast(`Copied phone: ${p}`, 'success');
        }
      });
    }

    if (btnCopyDossierEmail) {
      btnCopyDossierEmail.addEventListener('click', () => {
        const e = btnCopyDossierEmail.dataset.copy;
        if (e) {
          navigator.clipboard.writeText(e);
          showToast(`Copied email: ${e}`, 'success');
        }
      });
    }
  }

  // ── Floating Bulk Actions Bar ───────────────────────────────────────────────
  function updateBulkActionBar() {
    const count = selectedLeadIndices.size;
    if (bulkSelectedCount) {
      bulkSelectedCount.textContent = count;
    }

    if (count > 0) {
      if (bulkActionBar.classList.contains('is-hidden')) {
        bulkActionBar.classList.remove('is-hidden');
        runAnimation(bulkActionBar, {
          opacity: [0, 1],
          translateY: [40, 0],
          duration: 300,
          ease: 'outBack(1.4)'
        });
      }
    } else {
      if (!bulkActionBar.classList.contains('is-hidden')) {
        runAnimation(bulkActionBar, {
          opacity: [1, 0],
          translateY: [0, 30],
          duration: 200,
          ease: 'inQuad',
          onComplete: () => {
            bulkActionBar.classList.add('is-hidden');
          }
        });
      }
    }
  }

  function setupBulkActionBar() {
    if (checkAllLeads) {
      checkAllLeads.addEventListener('change', () => {
        const isChecked = checkAllLeads.checked;
        const rows = leadsTableBody.querySelectorAll('.lead-row');
        rows.forEach((tr) => {
          const idx = parseInt(tr.dataset.leadIdx, 10);
          const cb = tr.querySelector('.lead-checkbox');
          if (isChecked) {
            selectedLeadIndices.add(idx);
            if (cb) cb.checked = true;
            tr.classList.add('is-selected');
          } else {
            selectedLeadIndices.delete(idx);
            if (cb) cb.checked = false;
            tr.classList.remove('is-selected');
          }
        });
        updateBulkActionBar();
      });
    }

    if (btnBulkClear) {
      btnBulkClear.addEventListener('click', () => {
        selectedLeadIndices.clear();
        if (checkAllLeads) checkAllLeads.checked = false;
        leadsTableBody.querySelectorAll('.lead-checkbox').forEach((cb) => {
          cb.checked = false;
        });
        leadsTableBody.querySelectorAll('.lead-row').forEach((tr) => {
          tr.classList.remove('is-selected');
        });
        updateBulkActionBar();
        showToast('Deselected all leads', 'info');
      });
    }

    if (btnBulkCopyEmails) {
      btnBulkCopyEmails.addEventListener('click', () => {
        addMicroBounce(btnBulkCopyEmails);
        const emails = new Set();
        selectedLeadIndices.forEach((idx) => {
          const l = allLeads[idx];
          if (l && l.emails) {
            l.emails.split(',').forEach((em) => {
              const clean = em.trim();
              if (clean) emails.add(clean);
            });
          }
        });
        if (emails.size === 0) {
          showToast('No email addresses found among selected leads.', 'error');
          return;
        }
        const text = Array.from(emails).join(', ');
        navigator.clipboard.writeText(text);
        showToast(`Copied ${emails.size} email addresses to clipboard! 📋`, 'success');
      });
    }

    if (btnBulkCopyPhones) {
      btnBulkCopyPhones.addEventListener('click', () => {
        addMicroBounce(btnBulkCopyPhones);
        const phones = new Set();
        selectedLeadIndices.forEach((idx) => {
          const l = allLeads[idx];
          const p = l ? (l.clean_phone || l.phone) : '';
          if (p) phones.add(p.trim());
        });
        if (phones.size === 0) {
          showToast('No phone numbers found among selected leads.', 'error');
          return;
        }
        const text = Array.from(phones).join(', ');
        navigator.clipboard.writeText(text);
        showToast(`Copied ${phones.size} phone numbers to clipboard! 📋`, 'success');
      });
    }

    if (btnBulkSyncSupabase) {
      btnBulkSyncSupabase.addEventListener('click', async () => {
        addMicroBounce(btnBulkSyncSupabase);
        const selected = Array.from(selectedLeadIndices).map((i) => allLeads[i]).filter(Boolean);
        if (selected.length === 0) {
          showToast('No leads selected to sync.', 'error');
          return;
        }
        btnBulkSyncSupabase.disabled = true;
        const orig = btnBulkSyncSupabase.innerHTML;
        btnBulkSyncSupabase.innerHTML = '<span>Syncing...</span>';
        try {
          const res = await fetch('/api/supabase/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ leads: selected, job_id: currentJobId })
          });
          const data = await res.json();
          btnBulkSyncSupabase.disabled = false;
          btnBulkSyncSupabase.innerHTML = orig;
          if (data.success) {
            showToast(`✔ Synced ${data.count} selected leads to Supabase table 'leads'!`, 'success');
          } else {
            showToast(`Sync failed: ${data.error || 'Check Supabase status'}`, 'error');
          }
        } catch (err) {
          btnBulkSyncSupabase.disabled = false;
          btnBulkSyncSupabase.innerHTML = orig;
          showToast(`Sync error: ${err.message}`, 'error');
        }
      });
    }

    if (btnBulkExportCsv) {
      btnBulkExportCsv.addEventListener('click', () => {
        addMicroBounce(btnBulkExportCsv);
        const selected = Array.from(selectedLeadIndices).map((i) => allLeads[i]).filter(Boolean);
        if (selected.length === 0) {
          showToast('No leads selected to export.', 'error');
          return;
        }
        downloadSelectedLeadsCsv(selected);
        showToast(`Exported ${selected.length} selected leads to CSV!`, 'success');
      });
    }
  }

  function downloadSelectedLeadsCsv(leads) {
    const fields = ['lead_tier', 'lead_score', 'title', 'phone', 'emails', 'website', 'category', 'address', 'review_rating', 'review_count', 'instagram', 'facebook', 'linkedin'];
    const headerLine = fields.join(',');
    const rows = leads.map((l) => {
      return fields.map((f) => {
        let val = l[f] === undefined || l[f] === null ? '' : String(l[f]);
        if (val.includes('"') || val.includes(',') || val.includes('\n')) {
          val = `"${val.replace(/"/g, '""')}"`;
        }
        return val;
      }).join(',');
    });

    const csvContent = '\uFEFF' + [headerLine, ...rows].join('\r\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', `leadmap_selected_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  // ── Leaflet Interactive Map View ───────────────────────────────────────────
  function setupMapView() {
    if (btnViewTable && btnViewMap) {
      btnViewTable.addEventListener('click', () => {
        addMicroBounce(btnViewTable);
        currentViewMode = 'table';
        btnViewTable.classList.add('active');
        btnViewMap.classList.remove('active');
        leadsMapContainer.classList.add('is-hidden');
        leadsTableContainer.classList.remove('is-hidden');
      });

      btnViewMap.addEventListener('click', () => {
        addMicroBounce(btnViewMap);
        currentViewMode = 'map';
        btnViewMap.classList.add('active');
        btnViewTable.classList.remove('active');
        leadsTableContainer.classList.add('is-hidden');
        leadsMapContainer.classList.remove('is-hidden');
        initOrUpdateMap();
      });
    }
  }

  function initOrUpdateMap() {
    if (typeof window.L === 'undefined') {
      console.warn('Leaflet library is not available');
      return;
    }

    const tileUrl = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';

    if (!leafletMap) {
      leafletMap = window.L.map('leads-map-container', {
        zoomControl: true,
        scrollWheelZoom: true
      }).setView([18.5204, 73.8567], 12);

      leafletTileLayer = window.L.tileLayer(tileUrl, {
        maxZoom: 19,
        attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>'
      }).addTo(leafletMap);

      mapMarkersLayer = window.L.layerGroup().addTo(leafletMap);
    } else if (leafletTileLayer) {
      leafletTileLayer.setUrl(tileUrl);
    }

    setTimeout(() => {
      if (leafletMap) leafletMap.invalidateSize();
    }, 120);

    if (!mapMarkersLayer) return;
    mapMarkersLayer.clearLayers();

    const validMarkers = [];
    allLeads.forEach((lead, idx) => {
      const lat = parseFloat(lead.latitude);
      const lon = parseFloat(lead.longitude);
      if (!isNaN(lat) && !isNaN(lon) && (lat !== 0 || lon !== 0)) {
        const tier = lead.lead_tier || 'COLD';
        const score = lead.lead_score || 0;
        const tierColor = tier === 'HOT' ? '#ef4444' : tier === 'WARM' ? '#f59e0b' : '#3b82f6';
        const tierIcon = tier === 'HOT' ? '🔥' : tier === 'WARM' ? '⚡' : '❄️';
        const tierClass = tier === 'HOT' ? 'tier-hot' : tier === 'WARM' ? 'tier-warm' : 'tier-cold';

        const phoneVal = lead.clean_phone || lead.phone || '';
        const emailVal = lead.emails || '';
        const rating = lead.review_rating ? Number(lead.review_rating).toFixed(1) : null;
        const reviews = lead.review_count || 0;
        const waUrl = buildWhatsAppUrl(phoneVal, lead.title, rating, lead.address);

        const marker = window.L.circleMarker([lat, lon], {
          radius: tier === 'HOT' ? 10 : tier === 'WARM' ? 9 : 8,
          fillColor: tierColor,
          color: '#ffffff',
          weight: 2,
          opacity: 1,
          fillOpacity: 0.92
        });

        const popupHtml = `
          <div style="min-width: 220px; font-family: inherit;">
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.4rem;">
              <span class="tier-badge ${tierClass}" style="font-size: 0.72rem; padding: 0.15rem 0.45rem;">${tierIcon} ${tier} (${score})</span>
              ${rating ? `<span style="font-weight:700; font-size:0.8rem; color:#f59e0b;">★ ${rating} (${reviews})</span>` : ''}
            </div>
            <h4 style="margin: 0 0 0.25rem; font-size: 0.95rem; font-weight: 700; color: var(--text-title);">${escapeHtml(lead.title || 'Unknown Business')}</h4>
            <p style="margin: 0 0 0.5rem; font-size: 0.75rem; color: var(--text-muted);">${escapeHtml(lead.category || '')}</p>
            <div style="font-size: 0.78rem; display: flex; flex-direction: column; gap: 0.25rem; margin-bottom: 0.65rem;">
              ${phoneVal ? `<div>📞 <strong>${escapeHtml(phoneVal)}</strong></div>` : ''}
              ${emailVal ? `<div style="color:var(--accent-cyan);">✉️ ${escapeHtml(emailVal)}</div>` : ''}
            </div>
            <div style="display: flex; gap: 0.35rem; align-items: center;">
              ${waUrl ? `<a href="${waUrl}" target="_blank" rel="noopener noreferrer" class="btn-whatsapp-mini" style="font-size:0.72rem; padding:0.2rem 0.5rem;">💬 WhatsApp</a>` : ''}
              <button type="button" class="btn btn-sm btn-secondary btn-map-dossier" data-lead-idx="${idx}" style="font-size:0.72rem; padding:0.2rem 0.5rem;">📋 Dossier</button>
            </div>
          </div>
        `;

        marker.bindPopup(popupHtml);
        marker.addTo(mapMarkersLayer);
        validMarkers.push(marker);
      }
    });

    if (validMarkers.length > 0) {
      const group = window.L.featureGroup(validMarkers);
      leafletMap.fitBounds(group.getBounds().pad(0.12));
    }

    if (leafletMap) {
      leafletMap.on('popupopen', (e) => {
        const popupNode = e.popup.getElement();
        if (!popupNode) return;
        const btn = popupNode.querySelector('.btn-map-dossier');
        if (btn) {
          btn.addEventListener('click', () => {
            const idx = parseInt(btn.dataset.leadIdx, 10);
            const lead = allLeads[idx];
            if (lead) openDossier(lead);
          });
        }
      });
    }
  }

  // ── Leads Data Table Rendering ─────────────────────────────────────────────
  function renderTable() {
    let filtered = [...allLeads];

    // Filter by tab
    if (activeFilter === 'hot') {
      filtered = filtered.filter((l) => l.lead_tier === 'HOT');
    } else if (activeFilter === 'warm') {
      filtered = filtered.filter((l) => l.lead_tier === 'WARM');
    } else if (activeFilter === 'has_email') {
      filtered = filtered.filter((l) => Boolean(l.emails));
    }

    // Filter by search query
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      filtered = filtered.filter((l) => {
        return (
          (l.title && l.title.toLowerCase().includes(q)) ||
          (l.emails && l.emails.toLowerCase().includes(q)) ||
          (l.phone && l.phone.toLowerCase().includes(q)) ||
          (l.website && l.website.toLowerCase().includes(q)) ||
          (l.category && l.category.toLowerCase().includes(q)) ||
          (l.address && l.address.toLowerCase().includes(q))
        );
      });
    }

    // Sort
    filtered.sort((a, b) => {
      let valA = a[sortField];
      let valB = b[sortField];

      if (sortField === 'score') {
        valA = Number(a.lead_score || 0);
        valB = Number(b.lead_score || 0);
      } else if (sortField === 'rating') {
        valA = Number(a.review_rating || 0);
        valB = Number(b.review_rating || 0);
      } else if (sortField === 'title') {
        valA = (a.title || '').toLowerCase();
        valB = (b.title || '').toLowerCase();
      }

      if (valA < valB) return sortAsc ? -1 : 1;
      if (valA > valB) return sortAsc ? 1 : -1;
      return 0;
    });

    tableShowingText.textContent = `Showing ${filtered.length} of ${allLeads.length} leads`;

    if (filtered.length === 0) {
      if (allLeads.length === 0) {
        leadsTableBody.innerHTML = `
          <tr class="empty-state-row">
            <td colspan="8">
              <div class="empty-state">
                <div class="empty-state-icon">🛡️</div>
                <h3>No New Leads in This Run</h3>
                <p>All extracted businesses were already collected in your previous searches! Increase <strong>Scroll Depth</strong> (e.g. to 10 or 15) or search a specific neighborhood to crawl deeper for new leads.</p>
              </div>
            </td>
          </tr>`;
      } else {
        leadsTableBody.innerHTML = `
          <tr class="empty-state-row">
            <td colspan="8">
              <div class="empty-state">
                <div class="empty-state-icon">🔍</div>
                <h3>No Matching Leads</h3>
                <p>Try adjusting your search query or filter tab.</p>
              </div>
            </td>
          </tr>`;
      }
      return;
    }

    leadsTableBody.innerHTML = filtered.map((lead) => {
      const idx = allLeads.indexOf(lead);
      const tier = lead.lead_tier || 'COLD';
      const score = lead.lead_score || 0;
      const tierClass = tier === 'HOT' ? 'tier-hot' : tier === 'WARM' ? 'tier-warm' : 'tier-cold';
      const tierIcon = tier === 'HOT' ? '🔥' : tier === 'WARM' ? '⚡' : '❄️';
      const isSelected = selectedLeadIndices.has(idx);

      // Phone
      const phoneVal = lead.clean_phone || lead.phone || '';
      const phoneHtml = phoneVal ? `
        <div class="contact-pill">
          <span>📞 ${escapeHtml(phoneVal)}</span>
          <button type="button" class="btn-copy-mini" data-copy="${escapeHtml(phoneVal)}" title="Copy Phone">📋</button>
        </div>` : '<span style="color:var(--text-muted);font-size:0.75rem">—</span>';

      // Emails
      const emailsVal = lead.emails || '';
      const emailHtml = emailsVal ? `
        <div class="contact-pill is-email">
          <span>✉️ ${escapeHtml(emailsVal)}</span>
          <button type="button" class="btn-copy-mini" data-copy="${escapeHtml(emailsVal)}" title="Copy Email">📋</button>
        </div>` : '';

      // Website
      const webVal = lead.website || '';
      const domainVal = lead.domain || (webVal ? webVal.replace(/^https?:\/\//i, '').replace(/^www\./i, '').split('/')[0] : '');
      const websiteHtml = webVal ? `
        <a href="${escapeHtml(webVal)}" target="_blank" rel="noopener noreferrer" class="website-link" title="${escapeHtml(webVal)}">
          <span>${escapeHtml(domainVal || 'Visit Site')}</span>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
        </a>` : '<span style="color:var(--text-muted);font-size:0.75rem">—</span>';

      // Social Profiles (Instagram, Facebook, LinkedIn)
      const igVal = lead.instagram || '';
      const fbVal = lead.facebook || '';
      const liVal = lead.linkedin || '';
      let socialsHtml = '';
      if (igVal || fbVal || liVal) {
        socialsHtml = '<div class="social-chips-row">';
        if (igVal) {
          const igHandle = igVal.replace(/^https?:\/\/(?:www\.)?instagram\.com\//i, '@').replace(/\/$/, '');
          socialsHtml += `<a href="${escapeHtml(igVal)}" target="_blank" rel="noopener noreferrer" class="social-chip ig-chip" title="Instagram: ${escapeHtml(igVal)}">📸 ${escapeHtml(igHandle)}</a>`;
        }
        if (fbVal) {
          socialsHtml += `<a href="${escapeHtml(fbVal)}" target="_blank" rel="noopener noreferrer" class="social-chip fb-chip" title="Facebook: ${escapeHtml(fbVal)}">👥 Facebook</a>`;
        }
        if (liVal) {
          socialsHtml += `<a href="${escapeHtml(liVal)}" target="_blank" rel="noopener noreferrer" class="social-chip li-chip" title="LinkedIn: ${escapeHtml(liVal)}">💼 LinkedIn</a>`;
        }
        socialsHtml += '</div>';
      }

      // Rating
      const rating = lead.review_rating ? Number(lead.review_rating).toFixed(1) : null;
      const reviews = lead.review_count || 0;
      const ratingHtml = rating ? `
        <div class="rating-pill">
          <span>★ ${rating}</span>
          <span class="review-count-text">(${reviews})</span>
        </div>` : '<span style="color:var(--text-muted);font-size:0.75rem">No rating</span>';

      // WhatsApp direct URL
      const waUrl = buildWhatsAppUrl(phoneVal, lead.title, rating, lead.address);
      const waBtnHtml = waUrl ? `
        <a href="${waUrl}" target="_blank" rel="noopener noreferrer" class="btn-whatsapp-mini" onclick="event.stopPropagation()" title="Open 1-Click WhatsApp Chat">
          💬 WhatsApp
        </a>` : '';

      return `
        <tr class="lead-row ${isSelected ? 'is-selected' : ''}" data-lead-idx="${idx}">
          <td class="row-check-cell" onclick="event.stopPropagation()">
            <input type="checkbox" class="lead-checkbox" data-lead-idx="${idx}" ${isSelected ? 'checked' : ''} aria-label="Select lead">
          </td>
          <td>
            <div class="tier-badge ${tierClass}" title="${escapeHtml((lead.score_reasons || []).join(', '))}">
              <span>${tierIcon} ${tier}</span>
              <span class="lead-score-val">${score}</span>
            </div>
          </td>
          <td>
            <div class="company-cell">
              <span class="company-title">${escapeHtml(lead.title || 'Unknown Business')}</span>
              <span class="company-category">${escapeHtml(lead.category || '')}</span>
            </div>
          </td>
          <td>
            <div class="contact-cell">
              ${emailHtml}
              ${phoneHtml}
            </div>
          </td>
          <td>
            <div class="website-cell">
              ${websiteHtml}
              ${socialsHtml}
            </div>
          </td>
          <td>
            ${ratingHtml}
          </td>
          <td>
            <div class="address-cell" title="${escapeHtml(lead.address || '')}">
              ${escapeHtml(lead.address || '—')}
            </div>
          </td>
          <td onclick="event.stopPropagation()">
            <div style="display: flex; align-items: center; gap: 0.35rem; flex-wrap: nowrap;">
              ${waBtnHtml}
              <button type="button" class="btn btn-sm btn-ghost btn-view-dossier" data-lead-idx="${idx}" title="Open Lead Dossier & Pitch">📋 Intel</button>
              ${webVal ? `<a href="${escapeHtml(webVal)}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-ghost" title="Open Website">🌐</a>` : ''}
            </div>
          </td>
        </tr>
      `;
    }).join('');

    // Animate newly rendered table rows cascading into view with stagger
    const rows = leadsTableBody.querySelectorAll('tr:not(.empty-state-row)');
    if (rows.length > 0) {
      runAnimation(Array.from(rows).slice(0, 35), {
        opacity: [0, 1],
        translateY: [12, 0],
        duration: 320,
        delay: (el, i) => Math.min(i * 16, 350),
        ease: 'outCubic'
      });
    }

    // Attach copy events
    leadsTableBody.querySelectorAll('.btn-copy-mini').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const text = btn.dataset.copy;
        if (text) {
          navigator.clipboard.writeText(text);
          addMicroBounce(btn);
          const origText = btn.innerHTML;
          btn.innerHTML = '✔';
          btn.classList.add('copied');
          setTimeout(() => {
            btn.innerHTML = origText;
            btn.classList.remove('copied');
          }, 1500);
          showToast(`Copied: ${text}`, 'success');
        }
      });
    });

    // Row click opens dossier
    leadsTableBody.querySelectorAll('.lead-row').forEach((tr) => {
      tr.addEventListener('click', (e) => {
        if (e.target.closest('a, button, input, .contact-pill, .btn-copy-mini, .btn-whatsapp-mini')) return;
        const idx = parseInt(tr.dataset.leadIdx, 10);
        const lead = allLeads[idx];
        if (lead) openDossier(lead);
      });
    });

    // Individual checkbox click
    leadsTableBody.querySelectorAll('.lead-checkbox').forEach((cb) => {
      cb.addEventListener('change', (e) => {
        e.stopPropagation();
        const idx = parseInt(cb.dataset.leadIdx, 10);
        const row = cb.closest('tr');
        if (cb.checked) {
          selectedLeadIndices.add(idx);
          if (row) row.classList.add('is-selected');
        } else {
          selectedLeadIndices.delete(idx);
          if (row) row.classList.remove('is-selected');
        }
        updateBulkActionBar();
      });
    });

    // Intel button click
    leadsTableBody.querySelectorAll('.btn-view-dossier').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.leadIdx, 10);
        const lead = allLeads[idx];
        if (lead) openDossier(lead);
      });
    });

    // Synchronize Map View if currently active
    if (currentViewMode === 'map') {
      initOrUpdateMap();
    }
  }

  // Setup table filter tabs, search, and sorting
  function setupTableEvents() {
    filterTabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        addMicroBounce(tab);
        filterTabs.forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        activeFilter = tab.dataset.filter || 'all';
        renderTable();
      });
    });

    inputTableSearch.addEventListener('input', (e) => {
      searchQuery = e.target.value.trim();
      renderTable();
    });

    document.querySelectorAll('.data-table th[data-sort]').forEach((th) => {
      th.addEventListener('click', () => {
        addMicroBounce(th);
        const field = th.dataset.sort;
        if (sortField === field) {
          sortAsc = !sortAsc;
        } else {
          sortField = field;
          sortAsc = false;
        }
        renderTable();
      });
    });
  }

  // ── Setup Export Menu ──────────────────────────────────────────────────────
  function setupExportEvents() {
    btnExportDropdown.addEventListener('click', (e) => {
      e.stopPropagation();
      addMicroBounce(btnExportDropdown);
      const isHidden = exportMenu.classList.contains('is-hidden');
      if (isHidden) {
        exportMenu.classList.remove('is-hidden');
        runAnimation(exportMenu, {
          opacity: [0, 1],
          translateY: [-6, 0],
          duration: 200,
          ease: 'outCubic'
        });
      } else {
        exportMenu.classList.add('is-hidden');
      }
    });

    document.addEventListener('click', () => {
      exportMenu.classList.add('is-hidden');
    });

    document.querySelectorAll('.dropdown-item[data-export-fmt]').forEach((item) => {
      item.addEventListener('click', () => {
        if (!currentJobId) {
          showToast('No active leads to export.', 'error');
          return;
        }
        const fmt = item.dataset.exportFmt;
        window.location.href = `/api/export?job_id=${encodeURIComponent(currentJobId)}&format=${encodeURIComponent(fmt)}`;
        showToast(`Exporting ${fmt.toUpperCase()}...`, 'success');
      });
    });
  }

  // ── History Drawer Setup ───────────────────────────────────────────────────
  function setupHistoryDrawer() {
    btnToggleHistory.addEventListener('click', () => {
      addMicroBounce(btnToggleHistory);
      openHistoryDrawer();
    });
    btnCloseDrawer.addEventListener('click', closeHistoryDrawer);
    drawerBackdrop.addEventListener('click', closeHistoryDrawer);
  }

  async function openHistoryDrawer() {
    historyDrawer.classList.remove('is-closed');
    drawerBackdrop.classList.remove('is-closed');

    runAnimation(drawerBackdrop, { opacity: [0, 1], duration: 250, ease: 'linear' });
    runAnimation(historyDrawer, {
      translateX: ['100%', '0%'],
      duration: 380,
      ease: 'outCubic'
    });

    historyList.innerHTML = '<div class="empty-history">Loading archives...</div>';

    try {
      const res = await fetch('/api/jobs');
      const data = await res.json();
      const jobs = data.jobs || [];

      if (jobs.length === 0) {
        historyList.innerHTML = '<div class="empty-history">No past scrapes found.</div>';
        return;
      }

      historyList.innerHTML = jobs.map((j) => {
        const title = j.keyword ? `${j.keyword} (${j.city || 'area'})` : `Job ${j.id.substring(0, 8)}`;
        const total = j.metrics ? j.metrics.total : 0;
        const hot = j.metrics ? j.metrics.hot : 0;

        return `
          <div class="history-item" data-job-id="${j.id}">
            <h4>${escapeHtml(title)}</h4>
            <div class="history-meta">
              <span>${total} leads (${hot} 🔥 hot)</span>
              <span>Status: ${escapeHtml(j.status)}</span>
            </div>
          </div>
        `;
      }).join('');

      const items = historyList.querySelectorAll('.history-item');
      if (items.length > 0) {
        runAnimation(Array.from(items), {
          opacity: [0, 1],
          translateX: [20, 0],
          duration: 300,
          delay: (el, i) => i * 35,
          ease: 'outCubic'
        });
      }

      historyList.querySelectorAll('.history-item').forEach((item) => {
        item.addEventListener('click', () => {
          const jid = item.dataset.jobId;
          loadJobLeads(jid);
          closeHistoryDrawer();
          showToast('Loaded scrape archive!', 'success');
        });
      });

    } catch (err) {
      historyList.innerHTML = '<div class="empty-history">Failed to load history.</div>';
    }
  }

  function closeHistoryDrawer() {
    runAnimation(historyDrawer, {
      translateX: ['0%', '100%'],
      duration: 280,
      ease: 'inCubic',
      onComplete: () => {
        historyDrawer.classList.add('is-closed');
      }
    });
    runAnimation(drawerBackdrop, {
      opacity: [1, 0],
      duration: 220,
      ease: 'linear',
      onComplete: () => {
        drawerBackdrop.classList.add('is-closed');
      }
    });
  }

  // ── Supabase Integration & Modal Setup ─────────────────────────────────────
  function setupSupabaseIntegration() {
    const btnSupabaseStatus = document.getElementById('btn-supabase-status');
    const supabasePillText = document.getElementById('supabase-pill-text');
    const modalSupabase = document.getElementById('modal-supabase');
    const btnCloseSupabaseModal = document.getElementById('btn-close-supabase-modal');
    const formSupabaseConfig = document.getElementById('form-supabase-config');
    const inputSupabaseUrl = document.getElementById('input-supabase-url');
    const inputSupabaseKey = document.getElementById('input-supabase-key');
    const inputSupabaseTable = document.getElementById('input-supabase-table');
    const toggleSupabaseAuto = document.getElementById('toggle-supabase-auto');
    const btnTestSupabase = document.getElementById('btn-test-supabase');
    const testFeedback = document.getElementById('supabase-test-feedback');
    const btnCopySql = document.getElementById('btn-copy-sql');
    const btnSyncSupabase = document.getElementById('btn-sync-supabase');

    const btnDisconnectSupabase = document.getElementById('btn-disconnect-supabase');

    // Check status
    async function checkSupabaseStatus() {
      try {
        const res = await fetch('/api/supabase/status');
        const data = await res.json();
        if (data.connected) {
          btnSupabaseStatus.classList.add('connected');
          supabasePillText.textContent = 'Supabase Connected';
          btnSupabaseStatus.setAttribute('title', 'Default Database: Supabase Cloud (Click to manage)');
        } else {
          btnSupabaseStatus.classList.remove('connected');
          supabasePillText.textContent = 'Local Database';
          btnSupabaseStatus.setAttribute('title', 'Default Database: Local Storage (Click to connect Supabase)');
        }
        return data;
      } catch {
        btnSupabaseStatus.classList.remove('connected');
        supabasePillText.textContent = 'Local Database';
      }
    }

    checkSupabaseStatus();

    // Open Modal with spring animation
    btnSupabaseStatus.addEventListener('click', async () => {
      addMicroBounce(btnSupabaseStatus);
      modalSupabase.classList.remove('is-hidden');
      const card = modalSupabase.querySelector('.modal-card');

      runAnimation(modalSupabase, { opacity: [0, 1], duration: 200, ease: 'linear' });
      runAnimation(card, {
        scale: [0.92, 1],
        translateY: [25, 0],
        opacity: [0, 1],
        duration: 350,
        ease: 'outBack(1.3)'
      });

      testFeedback.classList.add('is-hidden');
      const statusData = await checkSupabaseStatus();
      if (statusData) {
        if (statusData.url) inputSupabaseUrl.value = statusData.url;
        if (statusData.table) inputSupabaseTable.value = statusData.table;
        toggleSupabaseAuto.checked = statusData.auto_sync !== false;
      }
    });

    // Close Modal with smooth shrink
    function closeModal() {
      const card = modalSupabase.querySelector('.modal-card');
      runAnimation(card, {
        scale: [1, 0.94],
        opacity: [1, 0],
        duration: 200,
        ease: 'inQuad',
        onComplete: () => {
          modalSupabase.classList.add('is-hidden');
        }
      });
    }

    btnCloseSupabaseModal.addEventListener('click', closeModal);
    modalSupabase.addEventListener('click', (e) => {
      if (e.target === modalSupabase) closeModal();
    });

    // Safe In-Memory Test Connection Button (Does NOT save to .env)
    btnTestSupabase.addEventListener('click', async () => {
      addMicroBounce(btnTestSupabase);
      const url = inputSupabaseUrl.value.trim();
      const key = inputSupabaseKey.value.trim();
      const table = inputSupabaseTable.value.trim() || 'leads';

      if (!url || !key) {
        testFeedback.className = 'status-alert alert-error';
        testFeedback.textContent = 'Please enter both your Supabase URL and API Key.';
        testFeedback.classList.remove('is-hidden');
        runAnimation(testFeedback, { opacity: [0, 1], translateY: [-8, 0], duration: 250, ease: 'outCubic' });
        return;
      }

      btnTestSupabase.disabled = true;
      btnTestSupabase.textContent = 'Testing in Memory...';

      try {
        const res = await fetch('/api/supabase/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url, key, table })
        });
        const data = await res.json();
        btnTestSupabase.disabled = false;
        btnTestSupabase.textContent = 'Test Connection';

        if (data.success) {
          testFeedback.className = 'status-alert alert-success';
          testFeedback.textContent = '✔ Connection verified! Table "' + table + '" is ready. Click "Save & Connect" to set as default backend database in .env.';
        } else {
          testFeedback.className = 'status-alert alert-error';
          testFeedback.textContent = `✗ ${data.message || 'Connection failed'} (Local DB remains your active default database)`;
        }
        testFeedback.classList.remove('is-hidden');
        runAnimation(testFeedback, { opacity: [0, 1], translateY: [-8, 0], duration: 250, ease: 'outCubic' });
      } catch (err) {
        btnTestSupabase.disabled = false;
        btnTestSupabase.textContent = 'Test Connection';
        testFeedback.className = 'status-alert alert-error';
        testFeedback.textContent = `✗ Error: ${err.message}`;
        testFeedback.classList.remove('is-hidden');
        runAnimation(testFeedback, { opacity: [0, 1], translateY: [-8, 0], duration: 250, ease: 'outCubic' });
      }
    });

    // Form Save (ONLY saves to .env if verification succeeds)
    formSupabaseConfig.addEventListener('submit', async (e) => {
      e.preventDefault();
      const url = inputSupabaseUrl.value.trim();
      const key = inputSupabaseKey.value.trim();
      const table = inputSupabaseTable.value.trim() || 'leads';
      const autoSync = toggleSupabaseAuto.checked;

      btnSaveSupabase.disabled = true;
      btnSaveSupabase.textContent = 'Verifying & Saving...';

      try {
        const res = await fetch('/api/supabase/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url, key, table, auto_sync: autoSync })
        });
        const data = await res.json();
        btnSaveSupabase.disabled = false;
        btnSaveSupabase.textContent = 'Save & Connect';

        if (res.ok && data.success) {
          showToast('Supabase verified & set as default backend database! ✔', 'success');
          closeModal();
          checkSupabaseStatus();
        } else {
          testFeedback.className = 'status-alert alert-error';
          testFeedback.textContent = `✗ ${data.message || 'Verification failed. Not saved to .env.'}`;
          testFeedback.classList.remove('is-hidden');
          runAnimation(testFeedback, { opacity: [0, 1], translateY: [-8, 0], duration: 250, ease: 'outCubic' });
        }
      } catch (err) {
        btnSaveSupabase.disabled = false;
        btnSaveSupabase.textContent = 'Save & Connect';
        showToast(`Save failed: ${err.message}`, 'error');
      }
    });

    // Disconnect & Revert to Local Database
    if (btnDisconnectSupabase) {
      btnDisconnectSupabase.addEventListener('click', async () => {
        addMicroBounce(btnDisconnectSupabase);
        try {
          const res = await fetch('/api/supabase/disconnect', { method: 'POST' });
          const data = await res.json();
          inputSupabaseUrl.value = '';
          inputSupabaseKey.value = '';
          testFeedback.className = 'status-alert alert-success';
          testFeedback.textContent = 'Reverted to Local Database. Cloud credentials removed from .env.';
          testFeedback.classList.remove('is-hidden');
          showToast('Default database reverted to Local DB', 'success');
          checkSupabaseStatus();
        } catch (err) {
          showToast(`Disconnect failed: ${err.message}`, 'error');
        }
      });
    }

    // Copy SQL Schema Button
    btnCopySql.addEventListener('click', async () => {
      addMicroBounce(btnCopySql);
      const sqlSchema = `-- LeadMap Pro Supabase Schema
CREATE TABLE IF NOT EXISTS public.leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    job_id TEXT,
    title TEXT NOT NULL,
    category TEXT,
    clean_phone TEXT,
    phone TEXT,
    emails TEXT,
    website TEXT,
    domain TEXT,
    address TEXT,
    review_rating NUMERIC(3, 2),
    review_count INTEGER DEFAULT 0,
    latitude TEXT,
    longitude TEXT,
    lead_score INTEGER DEFAULT 0,
    lead_tier TEXT DEFAULT 'COLD',
    score_reasons TEXT[] DEFAULT '{}',
    instagram TEXT,
    facebook TEXT,
    linkedin TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_phone_domain ON public.leads (clean_phone, domain)
WHERE clean_phone IS NOT NULL AND clean_phone != '' AND domain IS NOT NULL AND domain != '';
ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow anon read and insert on leads" ON public.leads FOR ALL TO anon, authenticated, service_role USING (true) WITH CHECK (true);`;
      try {
        await navigator.clipboard.writeText(sqlSchema);
        btnCopySql.textContent = 'Copied! ✔';
        showToast('SQL Schema copied to clipboard!', 'success');
        setTimeout(() => { btnCopySql.textContent = 'Copy SQL'; }, 2500);
      } catch {
        showToast('Failed to copy to clipboard', 'error');
      }
    });

    // Toolbar Sync Button
    btnSyncSupabase.addEventListener('click', async () => {
      addMicroBounce(btnSyncSupabase);
      if (!allLeads || allLeads.length === 0) {
        showToast('No leads available to sync.', 'error');
        return;
      }

      btnSyncSupabase.disabled = true;
      const origText = btnSyncSupabase.innerHTML;
      btnSyncSupabase.innerHTML = '<span>Syncing to Supabase...</span>';

      try {
        const res = await fetch('/api/supabase/sync', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_id: currentJobId })
        });
        const data = await res.json();
        btnSyncSupabase.disabled = false;
        btnSyncSupabase.innerHTML = origText;

        if (data.success) {
          showToast(`✔ Synced ${data.count} leads to Supabase table 'leads'!`, 'success');
          addMicroBounce(btnSupabaseStatus);
        } else {
          showToast(`Sync failed: ${data.error}`, 'error');
        }
      } catch (err) {
        btnSyncSupabase.disabled = false;
        btnSyncSupabase.innerHTML = origText;
        showToast(`Sync error: ${err.message}`, 'error');
      }
    });
  }

  // ── Toast Notification System (Spring & Fade) ──────────────────────────────
  function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    runAnimation(toast, {
      opacity: [0, 1],
      translateY: [25, 0],
      scale: [0.92, 1],
      duration: 320,
      ease: 'outBack(1.4)'
    });

    setTimeout(() => {
      runAnimation(toast, {
        opacity: [1, 0],
        translateY: [0, -15],
        scale: [1, 0.94],
        duration: 250,
        ease: 'inQuad',
        onComplete: () => {
          toast.remove();
        }
      });
    }, 3500);
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Launch when DOM is ready
  document.addEventListener('DOMContentLoaded', init);
})();

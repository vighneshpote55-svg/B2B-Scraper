const puppeteer = require('puppeteer-core');
const path = require('path');

const ARTIFACT_DIR = '/home/vighnesh/.gemini/antigravity-ide/brain/3feb6d7f-b8d3-418b-a7d1-0d6d6ff6b7c1';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1440,900']
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  console.log('Navigating to http://localhost:3000...');
  await page.goto('http://localhost:3000', { waitUntil: 'networkidle2' });

  // Wait for table rows to load
  await page.waitForSelector('.lead-row', { timeout: 8000 });
  console.log('Leads loaded successfully in table.');

  // 1. Open Dossier Drawer
  console.log('Opening Dossier Drawer...');
  await page.click('.lead-row:first-child .btn-view-dossier');
  await new Promise(r => setTimeout(r, 700));

  await page.screenshot({
    path: path.join(ARTIFACT_DIR, 'feature_dossier_drawer.png'),
    fullPage: false
  });
  console.log('Captured feature_dossier_drawer.png');

  // 2. Switch to Cold Email Tab
  console.log('Switching to Cold Email pitch tab...');
  await page.click('.outreach-tab-btn[data-pitch="email"]');
  await new Promise(r => setTimeout(r, 400));

  await page.screenshot({
    path: path.join(ARTIFACT_DIR, 'feature_dossier_email.png'),
    fullPage: false
  });
  console.log('Captured feature_dossier_email.png');

  // Close Dossier Drawer
  await page.click('#btn-close-dossier');
  await new Promise(r => setTimeout(r, 500));

  // 3. Multi-Select Bulk Actions Bar
  console.log('Selecting multiple leads for Bulk Actions...');
  const checkboxes = await page.$$('.lead-checkbox');
  if (checkboxes.length >= 3) {
    await checkboxes[0].click();
    await checkboxes[1].click();
    await checkboxes[2].click();
  }
  await new Promise(r => setTimeout(r, 600));

  await page.screenshot({
    path: path.join(ARTIFACT_DIR, 'feature_bulk_actions.png'),
    fullPage: false
  });
  console.log('Captured feature_bulk_actions.png');

  // 4. Interactive Leaflet Map View
  console.log('Switching to Map View...');
  await page.click('#btn-view-map');
  await new Promise(r => setTimeout(r, 4500));

  // Click on a marker to open popup
  const marker = await page.$('.leaflet-interactive');
  if (marker) {
    await marker.click();
    await new Promise(r => setTimeout(r, 800));
  }

  await page.screenshot({
    path: path.join(ARTIFACT_DIR, 'feature_leaflet_map.png'),
    fullPage: false
  });
  console.log('Captured feature_leaflet_map.png');

  await browser.close();
  console.log('Verification finished successfully!');
})();

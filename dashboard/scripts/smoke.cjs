// Headless smoke test of the built dashboard served by the backend.
// Usage: NODE_PATH=$(npm root -g) PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node scripts/smoke.cjs [baseUrl] [screenshotDir]
const { chromium } = require('playwright');
const { mkdirSync } = require('node:fs');

(async () => {

const base = process.argv[2] ?? 'http://localhost:8000';
const shots = process.argv[3] ?? 'docs/screenshots';
mkdirSync(shots, { recursive: true });

const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(`[console] ${page.url()} ${m.text()}`);
});
page.on('pageerror', (e) => errors.push(`[pageerror] ${page.url()} ${e.message}`));
page.on('response', (r) => {
  if (r.status() >= 400 && r.url().includes('/api/')) errors.push(`[http ${r.status()}] ${r.url()}`);
});

const step = async (name, fn) => {
  process.stdout.write(`- ${name} … `);
  await fn();
  console.log('ok');
};

await step('login', async () => {
  await page.goto(base + '/');
  await page.fill('input[name=username]', 'admin');
  await page.fill('input[name=password]', 'admin123');
  await page.click('button[type=submit]');
  await page.waitForSelector('text=Vehicles inside');
});

await step('live', async () => {
  await page.waitForSelector('.tbl img.thumb', { timeout: 10000 });
  await page.waitForSelector('.recharts-bar-rectangle path', { timeout: 10000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${shots}/live.png` });
});

await step('review', async () => {
  await page.click('nav >> text=Review queue');
  await page.waitForSelector('.q-detail');
  await page.keyboard.press('j'); // AMBIGUOUS exit with open-session candidates
  await page.waitForSelector('.cand:has-text("open session")');
  await page.keyboard.press('?');
  await page.waitForSelector('.cheatsheet');
  await page.keyboard.press('?');
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${shots}/review.png` });
  for (const tab of ['Orphan sessions', 'Offline UPI claims', 'Open disputes', 'Unpaid > 30 min']) {
    await page.click(`.tab:has-text("${tab}")`);
    await page.waitForTimeout(150);
  }
  // resolve one event by typing a plate
  await page.click('.tab:has-text("Unread / review events")');
  await page.keyboard.press('/');
  await page.keyboard.type('MH12AB4321');
  await page.keyboard.press('Enter');
  await page.waitForSelector('.toast:has-text("Resolved")', { timeout: 5000 });
});

await step('cash', async () => {
  await page.click('nav >> text=Cash control');
  await page.waitForSelector('text=Live cash in hand');
  await page.waitForSelector('text=Worker comparison');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${shots}/cash.png`, fullPage: true });
  await page.click('button:has-text("Count & confirm")');
  await page.waitForSelector('.denom-grid');
  await page.keyboard.press('Escape');
});

await step('vehicles', async () => {
  await page.click('nav >> text=Vehicle ledger');
  await page.fill('.search input', 'MH43AB1234');
  await page.click('.search button');
  await page.waitForSelector('.vehicle-head', { timeout: 5000 });
  await page.click('.tab:has-text("Ledger")');
});

await step('defaulters', async () => {
  await page.click('nav >> text=Defaulters');
  await page.waitForSelector('text=Regular defaulters');
});

await step('reports', async () => {
  await page.click('nav >> text=Reports');
  await page.waitForSelector('text=Total collected');
  for (const r of ['Cash reconciliation', 'Worker comparison', 'Collections per shift', 'Disputes by worker', 'Sessions with no payment',
    'Passes', 'ANPR accuracy', 'Peak hours', 'Overrides', 'Defaulters', 'UPI reconciliation']) {
    await page.click(`.report-item:has-text("${r}")`);
    await page.waitForTimeout(250);
  }
  await page.click('button:has-text("Run reconciliation")');
  await page.waitForSelector('text=Settlement lines', { timeout: 5000 });
  await page.click('.report-item:has-text("Daily revenue")');
  await page.waitForSelector('.recharts-bar-rectangle path');
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${shots}/reports.png` });
});

await step('config', async () => {
  await page.click('nav >> text=Configuration');
  await page.waitForSelector('text=Plate matching');
  for (const t of ['Gates & cameras', 'Vehicle classes', 'Tariffs', 'Pass types', 'Zones & assignments', 'Users & roles']) {
    await page.click(`.tab:has-text("${t}")`);
    await page.waitForTimeout(300);
  }
  await page.click('.tab:has-text("Tariffs")');
  await page.click('button:has-text("New version")');
  await page.waitForSelector('text=Live preview');
  await page.waitForFunction(() => document.querySelectorAll('.tariff-layout td.num').length > 0 && ![...document.querySelectorAll('.tariff-layout td.num')].some((t) => t.textContent === '…'));
  await page.keyboard.press('Escape');
});

await step('alerts, audit, privacy', async () => {
  await page.click('nav >> text=Alerts');
  await page.waitForSelector('h1:has-text("Alerts")');
  await page.click('nav >> text=Audit log');
  await page.waitForSelector('h1:has-text("Audit log")');
  await page.click('nav >> text=Privacy');
  await page.waitForSelector('h1:has-text("Privacy")');
});

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
const toast = (re) => page.waitForSelector(`.toast.tone-ok:has-text("${re}")`, { timeout: 6000 });

await step('actions: handover, deposit, bank credit', async () => {
  await page.click('nav >> text=Cash control');
  await page.click('button:has-text("Count & confirm")');
  await page.setInputFiles('.modal input[type=file]', { name: 'cash.png', mimeType: 'image/png', buffer: PNG });
  await page.click('.modal-f .btn-primary');
  await toast('Handover confirmed');
  await page.click('button:has-text("Record deposit")');
  await page.fill('.modal input >> nth=1', '10');
  await page.fill('.modal input >> nth=2', 'SLIP-001');
  await page.setInputFiles('.modal input[type=file]', { name: 'slip.png', mimeType: 'image/png', buffer: PNG });
  await page.click('.modal-f .btn-primary');
  await toast('Deposit recorded');
  await page.click('button:has-text("Mark bank credit")');
  await page.click('.modal-f .btn-primary');
  await toast('Bank credit recorded');
});

await step('actions: review claim, dispute, orphan', async () => {
  await page.click('nav >> text=Review queue');
  await page.click('.tab:has-text("Offline UPI claims")');
  await page.keyboard.press('Enter');
  await page.fill('.modal input', 'UTR123456');
  await page.fill('.modal textarea', 'seen on statement');
  await page.click('.modal-f .btn-primary');
  await toast('Payment confirmed');
  await page.click('.tab:has-text("Open disputes")');
  await page.click('button:has-text("Resolve")');
  await page.fill('.modal textarea', 'CCTV shows cash handed');
  await page.check('.modal input[type=checkbox]');
  await page.click('.modal-f .btn-primary');
  await toast('Dispute upheld');
  await page.click('.tab:has-text("Orphan sessions")');
  await page.click('td.actions button:has-text("Charge")');
  await page.fill('.modal textarea', 'left via G2 during outage');
  await page.click('.modal-f .btn-primary');
  await toast('Session charged');
});

await step('actions: adjustment, settings, alert ack, privacy export', async () => {
  await page.click('nav >> text=Vehicle ledger');
  await page.fill('.search input', 'MH14CD5678');
  await page.click('.search button');
  await page.waitForSelector('.vehicle-head');
  await page.click('button:has-text("Adjust balance")');
  await page.fill('.modal input', '-5');
  await page.fill('.modal textarea', 'goodwill');
  await page.click('.modal-f .btn-primary');
  await toast('Adjustment posted');
  await page.click('nav >> text=Configuration');
  const lot = page.locator('.field:has-text("Lot name") input');
  await lot.fill('Kalyan Station Two-wheeler Parking');
  await page.click('button:has-text("Save settings")');
  await toast('Saved 1 setting');
  await page.click('nav >> text=Alerts');
  await page.click('td.actions button:has-text("Acknowledge") >> nth=0');
  await page.fill('.modal textarea', 'guard noted the vehicle');
  await page.click('.modal-f .btn-primary');
  await toast('Alert acknowledged');
  await page.click('nav >> text=Privacy');
  await page.fill('.search input', 'MH14CD5678');
  await page.click('.search button');
  await page.click('.chip-btn >> nth=0');
  const [dl] = await Promise.all([page.waitForEvent('download'), page.click('button:has-text("Download JSON export")')]);
  if (!dl.suggestedFilename().endsWith('.json')) throw new Error('bad export');
  const [x] = await Promise.all([page.waitForEvent('download'), (async () => {
    await page.click('nav >> text=Reports');
    await page.click('button:has-text("Export Excel")');
  })()]);
  if (!x.suggestedFilename().endsWith('.xlsx')) throw new Error('bad xlsx');
});

await step('supervisor role', async () => {
  await page.click('button:has-text("Log out")');
  await page.fill('input[name=username]', 'sup1');
  await page.fill('input[name=password]', 'super123');
  await page.click('button[type=submit]');
  await page.click('nav >> text=Live');
  await page.waitForSelector('text=Vehicles inside');
  if (await page.locator('nav >> text=Audit log').count()) throw new Error('supervisor sees Audit');
  await page.click('nav >> text=Configuration');
  await page.waitForSelector('text=Read-only');
});

await browser.close();
if (errors.length) {
  console.error(`\n${errors.length} error(s):\n` + errors.join('\n'));
  process.exit(1);
}
console.log('\nNo console errors.');
})().catch((e) => {
  console.error(e);
  process.exit(1);
});

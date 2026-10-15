// Optional browser verification: install Playwright and Chromium, then run with Node.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const http = require('http');

(async () => {
  const root = path.resolve(__dirname, '..');
  const out = path.join(root, 'artifacts/research/browser-check');
  fs.mkdirSync(out, { recursive: true });
  const server = http.createServer((req,res) => {
    const file = path.join(root, decodeURIComponent(req.url.split('?')[0]));
    if (!file.startsWith(root + path.sep)) { res.writeHead(403); res.end(); return; }
    fs.readFile(file, (err,data) => {
      if (err) { res.writeHead(404); res.end(); return; }
      res.setHeader('Content-Type', file.endsWith('.png') ? 'image/png' : 'text/html; charset=utf-8');
      res.end(data);
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({
    executablePath: process.env.AUDIT_CHROMIUM || '/usr/bin/chromium',
    headless: true, args: ['--no-sandbox']
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base + '/docs/site/audit.html');
    await page.waitForSelector('#layer option', { state: 'attached' });
    const initialRows = await page.locator('#rows tr').count();
    if (!await page.locator('#mode').textContent()) throw Error('Missing evidence notice');
    const options = await page.locator('#layer option').evaluateAll(xs => xs.map(x => x.value));
    if (options.includes('assume_total_loss:costs_tax')) {
      await page.selectOption('#layer', 'assume_total_loss:costs_tax');
      if (!await page.locator('#note').textContent()) throw Error('Missing scenario explanation');
    }
    await page.selectOption('#view', 'delete_exited_biased');
    if (!(await page.locator('#note').textContent()).includes('WARNING')) throw Error('Missing bias warning');
    await page.selectOption('#view', 'all_histories');
    await page.selectOption('#layer', 'gross');
    const failureCount = await page.locator('#failures li').count();
    const dlPromise = page.waitForEvent('download');
    await page.click('#download');
    const dl = await dlPromise;
    await dl.saveAs(path.join(out, 'downloaded-ledger.json'));
    const ledger = JSON.parse(fs.readFileSync(path.join(out, 'downloaded-ledger.json'), 'utf8'));
    if (!Array.isArray(ledger.rows) || !Array.isArray(ledger.failures)) throw Error('Invalid downloaded ledger');
    await page.screenshot({ path: path.join(out, 'desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    if (overflow) throw Error('Page overflows mobile viewport outside the table scroll container');
    await page.screenshot({ path: path.join(out, 'mobile.png'), fullPage: true });
    await page.goto(base + '/reports/REPRODUCTION_AUDIT.html');
    await page.evaluate(() => Promise.all([...document.images].map(i => i.decode())));
    await page.pdf({ path: path.join(root, 'reports/REPRODUCTION_AUDIT.pdf'), format: 'A4',
      printBackground: true, margin: { top: '15mm', bottom: '15mm', left: '14mm', right: '14mm' } });
    if (errors.length) throw Error(errors.join('\n'));
    const result = { initialRows, displayedFailureRecords: failureCount, downloadableRows: ledger.rows.length,
      downloadableFailures: ledger.failures.length, mobilePageOverflow: overflow, pageErrors: errors,
      verified: ['filters', 'bias warning', 'failure visibility', 'JSON download', 'mobile overflow', 'report PDF'] };
    fs.writeFileSync(path.join(out, 'browser_results.json'), JSON.stringify(result, null, 2));
    process.stdout.write(JSON.stringify(result) + '\n');
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(e => { process.stderr.write(e.stack + '\n'); process.exitCode = 1; });

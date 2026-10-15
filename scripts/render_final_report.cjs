// Verify the generated report in Chromium and render its shareable A4 PDF.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const http = require('http');

(async () => {
  const root = path.resolve(__dirname, '..');
  const out = path.join(root, 'artifacts/research/final-project-report');
  fs.mkdirSync(out, { recursive: true });
  const mime = { '.png': 'image/png', '.svg': 'image/svg+xml', '.pdf': 'application/pdf',
                 '.json': 'application/json', '.csv': 'text/csv' };
  const server = http.createServer((req, res) => {
    const file = path.resolve(root, '.' + decodeURIComponent(req.url.split('?')[0]));
    if (!file.startsWith(root + path.sep)) { res.writeHead(403); res.end(); return; }
    fs.readFile(file, (error, data) => {
      if (error) { res.writeHead(404); res.end(); return; }
      res.setHeader('Content-Type', mime[path.extname(file)] || 'text/html; charset=utf-8');
      res.end(data);
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ executablePath: process.env.AUDIT_CHROMIUM || '/usr/bin/chromium',
                                      headless: true, args: ['--no-sandbox'] });
    const page = await browser.newPage({ viewport: { width: 1200, height: 1100 } });
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/reports/FINAL_PROJECT_REPORT.html`);
    await page.evaluate(() => Promise.all([...document.images].map(img => img.decode())));
    const links = await page.locator('a[href]').evaluateAll(xs => xs.map(a => a.getAttribute('href')));
    const brokenLocalLinks = links.filter(href => {
      if (/^(?:https?:|mailto:|#)/.test(href)) return false;
      const file = path.resolve(root, 'reports', decodeURIComponent(href.split('#')[0]));
      return !file.startsWith(root + path.sep) || !fs.existsSync(file);
    });
    const result = {
      title: await page.title(), tables: await page.locator('table').count(),
      images: await page.locator('img').count(), curatedFigures: await page.locator('figure').count(),
      brokenImages: await page.locator('img').evaluateAll(xs => xs.filter(i => !i.complete || !i.naturalWidth).length),
      brokenLocalLinks, pageErrors,
    };
    if (result.brokenImages || brokenLocalLinks.length || pageErrors.length || result.curatedFigures !== 4) {
      throw Error(JSON.stringify(result));
    }
    await page.screenshot({ path: path.join(out, 'report_preview.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    result.mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    if (result.mobileOverflow) throw Error('Report overflows the mobile viewport');
    await page.screenshot({ path: path.join(out, 'report_mobile.png') });
    await page.setViewportSize({ width: 1200, height: 1100 });
    await page.pdf({ path: path.join(root, 'reports/FINAL_PROJECT_REPORT.pdf'), format: 'A4',
      printBackground: true, displayHeaderFooter: true,
      headerTemplate: '<div></div>',
      footerTemplate: '<div style="font:8px sans-serif;color:#52616b;width:100%;padding:0 14mm;display:flex;justify-content:space-between"><span>India Anomaly Lab · Final project report · 15 October 2026</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
      margin: { top: '15mm', bottom: '18mm', left: '14mm', right: '14mm' } });
    fs.writeFileSync(path.join(out, 'report_render_check.json'), JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

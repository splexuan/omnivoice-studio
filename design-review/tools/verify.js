const { chromium } = require('playwright-core');
(async () => {
  const b = await chromium.launch({ headless: true });
  const p = await b.newPage({ viewport: { width: 1120, height: 1200 }, deviceScaleFactor: 1.5 });
  const errs = [];
  p.on('pageerror', e => errs.push('pageerror: ' + e.message));
  p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  const f = 'file:///C:/Game/omnivoice-studio/design-review/UI%E8%AE%BE%E8%AE%A1%E8%AF%84%E5%AE%A1%E6%8A%A5%E5%91%8A.html';
  await p.goto(f, { waitUntil: 'load' });
  await p.waitForTimeout(1500);
  const img = await p.evaluate(() => {
    const i = [...document.querySelectorAll('img')];
    return i.map(x => ({ ok: x.complete && x.naturalWidth > 0, w: x.naturalWidth, h: x.naturalHeight }));
  });
  await p.screenshot({ path: 'report-top.png', clip: { x: 0, y: 0, width: 1120, height: 1400 } });
  await p.screenshot({ path: 'report-full.png', fullPage: true });
  const h = await p.evaluate(() => document.documentElement.scrollHeight);
  console.log('page height', h);
  console.log('images', JSON.stringify(img));
  console.log('errors', errs.length ? errs : 'none');
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });

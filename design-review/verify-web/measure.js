const { chromium } = require('playwright-core');
(async () => {
  const b = await chromium.launch({ headless: true });
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  await p.goto('http://127.0.0.1:7866/index-v2.html', { waitUntil: 'load' });
  await p.waitForFunction(() => document.querySelector('#c-go'), { timeout: 20000 });
  await p.waitForTimeout(700);
  const r = await p.evaluate(() => {
    const col = document.querySelector('#panel-clone .col-form');
    const kids = [...col.children];
    const rows = kids.map((el) => {
      const cs = getComputedStyle(el);
      const b = el.getBoundingClientRect();
      return {
        tag: el.tagName.toLowerCase(),
        cls: el.className,
        label: (el.querySelector('label')?.textContent || el.textContent || '').trim().slice(0, 16),
        top: Math.round(b.top + scrollY), h: Math.round(b.height),
        mb: cs.marginBottom, mt: cs.marginTop, pt: cs.paddingTop,
      };
    });
    const go = document.querySelector('#c-go').getBoundingClientRect();
    return {
      colTop: Math.round(col.getBoundingClientRect().top + scrollY),
      headerH: Math.round(document.querySelector('header').getBoundingClientRect().height),
      tabsBottom: Math.round(document.querySelector('.tabs').getBoundingClientRect().bottom + scrollY),
      go: { top: Math.round(go.top + scrollY), h: Math.round(go.height) },
      rows,
      textareaMinH: getComputedStyle(document.querySelector('#c-text')).minHeight,
    };
  });
  console.log(JSON.stringify(r, null, 1));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });

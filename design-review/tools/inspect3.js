const { chromium } = require('playwright-core');
const URL = 'http://127.0.0.1:7866';
(async () => {
  const browser = await chromium.launch({ headless: true });
  for (const dark of [false, true]) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1,
      colorScheme: dark ? 'dark' : 'light', locale: 'zh-CN' });
    const page = await ctx.newPage();
    await page.goto(URL, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(2000);
    const out = await page.evaluate(() => {
      const btn = (t) => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === t);
      const st = (el) => { if (!el) return null; const c = getComputedStyle(el); const r = el.getBoundingClientRect();
        return { bg: c.backgroundColor, color: c.color, border: c.borderColor, bw: c.borderWidth,
                 radius: c.borderRadius, h: Math.round(r.height), w: Math.round(r.width),
                 opacity: c.opacity, disabled: el.disabled, cursor: c.cursor }; };
      // 切到音色库看破坏性按钮
      const libTab = [...document.querySelectorAll('[role=tab]')].find(t => t.textContent.trim() === '音色库');
      libTab && libTab.click();
      return new Promise(res => setTimeout(() => res({
        primary: st(btn('生成语音')),
        secondary: st(btn('保存到音色库')),
        refresh: st(btn('刷新列表')),
        delSelected: st(btn('删除所选')),
        rename: st(btn('重命名')),
        tabs: [...document.querySelectorAll('[role=tab]')].map(t => {
          const c = getComputedStyle(t);
          return { t: t.textContent.trim(), selected: t.getAttribute('aria-selected'),
                   color: c.color, fs: c.fontSize, fw: c.fontWeight,
                   bb: c.borderBottomWidth + ' ' + c.borderBottomColor };
        }),
        bodyBg: getComputedStyle(document.body).backgroundColor,
        cardBg: st(document.querySelector('.block.padded')) ,
      }), 1200));
    });
    console.log('\n======== ' + (dark ? 'DARK' : 'LIGHT') + ' ========');
    console.log(JSON.stringify(out, null, 1));
    await ctx.close();
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });

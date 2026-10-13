const { chromium } = require('playwright-core');
const URL = 'http://127.0.0.1:7866';

(async () => {
  const browser = await chromium.launch({ headless: true });
  for (const dark of [false, true]) {
    const ctx = await browser.newContext({
      viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1,
      colorScheme: dark ? 'dark' : 'light', locale: 'zh-CN',
    });
    const page = await ctx.newPage();
    await page.goto(URL, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(2000);

    const out = await page.evaluate(() => {
      const findByText = (txt) => [...document.querySelectorAll('span,label,div')]
        .filter(e => e.children.length === 0 && e.textContent.trim() === txt);
      const info = (el) => {
        const c = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return {
          tag: el.tagName.toLowerCase(),
          cls: (el.className || '').toString().slice(0, 90),
          bg: c.backgroundColor, color: c.color,
          fontSize: c.fontSize, fontWeight: c.fontWeight,
          padding: c.padding, borderRadius: c.borderRadius,
          display: c.display, width: Math.round(r.width), height: Math.round(r.height),
          parentCls: (el.parentElement?.className || '').toString().slice(0, 90),
        };
      };
      const res = {};
      for (const t of ['语种', '要合成的文本', '存为音色', '选择音色', '批量选择（可多选）']) {
        const els = findByText(t);
        res[t] = els.length ? info(els[0]) : { missing: true };
      }
      // 组件外层 block 的内边距与间距
      const blocks = [...document.querySelectorAll('.block')];
      res.__blockSample = blocks.slice(0, 3).map(b => {
        const c = getComputedStyle(b);
        return { cls: b.className.slice(0, 70), padding: c.padding, margin: c.margin,
                 gap: c.gap, borderRadius: c.borderRadius, height: Math.round(b.getBoundingClientRect().height) };
      });
      // 音频组件高度
      const audio = [...document.querySelectorAll('.block')].find(b => b.textContent.includes('合成结果'));
      res.__audioBlock = audio ? { height: Math.round(audio.getBoundingClientRect().height), padding: getComputedStyle(audio).padding } : null;
      // 「重命名为」这一行到 Dataframe 之间的空白
      const rename = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '重命名');
      const df = document.querySelector('.dataframe, table, [data-testid=dataframe]');
      res.__gapRenameToTable = (rename && df) ? Math.round(df.getBoundingClientRect().top - rename.getBoundingClientRect().bottom) : null;
      // 首个组件相对 hero 的纵向位置
      const hero = document.querySelector('.ov-hero');
      const primary = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '生成语音');
      res.__heroTop = hero ? Math.round(hero.getBoundingClientRect().top) : null;
      res.__primaryBtnTop = primary ? Math.round(primary.getBoundingClientRect().top) : null;
      // 页面总高
      res.__pageHeight = document.documentElement.scrollHeight;
      // 标签药丸的背景（找带背景的 label 容器）
      const chip = [...document.querySelectorAll('*')].find(e => {
        const c = getComputedStyle(e);
        return e.textContent.trim() === '语种' && c.backgroundColor !== 'rgba(0, 0, 0, 0)' && c.borderRadius !== '0px';
      });
      res.__chip = chip ? (() => { const c = getComputedStyle(chip);
        return { tag: chip.tagName.toLowerCase(), cls: chip.className.toString().slice(0, 80),
                 bg: c.backgroundColor, color: c.color, borderRadius: c.borderRadius,
                 padding: c.padding, fontSize: c.fontSize }; })() : null;
      return res;
    });
    console.log('\n======== ' + (dark ? 'DARK' : 'LIGHT') + ' ========');
    console.log(JSON.stringify(out, null, 1));
    await ctx.close();
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });

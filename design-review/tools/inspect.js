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

    const data = await page.evaluate(() => {
      const cs = (el) => el ? getComputedStyle(el) : null;
      const pick = (s) => {
        const el = document.querySelector(s);
        if (!el) return { selector: s, missing: true };
        const c = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return {
          selector: s,
          bg: c.backgroundColor, color: c.color,
          fontSize: c.fontSize, fontWeight: c.fontWeight,
          padding: c.padding, borderRadius: c.borderRadius,
          height: Math.round(r.height), width: Math.round(r.width),
          fontFamily: c.fontFamily.slice(0, 60),
        };
      };
      const all = (s) => [...document.querySelectorAll(s)];

      // 主内容容器宽度
      const cont = document.querySelector('.gradio-container');
      // 主行 flex 方向
      const mainRow = document.querySelector('.ov-main-row');
      const labelWraps = all('.label-wrap');
      const primaryBtn = all('button').find(b => b.textContent.trim() === '生成语音');
      const secondaryBtn = all('button').find(b => b.textContent.trim() === '保存到音色库');
      const stopBtn = all('button').find(b => b.textContent.trim() === '删除所选');
      const refreshBtn = all('button').find(b => b.textContent.trim() === '刷新列表');

      const boxes = all('.block');
      const tallBoxes = boxes.map(b => Math.round(b.getBoundingClientRect().height))
        .filter(h => h > 0).sort((a, b) => b - a).slice(0, 6);

      return {
        containerWidth: cont ? Math.round(cont.getBoundingClientRect().width) : null,
        containerMaxWidth: cont ? getComputedStyle(cont).maxWidth : null,
        mainRowFlexDirection: mainRow ? getComputedStyle(mainRow).flexDirection : null,
        mainRowClasses: mainRow ? mainRow.className : null,
        pageBg: getComputedStyle(document.body).backgroundColor,
        h1: pick('h1.ov-title'),
        meta: pick('.ov-meta'),
        metaK: pick('.ov-meta-k'),
        labelCount: labelWraps.length,
        labelWrap: labelWraps[0] ? (() => {
          const c = getComputedStyle(labelWraps[0]);
          return { bg: c.backgroundColor, color: c.color, fontSize: c.fontSize,
                   fontWeight: c.fontWeight, borderRadius: c.borderRadius,
                   padding: c.padding, height: Math.round(labelWraps[0].getBoundingClientRect().height) };
        })() : null,
        labelTexts: labelWraps.slice(0, 8).map(l => l.textContent.trim().slice(0, 24)),
        sect: pick('.ov-sect'),
        hint: pick('.ov-hint'),
        primaryBtn: primaryBtn ? (() => { const c = getComputedStyle(primaryBtn); const r = primaryBtn.getBoundingClientRect();
          return { bg: c.backgroundColor, color: c.color, fontSize: c.fontSize, borderRadius: c.borderRadius,
                   height: Math.round(r.height), width: Math.round(r.width) }; })() : null,
        secondaryBtn: secondaryBtn ? (() => { const c = getComputedStyle(secondaryBtn); const r = secondaryBtn.getBoundingClientRect();
          return { bg: c.backgroundColor, color: c.color, border: c.borderColor, borderWidth: c.borderWidth,
                   disabled: secondaryBtn.disabled, height: Math.round(r.height), width: Math.round(r.width) }; })() : null,
        stopBtn: stopBtn ? (() => { const c = getComputedStyle(stopBtn); const r = stopBtn.getBoundingClientRect();
          return { bg: c.backgroundColor, color: c.color, border: c.borderColor,
                   disabled: stopBtn.disabled, height: Math.round(r.height), width: Math.round(r.width) }; })() : null,
        refreshBtn: refreshBtn ? (() => { const c = getComputedStyle(refreshBtn); const r = refreshBtn.getBoundingClientRect();
          return { bg: c.backgroundColor, color: c.color, border: c.borderColor,
                   height: Math.round(r.height), width: Math.round(r.width) }; })() : null,
        tallestBlocks: tallBoxes,
        inputBox: pick('input'),
        tabActive: (() => { const t = document.querySelector('[role=tab][aria-selected=true]');
          if (!t) return null; const c = getComputedStyle(t);
          return { text: t.textContent.trim(), color: c.color, fontSize: c.fontSize,
                   borderBottom: c.borderBottomColor + ' ' + c.borderBottomWidth }; })(),
        tabInactive: (() => { const t = document.querySelector('[role=tab][aria-selected=false]');
          if (!t) return null; const c = getComputedStyle(t);
          return { text: t.textContent.trim(), color: c.color, fontSize: c.fontSize }; })(),
      };
    });
    console.log('\n================ THEME: ' + (dark ? 'DARK' : 'LIGHT') + ' ================');
    console.log(JSON.stringify(data, null, 1));
    await ctx.close();
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });

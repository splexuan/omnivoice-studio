/* 新 WebUI（FastAPI + 自研前端）实跑验证：
 * 截图 + 关键元素计算样式取样，确认评审报告里的 P0/P1 是否真的解决。
 * 用法: node shoot.js <outDir>
 */
const { chromium } = require('playwright-core');
const path = require('path');

const OUT = process.argv[2] || '.';
const BASE = 'http://127.0.0.1:7866/';
const VW = 1440, VH = 900;

const rgb = (s) => s; // 原样返回，便于人读

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: VW, height: VH }, deviceScaleFactor: 1.5,
  });

  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(BASE, { waitUntil: 'load' });
  // 等 bootstrap 完成（主按钮可用即代表渲染完毕）
  await page.waitForFunction(() => {
    const b = document.querySelector('#c-go');
    return b && document.querySelector('.card');
  }, { timeout: 20000 });
  await page.waitForTimeout(900);

  const shot = async (name) => {
    await page.screenshot({ path: path.join(OUT, name) });
    console.log('shot ->', name);
  };

  /* ---------- 浅色：三个标签页 ---------- */
  await shot('w01-clone-light.png');

  await page.click('#tab-design');
  await page.waitForTimeout(350);
  await shot('w02-design-light.png');

  await page.click('#tab-library');
  await page.waitForTimeout(350);
  await shot('w03-library-light.png');

  /* ---------- 深色 ---------- */
  await page.click('#theme-btn');
  await page.waitForTimeout(350);
  await page.click('#tab-clone');
  await page.waitForTimeout(350);
  await shot('w04-clone-dark.png');
  await page.click('#tab-design');
  await page.waitForTimeout(350);
  await shot('w05-design-dark.png');
  await page.click('#tab-library');
  await page.waitForTimeout(350);
  await shot('w06-library-dark.png');

  /* ---------- 计算样式取样（回到浅色） ---------- */
  await page.click('#theme-btn');
  await page.waitForTimeout(300);
  await page.click('#tab-clone');
  await page.waitForTimeout(300);

  const probe = await page.evaluate(() => {
    const pick = (el) => {
      if (!el) return null;
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return {
        text: (el.textContent || '').trim().slice(0, 22),
        tag: el.tagName.toLowerCase(),
        cls: el.className,
        bg: cs.backgroundColor,
        color: cs.color,
        fs: cs.fontSize, fw: cs.fontWeight,
        radius: cs.borderRadius,
        border: cs.borderTopWidth + ' ' + cs.borderTopColor,
        pad: cs.padding,
        top: Math.round(r.top + scrollY),
        h: Math.round(r.height), w: Math.round(r.width),
      };
    };
    const scrollY = window.scrollY;
    const label = document.querySelector('.field > label, .lbl');
    const allLabels = [...document.querySelectorAll('.field > label, .lbl')].map(pick);
    const go = document.querySelector('#c-go');
    const tab = document.querySelector('.tab[aria-selected="true"]');
    const card = document.querySelector('.card');
    const inp = document.querySelector('.field input, .field textarea, .field select');
    return {
      viewport: { w: innerWidth, h: innerHeight },
      docHeight: document.documentElement.scrollHeight,
      label: pick(label),
      labelsHaveBg: allLabels.filter((l) => l.bg && l.bg !== 'rgba(0, 0, 0, 0)').length,
      labelCount: allLabels.length,
      primaryBtn: pick(go),
      activeTab: pick(tab),
      card: pick(card),
      input: pick(inp),
      radiusVals: {
        base: getComputedStyle(document.documentElement).getPropertyValue('--radius').trim(),
        sm: getComputedStyle(document.documentElement).getPropertyValue('--radius-sm').trim(),
        lg: getComputedStyle(document.documentElement).getPropertyValue('--radius-lg').trim(),
      },
      firstScreenY: innerHeight,
    };
  });

  console.log('\n===== 计算样式取样 =====');
  console.log(JSON.stringify(probe, null, 2));
  console.log('\n===== JS 错误 =====');
  console.log(errors.length ? errors : 'none');

  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });

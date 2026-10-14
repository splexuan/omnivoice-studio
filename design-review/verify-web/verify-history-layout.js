/* 合成记录页的布局诊断 / 验收。
 *
 * 量出每条记录的盒模型、对齐方式与子元素横向位置，用来回答
 * 「这一行为什么看着不齐」这类问题（而不是靠读 CSS 猜）。
 * 同时留浅色截图，便于改前改后对照。
 *
 * 用法: node verify-history-layout.js <outDir> <port>
 */
const { chromium } = require('playwright-core');
const path = require('path');

const OUT = process.argv[2] || '.';
const PORT = process.argv[3] || '7866';
const BASE = `http://127.0.0.1:${PORT}/`;
const log = (...a) => console.log(...a);

const rect = (el) => {
  const b = el.getBoundingClientRect();
  return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) };
};

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 940 }, deviceScaleFactor: 1.5,
  });

  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForFunction(() => !!document.querySelector('#panel-synth'), { timeout: 25000 });
  await page.click('#tab-history');
  await page.waitForTimeout(1600);

  log('=== 容器盒模型 ===');
  const boxes = await page.evaluate(() => {
    const out = {};
    for (const sel of ['.card', '.h-tools', '.h-list', '.hrow']) {
      const el = document.querySelector(sel);
      if (!el) { out[sel] = null; continue; }
      const b = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      out[sel] = {
        x: Math.round(b.x), w: Math.round(b.width), h: Math.round(b.height),
        display: cs.display, justify: cs.justifyContent, padding: cs.padding,
        maxWidth: cs.maxWidth, margin: cs.margin,
      };
    }
    return out;
  });
  for (const [k, v] of Object.entries(boxes)) log(' ', k.padEnd(10), JSON.stringify(v));

  const rows = await page.evaluate(() => {
    const r2 = (el) => {
      const b = el.getBoundingClientRect();
      return { x: Math.round(b.x), w: Math.round(b.width), h: Math.round(b.height) };
    };
    return [...document.querySelectorAll('.hrow')].slice(0, 3).map((r) => {
      const top = r.querySelector('.hrow-top');
      const cs = top ? getComputedStyle(top) : null;
      const pick = (sel) => { const el = r.querySelector(sel); return el ? r2(el) : null; };
      return {
        row: r2(r),
        top: top ? r2(top) : null,
        justify: cs && cs.justifyContent,
        wrap: cs && cs.flexWrap,
        badge: pick('.hrow-top .badge'),
        voice: pick('.hrow-voice'),
        time: pick('.hrow-time'),
        text: pick('.hrow-text'),
        acts: pick('.hrow-acts'),
        listen: pick('[data-role=listen]'),
        reuse: pick('[data-role=reuse]'),
        del: pick('[data-role=del]'),
        topKids: top ? [...top.children].map((c) => ({
          t: c.textContent.trim().replace(/\s+/g, ' ').slice(0, 20),
          x: Math.round(c.getBoundingClientRect().x),
          w: Math.round(c.getBoundingClientRect().width),
        })) : [],
        metaKids: (() => {
          const m = r.querySelector('.hrow-meta');
          if (!m) return null;
          return [...m.children].map((c) => ({
            t: c.textContent.trim().slice(0, 16),
            x: Math.round(c.getBoundingClientRect().x),
            w: Math.round(c.getBoundingClientRect().width),
          }));
        })(),
      };
    });
  });

  log('');
  log('记录条数:', await page.$$eval('.hrow', (rs) => rs.length));
  rows.forEach((r, i) => {
    log(`--- 第 ${i + 1} 条 ---`);
    log('  行盒     :', JSON.stringify(r.row));
    log('  top 行   :', JSON.stringify(r.top), '｜justify=' + r.justify, '｜wrap=' + r.wrap);
    log('  关键 x   : badge=' + (r.badge && r.badge.x),
      ' voice=' + (r.voice && r.voice.x),
      ' time=' + (r.time && r.time.x),
      ' text=' + (r.text && r.text.x));
    log('  操作 x   : acts=' + (r.acts && r.acts.x),
      ' 试听=' + (r.listen && r.listen.x),
      ' 重新生成=' + (r.reuse && r.reuse.x),
      ' 删除=' + (r.del && r.del.x));
    log('  top 子元素:', JSON.stringify(r.topKids));
    log('  meta 列   :', JSON.stringify(r.metaKids));
  });

  /* 汇总结论：徽章与统计列是否真的跨记录对齐 */
  log('');
  log('=== 对齐结论 ===');
  const bxs = rows.map((r) => r.badge && r.badge.x);
  log('  徽章 x  :', JSON.stringify(bxs), '→',
    new Set(bxs).size === 1 ? '✅ 全部对齐' : '❌ 参差不齐');
  const cols = {};
  rows.forEach((r) => (r.metaKids || []).forEach((k, i) => {
    (cols[i] = cols[i] || []).push(k.x);
  }));
  Object.entries(cols).forEach(([i, xs]) => {
    log(`  统计第 ${+i + 1} 列 x :`, JSON.stringify(xs), '→',
      new Set(xs).size === 1 ? '✅ 对齐' : '⚠ 有偏差');
  });

  await page.screenshot({ path: path.join(OUT, 'hl-history-light.png') });
  await page.click('#theme-btn');
  await page.waitForTimeout(450);
  await page.screenshot({ path: path.join(OUT, 'hl-history-dark.png') });
  log('');
  log('截图: hl-history-light.png / hl-history-dark.png');
  log('');
  log('页面错误:', errors.length ? errors.join(' | ') : '(无)');
  await browser.close();
})();

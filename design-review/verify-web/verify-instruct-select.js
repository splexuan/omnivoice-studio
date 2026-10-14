/* 「附加指令」由自由文本改为下拉后的端到端验证。
 *
 *   ① 合成页的 6 个属性下拉（取值来自后端 categories），旧手填框 #s-instruct 应已消失
 *   ② 单个 / 多个选择 → 「将发送的指令」预览是否正确（含中文原样、英文口音规范化）
 *   ③ 中文方言 + 英文口音能否被同时选中（模型侧禁止混用 —— 看前端拦不拦）
 *   ④ 合成记录「重新生成」能否把 instruct 反解回下拉
 *   ⑤ 全程 console / pageerror，并留下关键状态截图
 *
 * 用法: node verify-instruct-select.js <outDir> <port>
 */
const { chromium } = require('playwright-core');
const path = require('path');

const OUT = process.argv[2] || '.';
const PORT = process.argv[3] || '7866';
const BASE = `http://127.0.0.1:${PORT}/`;
const log = (...a) => console.log(...a);

(async () => {
  // 本机没有 playwright 自带 chromium（也未下载 headless shell），改用系统 Chrome。
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 940 }, deviceScaleFactor: 1.5,
  });

  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForFunction(() => !!document.querySelector('#panel-synth'), { timeout: 25000 });
  await page.waitForTimeout(700);

  const preview = () => page.$eval('#s-instruct-preview',
    (e) => e.textContent.replace(/\s+/g, ' ').trim());
  const values = () => page.$$eval('#s-attrs select', (ss) => ss.map((s) => s.value));
  /* 附加指令区在页面底部，截图前先把它滚进视口，否则只会拍到上半屏 */
  const shotInstruct = async (name) => {
    await page.$eval('#s-instruct-preview', (el) => el.scrollIntoView({ block: 'center' }));
    await page.waitForTimeout(250);
    await page.screenshot({ path: path.join(OUT, name) });
    log('  shot ->', name);
  };

  /* ---------- ① 下拉清单 ---------- */
  const sels = await page.$$eval('#s-attrs select', (ss) => ss.map((s) => ({
    label: s.closest('.field')?.querySelector('label')?.textContent.trim() || '(无标签)',
    choices: [...s.options].map((o) => o.value),
    shown: [...s.options].map((o) => o.textContent),
    value: s.value,
  })));

  log('=== ① 属性下拉 ===');
  log('  下拉个数        :', sels.length);
  sels.forEach((s) => log(`    · ${s.label}  (${s.choices.length} 项)  默认=${s.value}`));
  log('  首项显示文本    :', JSON.stringify(sels.map((s) => s.shown[0])));
  log('  旧手填框 #s-instruct 还在吗 :', (await page.$('#s-instruct')) !== null);

  const opened = await page.evaluate(() => {
    let n = 0;
    document.querySelectorAll('details').forEach((d) => {
      if (d.textContent.includes('附加指令') || d.textContent.includes('生成参数')) { d.open = true; n++; }
    });
    return n;
  });
  log('  已展开折叠区数  :', opened);
  await page.waitForTimeout(300);
  await shotInstruct('is1-instruct-open.png');

  const idx = (kw) => sels.findIndex((s) => s.label.includes(kw));
  const pick = async (kw, val) => {
    await page.selectOption(`#s-attr-${idx(kw)}`, val);
    await page.waitForTimeout(200);
    log(`    选「${kw} = ${val}」 → ${await preview()}`);
  };

  /* ---------- ② 选择 → 预览 ---------- */
  log('');
  log('=== ② 选择与预览 ===');
  log('  未选择时:', await preview());
  await pick('性别', '女 Female');
  await pick('中文方言', '东北话');
  log('  当前下拉值      :', JSON.stringify(await values()));
  await shotInstruct('is2-dialect.png');

  /* ---------- ③ 方言 + 口音 混用 ---------- */
  log('');
  log('=== ③ 中文方言 × 英文口音（模型侧禁止混用）===');
  const aIdx = idx('口音');
  const dIdx = idx('中文方言');
  await page.selectOption(`#s-attr-${aIdx}`, sels[aIdx].choices.find((c) => c !== 'Auto'));
  await page.waitForTimeout(250);
  const mixed = await values();
  log('  选中口音后的预览:', await preview());
  log('  两个下拉的值    : 方言=' + mixed[dIdx] + '  口音=' + mixed[aIdx]);
  log('  前端是否阻止混用:', (mixed[dIdx] !== 'Auto' && mixed[aIdx] !== 'Auto')
    ? '否 —— 两个都留着，提交后会被模型拒绝' : '是');
  await shotInstruct('is3-mixed.png');

  // 复原，便于 ④ 断言干净
  for (const i of [aIdx, dIdx, idx('性别')]) {
    await page.selectOption(`#s-attr-${i}`, 'Auto');
  }
  await page.waitForTimeout(200);
  log('  复原后:', await preview());

  /* ---------- ④ 合成记录回填 ---------- */
  log('');
  log('=== ④ 合成记录「重新生成」回填 ===');
  await page.click('#tab-history');
  await page.waitForTimeout(1500);            // 等记录渲染出来
  const items = await page.evaluate(async () => {
    const j = await (await fetch('/api/history')).json();
    return Array.isArray(j) ? j : (j.items || []);
  });
  const k = items.findIndex((it) => (it.instruct || '').trim());
  log('  历史条数        :', items.length, '｜第一条带 instruct 的索引:', k);
  if (k >= 0) log('  该条 instruct   :', JSON.stringify(items[k].instruct));

  const btns = await page.$$('[data-role="reuse"]');
  if (k >= 0 && btns[k]) {
    await btns[k].click();
    await page.waitForTimeout(700);
    log('  点「重新生成」后所在标签页:',
      await page.$eval('.tab[aria-selected="true"]', (t) => t.textContent.trim()));
    log('  回填后下拉值    :', JSON.stringify(await values()));
    log('  回填后预览      :', await preview());
    await shotInstruct('is4-refill.png');
  } else {
    log('  跳过：没有可用的「重新生成」按钮（需先有一条合成记录）');
  }

  /* ---------- ⑤ 页面错误 ---------- */
  log('');
  log('=== ⑤ 页面错误 ===');
  log(errors.length ? errors.map((e) => '  ' + e).join('\n') : '  (无)');

  await browser.close();
})();

/* 「结果卡展示生成参数」+「点击历史记录回显结果」的真实验证。
 *
 * 会**真的跑一次合成**（需要模型；服务端若未预热，首次加载约 2.3 GB，会比较慢）。
 * 之所以要真跑：runJob 的成功分支这一轮被重构成了调用 paintResult，
 * 只靠「点击历史回显」验证不到这条链路（两者入口不同）。
 *
 * 跑完会**清掉本次自造的合成记录与 wav**（只删本次新增的文件，用前后差集判定），
 * 不在 outputs/ 留垃圾。
 *
 * 用法: NODE_PATH=<node_modules> node verify-result-params.js <outDir> <port>
 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');

const OUT = process.argv[2] || '.';
const PORT = process.argv[3] || '7866';
const ROOT = process.cwd();
const BASE = `http://127.0.0.1:${PORT}/`;

const errors = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const hist = (page) => page.evaluate(async () =>
  (await (await fetch('/api/history')).json()).items);

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1512, height: 950 }, deviceScaleFactor: 1.4 });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForFunction(() => {
    const app = document.querySelector('#app');
    const sub = document.querySelector('#brand-sub');
    return app && !app.classList.contains('hidden') && sub && sub.textContent !== 'Studio';
  }, { timeout: 25000 });
  await sleep(600);

  const shot = async (n, full = false) => {
    await page.screenshot({ path: path.join(OUT, n), fullPage: full });
    console.log('shot ->', n);
  };

  /* 记下跑之前的产物，最后只清理「本次新增」的 */
  const fileSet = (items) => items.map((i) => i.file).filter(Boolean);
  const filesBefore = fileSet(await hist(page));

  /* ---------- 选音色（平铺芯片：直接点，不再有下拉 / 弹窗） ---------- */
  const voice = await page.evaluate(() => {
    const c = document.querySelector('#s-voice-chips .vchip');
    if (!c) return null;
    c.click();
    return c.dataset.voice;
  });
  await sleep(500);

  /* ---------- 短文本 + 低步数，尽量跑得快 ---------- */
  await page.fill('#s-text', '参数回显验证。');
  await page.evaluate(() => {
    const ns = document.querySelector('[data-k="num_step"]');
    ns.value = 8; ns.dispatchEvent(new Event('input', { bubbles: true }));
    const sp = document.querySelector('[data-k="speed"]');
    sp.value = 1.15; sp.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await sleep(200);

  console.log('开始合成（音色 =', voice, '，步数 8）…');
  const t0 = Date.now();
  await page.click('#s-go');

  await page.waitForFunction(() => {
    const b = document.querySelector('#s-rail [data-role=badge]');
    return b && /已完成|完成（有提示）|失败/.test(b.textContent);
  }, { timeout: 300000 });
  const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

  const badge = await page.evaluate(() =>
    document.querySelector('#s-rail [data-role=badge]').textContent);
  const paramsText = await page.evaluate(() => {
    const el = document.querySelector('#s-rail [data-role=params]');
    return el && !el.classList.contains('hidden') ? el.innerText.replace(/\n+/g, ' | ') : null;
  });
  await shot('p1-result-params.png');

  console.log('\n合成耗时 ' + elapsed + 's，徽章 =', badge);
  console.log('结果卡参数区 =', paramsText);

  /* ---------- 点击「最近合成」应当回显结果 ---------- */
  await page.click('[data-role=recent] [data-recent]'); await sleep(700);
  const echo = await page.evaluate(() => {
    const rail = document.querySelector('#s-rail');
    const b = rail.querySelector('[data-role=badge]');
    const box = rail.querySelector('[data-role=playerbox]');
    const pm = rail.querySelector('[data-role=params]');
    const audio = rail.querySelector('audio');
    return {
      badge: b.textContent,
      playerShown: !box.classList.contains('hidden'),
      paramsShown: !pm.classList.contains('hidden'),
      params: pm.innerText.replace(/\n+/g, ' | '),
      audioSrc: audio ? audio.getAttribute('src') : null,
      text: document.querySelector('#s-text').value,
      lang: document.querySelector('#s-details select[data-k=language]').value,
      voice: (document.querySelector('#s-voice-chips .vchip.is-on') || {}).dataset
        ? document.querySelector('#s-voice-chips .vchip.is-on').dataset.voice : null,
    };
  });
  await shot('p2-echo-recent.png');

  console.log('\n--- 点击「最近合成」后 ---');
  console.log('徽章 =', echo.badge, '| 播放器显示 =', echo.playerShown,
              '| 参数显示 =', echo.paramsShown);
  console.log('回填文本 =', JSON.stringify(echo.text), '| 回填音色 =', echo.voice,
              '| 回填语种 =', echo.lang);
  console.log('参数 =', echo.params);

  /* ---------- 合成记录的参数行 ---------- */
  await page.click('.navitem[data-view="history"]'); await sleep(700);
  const histInfo = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('.hrow')];
    return {
      rows: rows.length,
      withParams: rows.filter((r) => r.querySelector('.hrow__params')).length,
      firstParams: rows[0]?.querySelector('.hrow__params')?.innerText.replace(/\n+/g, ' | ') || null,
    };
  });
  await shot('p3-history-params.png', true);
  console.log('\n合成记录：共', histInfo.rows, '行，其中带参数行的', histInfo.withParams);
  console.log('首行参数 =', histInfo.firstParams);

  /* ---------- 清理本次自造产物 ---------- */
  const itemsAfter = await hist(page);
  const fresh = itemsAfter.filter((i) => i.file && !filesBefore.includes(i.file));
  console.log('\n本次新增产物:', fresh.map((f) => f.file));
  if (fresh.length) {
    await page.evaluate(async (ids) => {
      await fetch('/api/history/delete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      });
    }, fresh.map((f) => f.id));
    fresh.forEach((f) => {
      const p = path.join(ROOT, 'outputs', f.file);
      if (fs.existsSync(p)) { fs.unlinkSync(p); console.log('已清理', p); }
    });
  }

  console.log('\n===== JS 错误 =====');
  console.log(errors.length ? errors : 'none');
  await browser.close();
  process.exit(errors.length ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(2); });

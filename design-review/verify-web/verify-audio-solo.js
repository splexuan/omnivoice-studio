/* 「同一时刻只播一个音频」验证。
 *
 * 用户反馈：播一个音频，再去别处播另一个会叠加，前一个不停。全站音频只有两个来源
 * （buildPlayer / createEditor 里的 `new Audio()`），都在各自 play 时调 soloMedia() 掐掉上一个。
 * 这个脚本就按「真实用户路径」逐个走一遍，每一步都要求「正在播的元素数 == 1」。
 *
 * 关键点：`new Audio()` 造出来的是**游离节点**（没挂进 DOM），`document.querySelector` 根本
 * 找不到，所以用 addInitScript 把 window.Audio 包一层，把所有实例收进 window.__medias。
 * **不改生产代码**。必须用 addInitScript（页面脚本执行前注入），否则会漏掉启动期建的实例。
 *
 * 无头 Chrome 默认禁止自动播放，所以给 --autoplay-policy=no-user-gesture-required；
 * 否则 play() 直接被拒、连 play 事件都不触发，测的就成了「浏览器策略」而不是我们的逻辑。
 *
 * 用法: NODE_PATH=<node_modules> node verify-audio-solo.js <outDir> <port>
 */
const { chromium } = require('playwright-core');
const path = require('path');

const OUT = process.argv[2] || '.';
const PORT = process.argv[3] || '7866';
const BASE = `http://127.0.0.1:${PORT}/`;

const errors = [];
const fails = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({
    headless: true, channel: 'chrome',
    args: ['--autoplay-policy=no-user-gesture-required'],
  });
  const page = await browser.newPage({ viewport: { width: 1512, height: 950 } });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.addInitScript(() => {
    const Orig = window.Audio;
    window.__medias = [];
    function TrackedAudio(...args) {
      const el = new Orig(...args);
      window.__medias.push(el);
      return el;
    }
    TrackedAudio.prototype = Orig.prototype;
    window.Audio = TrackedAudio;
  });

  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForFunction(() => {
    const app = document.querySelector('#app');
    return app && !app.classList.contains('hidden');
  }, { timeout: 25000 });
  await sleep(700);

  /* 正在播的元素：游离节点只能从 window.__medias 里数 */
  const now = () => page.evaluate(() => {
    const live = window.__medias.filter((a) => !a.paused);
    return {
      playing: live.length,
      src: live.map((a) => (a.currentSrc || '').split('/').pop()),
      total: window.__medias.length,
    };
  });

  let step = 0;
  const check = async (label) => {
    step++;
    const s = await now();
    const ok = s.playing <= 1;
    if (!ok) fails.push(label + ' -> 同时在播 ' + s.playing + ' 条: ' + JSON.stringify(s.src));
    console.log(`  ${ok ? '✓' : '✗'} ${String(step).padStart(2)}. ${label}` +
                `  → 在播 ${s.playing} 条 ${JSON.stringify(s.src)}（累计建过 ${s.total} 个 <audio>）`);
    return s;
  };
  const shot = (n) => page.screenshot({ path: path.join(OUT, n) });

  console.log('=== 逐条播放，检查是否互相掐断 ===');

  /* ---------- 1) 合成页：音色确认卡的播放器 ---------- */
  await page.evaluate(() => {
    const c = document.querySelector('#s-voice-chips .vchip');
    if (c) c.click();
  });
  await sleep(700);
  await page.click('#s-voice-card [data-role=play]'); await sleep(500);
  await check('合成页 · 音色确认卡播放器');

  /* ---------- 2) 音色库：连点两张卡的「试听」 ---------- */
  await page.click('.navitem[data-view="library"]'); await sleep(600);
  await page.locator('.vitem [data-role=play]').nth(0).click(); await sleep(700);
  await check('音色库 · 第 1 张卡「试听」（应把合成页那条掐掉）');

  await page.locator('.vitem [data-role=play]').nth(1).click(); await sleep(700);
  await check('音色库 · 第 2 张卡「试听」');
  await shot('s1-library-second-card.png');

  /* ---------- 3) 裁剪编辑器：详情面板的「裁剪参考音频」 ---------- */
  await page.locator('.vitem__name').nth(0).click(); await sleep(600);
  await page.click('#lb-trim-btn'); await sleep(1200);
  const trimOpen = await page.evaluate(() => document.querySelector('#dlg-trim').open);
  await page.click('#dlg-trim-editor [data-role=play]'); await sleep(500);
  await check('音色库 · 裁剪编辑器「试听选区」（应把库里那条掐掉）');
  await shot('s2-trim-editor.png');

  /* 编辑器里再点一次 = 暂停自己，不能变成 0 条以外的东西 */
  await page.click('#dlg-trim-editor [data-role=play]'); await sleep(400);
  await check('裁剪编辑器 · 再点一次（自己暂停 → 0 条在播）');

  /* ---------- 4) 合成记录：两个不同行的「试听」 ---------- */
  await page.click('#dlg-trim [data-close]'); await sleep(400);
  if (!trimOpen) fails.push('裁剪弹窗没打开');
  await page.click('.navitem[data-view="history"]'); await sleep(1100);
  const listen = page.locator('.hrow [data-role=listen]');
  const nListen = await listen.count();
  if (nListen < 2) {
    fails.push('合成记录可试听的行不足 2 条（' + nListen + '），无法验证叠加');
  } else {
    await listen.nth(0).click(); await sleep(800);
    await check('合成记录 · 第 1 行「试听」');
    await listen.nth(1).click(); await sleep(800);
    await check('合成记录 · 第 2 行「试听」（应把第 1 行掐掉）');
    await shot('s3-history-listen.png', true);

    /* 「收起」也要把自己停掉，否则会出现「看不见的音频还在响」 */
    await listen.nth(1).click(); await sleep(500);
    await check('合成记录 · 再点一次「收起」（应停掉）');
  }

  /* ---------- 5) 回合成页：确认卡再播，记录那条要停 ---------- */
  await page.click('.navitem[data-view="synth"]'); await sleep(500);
  await page.click('#s-voice-card [data-role=play]'); await sleep(500);
  await check('回合成页再播确认卡（应把记录里那条掐掉）');

  /* ---------- 6) 反复横跳 6 次，元素数会涨但「在播」必须恒为 1 ---------- */
  await page.click('.navitem[data-view="library"]'); await sleep(600);
  for (let i = 0; i < 6; i++) {
    await page.locator('.vitem [data-role=play]').nth(i % 2).click();
    await sleep(450);
    const s = await now();
    if (s.playing > 1) {
      fails.push(`横跳第 ${i + 1} 次出现 ${s.playing} 条同时在播`);
      console.log(`  ✗ 横跳第 ${i + 1} 次 → 在播 ${s.playing} 条 ${JSON.stringify(s.src)}`);
    }
  }
  await check('反复切换 6 次后');

  /* 顺带确认：被掐掉的那一方 UI 复位了（播放键回到「播放」、播放头熄灭）——
     只 pause() 而不复位，会出现「没声音但按钮显示在播」 */
  await page.click('.navitem[data-view="synth"]'); await sleep(500);
  const stale = await page.evaluate(() => {
    const host = document.querySelector('#s-voice-card');
    if (!host) return null;
    const btn = host.querySelector('[data-role=play]');
    const head = host.querySelector('[data-role=playhead]');
    return {
      btnPath: btn ? (btn.querySelector('svg path') || {}).getAttribute?.('d') : null,
      headOn: head ? head.classList.contains('on') : null,
    };
  });
  console.log('\n被掐掉一方 UI 复位: ' + JSON.stringify(stale) +
              '（btnPath 应为播放三角 M8 5v14l11-7z，headOn 应为 false）');

  console.log('\n===== JS 错误 =====');
  console.log(errors.length ? errors : 'none');
  console.log('===== 叠加问题 =====');
  console.log(fails.length ? fails : '未发现（每一步都只有 ≤1 条在播）');

  await browser.close();
  process.exit(fails.length || errors.length ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(2); });

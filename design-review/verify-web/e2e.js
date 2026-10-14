/* 端到端界面测试：真实走一遍「填文本 → 选音色 → 生成 → 出结果」，
 * 截图结果态，并校验播放器与指标卡是否出现、有无 JS 报错。 */
const { chromium } = require('playwright-core');
const path = require('path');
const OUT = process.argv[2] || '.';

(async () => {
  const b = await chromium.launch({ headless: true });
  const p = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5 });

  const errors = [];
  p.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  p.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await p.addInitScript(() => {
    window.__rej = [];
    window.addEventListener('unhandledrejection', (e) => window.__rej.push(String(e.reason)));
  });

  await p.goto('http://127.0.0.1:7866/index-v2.html', { waitUntil: 'load' });
  await p.waitForFunction(() => document.querySelector('#c-go'), null, { timeout: 20000 });
  await p.waitForTimeout(800);

  // 1) 填文本
  await p.fill('#c-text', '这是通过全新自研界面的端到端合成测试，用来验证整条链路是否可用。');
  // 2) 从音色库选「刘飞」
  await p.fill('#c-voice', '刘飞');
  await p.waitForTimeout(300);
  await p.click('.combo-list li[role=option]');
  await p.waitForTimeout(400);

  const afterPick = await p.evaluate(() => {
    const d = document.querySelector('#c-drop .drop');
    return {
      dropHasClass: d ? d.classList.contains('has') : null,
      dropText: (document.querySelector('#c-drop [data-role=t]') || {}).textContent,
      dropSub: (document.querySelector('#c-drop [data-role=s]') || {}).textContent,
      saveEnabled: !document.querySelector('#c-save').disabled,
      refTextVal: document.querySelector('#c-reftxt').value,
    };
  });
  console.log('选音色后投放区状态:', JSON.stringify(afterPick, null, 1));

  await p.screenshot({ path: path.join(OUT, 'e2e-1-ready.png') });

  // 3) 点生成
  await p.click('#c-go');
  await p.waitForTimeout(1200);
  await p.screenshot({ path: path.join(OUT, 'e2e-2-running.png') });

  // 4) 等结果
  await p.waitForFunction(() => {
    const badge = document.querySelector('#c-result [data-role=badge]');
    return badge && /完成|失败/.test(badge.textContent);
  }, null, { timeout: 240000 });
  await p.waitForTimeout(1500);

  const result = await p.evaluate(() => {
    const host = document.querySelector('#c-result');
    const box = host.querySelector('[data-role=playerbox]');
    const audio = host.querySelector('audio');
    return {
      badge: host.querySelector('[data-role=badge]').textContent,
      badgeState: host.querySelector('[data-role=badge]').dataset.state,
      alertText: (host.querySelector('[data-role=alerts]') || {}).textContent,
      metrics: [...host.querySelectorAll('.metric')].map((m) => m.textContent.trim()),
      playerVisible: box ? !box.classList.contains('hidden') : false,
      audioSrc: audio ? audio.getAttribute('src') : (host.querySelector('[data-role=dl]') || {}).getAttribute?.('href'),
      dlHref: host.querySelector('[data-role=dl]').getAttribute('href'),
      dlName: host.querySelector('[data-role=dl]').getAttribute('download'),
    };
  });
  console.log('\n结果区状态:', JSON.stringify(result, null, 1));
  await p.screenshot({ path: path.join(OUT, 'e2e-3-done.png') });

  // 整页也抓一张
  await p.screenshot({ path: path.join(OUT, 'e2e-4-done-full.png'), fullPage: true });

  const rej = await p.evaluate(() => window.__rej);
  console.log('\nJS 错误:', errors.length ? errors : 'none');
  console.log('未处理拒绝:', rej.length ? rej : 'none');
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });

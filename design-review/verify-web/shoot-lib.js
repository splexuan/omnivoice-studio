/* 验证「重新转写参考文本」按钮在音色库里的落位与可读性。
 * 用法: node shoot-lib.js <outDir> <port>
 */
const { chromium } = require('playwright-core');
const path = require('path');

const OUT = process.argv[2] || '.';
const PORT = process.argv[3] || '7867';
const BASE = `http://127.0.0.1:${PORT}/index-v2.html`;

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5,
  });

  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForFunction(() => !!document.querySelector('.card'), { timeout: 20000 });
  await page.waitForTimeout(700);

  // 切到音色库
  await page.click('.tab[data-panel="library"]');
  await page.waitForTimeout(400);

  // 选中第一个音色（点行，不点按钮）
  const row = await page.$('tr[data-i="0"]');
  if (row) {
    await row.click({ position: { x: 120, y: 10 } });
    await page.waitForTimeout(500);
  }

  const info = await page.evaluate(() => {
    const btn = document.querySelector('#l-retrans-btn');
    const asr = document.querySelector('#l-asr');
    const txt = document.querySelector('#l-reftext');
    const cs = btn ? getComputedStyle(btn) : null;
    return {
      btnText: btn ? btn.textContent.trim() : null,
      btnDisabled: btn ? btn.disabled : null,
      btnBox: btn ? btn.getBoundingClientRect().toJSON() : null,
      btnStyle: cs ? { color: cs.color, bg: cs.backgroundColor, radius: cs.borderRadius,
                       fontSize: cs.fontSize, cursor: cs.cursor } : null,
      asr: asr ? asr.textContent : null,
      refTextHead: txt ? txt.value.slice(0, 24) : null,
      puncCount: txt ? (txt.value.match(/[，。！？、；：]/g) || []).length : null,
    };
  });

  console.log('--- 音色库详情区 ---');
  console.log(JSON.stringify(info, null, 1));
  console.log('--- 控制台错误 ---', errors.length ? errors : '无');

  await page.screenshot({ path: path.join(OUT, 'lib-retrans-light.png') });
  await page.click('#theme-btn').catch(() => {});
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, 'lib-retrans-dark.png') });

  await browser.close();
})();

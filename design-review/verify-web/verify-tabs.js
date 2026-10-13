/* 三标签页重构后的端到端验证：
 *   ① 语音合成页 = 只有「选音色 → 生成」（无上传、无保存）
 *   ② 语音设计页 = 未改动
 *   ③ 音色库页   = 上传 → 波形裁剪 → 保存（音频 + 转写）
 * 全程记录控制台错误，并留下浅/深色截图。
 *
 * 用法: node verify-tabs.js <outDir> <port> <audioFile>
 */
const { chromium } = require('playwright-core');
const path = require('path');

const OUT = process.argv[2] || '.';
const PORT = process.argv[3] || '7867';
const AUDIO = process.argv[4];
const BASE = `http://127.0.0.1:${PORT}/`;
const TMP = '__ui_check__';

const log = (...a) => console.log(...a);

/* 清理脚本自己造的音色。宿主环境的删除守卫偶发拒绝，所以带重试。 */
const purge = (page) => page.evaluate(async () => {
  const names = (await (await fetch('/api/voices')).json())
    .map((v) => v.name).filter((n) => n.startsWith('__ui_check__'));
  if (!names.length) return { names: [], rounds: 0 };
  let rounds = 0, left = names.length;
  for (let i = 0; i < 4; i++) {
    rounds++;
    const d = await (await fetch('/api/voices/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names }),
    })).json();
    left = (d.failed || []).length;
    if (!left) break;
    await new Promise((r) => setTimeout(r, 500));
  }
  return { names, rounds, left };
});

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 940 }, deviceScaleFactor: 1.5,
  });

  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForFunction(() => !!document.querySelector('#panel-synth'), { timeout: 25000 });
  log('前置清理:', JSON.stringify(await purge(page)));
  await page.reload({ waitUntil: 'load' });
  await page.waitForFunction(() => !!document.querySelector('#panel-synth'), { timeout: 25000 });
  await page.waitForTimeout(600);

  /* ---------- ① 语音合成页 ---------- */
  log('=== ① 语音合成页 ===');
  log('标签:', await page.$$eval('.tab', (ts) => ts.map((t) => t.textContent.trim())));
  log('页面里是否还有上传控件:', await page.$('#panel-synth .drop') !== null);

  const ini = await page.evaluate(() => ({
    goDisabled: document.querySelector('#s-go').disabled,
    hint: document.querySelector('#s-go-hint').textContent,
    card: document.querySelector('#s-voice-card').textContent.trim().slice(0, 40),
  }));
  log('初始状态:', JSON.stringify(ini));

  // 选音色
  await page.click('#s-voice');
  await page.waitForTimeout(250);
  const opts = await page.$$eval('#s-voice-combo .combo-list li', (ls) =>
    ls.map((l) => l.textContent.trim()));
  log('可选音色:', opts);
  await page.click('#s-voice-combo .combo-list li:first-child');
  await page.waitForTimeout(500);

  const afterPick = await page.evaluate(() => {
    const card = document.querySelector('#s-voice-card .vcard');
    return {
      cardShown: !!card,
      name: card ? card.querySelector('.vcard-name').textContent.trim() : null,
      badge: card ? card.querySelector('.badge').textContent.trim() : null,
      hasPlayer: !!(card && card.querySelector('.wpl')),
      text: card ? card.querySelector('.vcard-text').textContent.trim().slice(0, 30) : null,
      goDisabled: document.querySelector('#s-go').disabled,
      hint: document.querySelector('#s-go-hint').textContent,
    };
  });
  log('选完音色:', JSON.stringify(afterPick, null, 1));

  await page.click('#s-text');
  await page.keyboard.type('这是一段用来验证合成页的测试文本。');
  await page.waitForTimeout(200);
  log('输入文本后按钮可用:', !(await page.$eval('#s-go', (b) => b.disabled)),
      '| 提示:', JSON.stringify(await page.$eval('#s-go-hint', (p) => p.textContent)));

  await page.screenshot({ path: path.join(OUT, 't1-synth-light.png'), fullPage: true });
  await page.click('#theme-btn');
  await page.waitForTimeout(450);
  await page.screenshot({ path: path.join(OUT, 't1-synth-dark.png'), fullPage: true });
  await page.click('#theme-btn');
  await page.waitForTimeout(300);

  /* ---------- ② 语音设计页 ---------- */
  log('\n=== ② 语音设计页 ===');
  await page.click('.tab[data-panel="design"]');
  await page.waitForTimeout(400);
  log('属性选择器数量:', await page.$$eval('#d-attrs select', (s) => s.length));
  log('指令预览:', await page.$eval('#d-preview', (e) => e.textContent.trim()));
  await page.screenshot({ path: path.join(OUT, 't2-design-light.png'), fullPage: true });

  /* ---------- ③ 音色库页 ---------- */
  log('\n=== ③ 音色库页 ===');
  await page.click('.tab[data-panel="library"]');
  await page.waitForTimeout(400);
  log('新建卡 / 投放区 / 编辑器(未上传时应无):',
      await page.$eval('#l-new-badge', (e) => e.textContent.trim()),
      '| drop:', await page.$('#l-drop .drop') !== null,
      '| editor:', await page.$('#l-editor .editor') !== null);
  log('列表列头:', await page.$$eval('#l-list thead th', (ts) =>
    ts.map((t) => t.textContent.trim()).filter(Boolean)));
  log('首行:', await page.$$eval('#l-list tbody tr:first-child td', (ts) =>
    ts.map((t) => t.textContent.trim())));

  // 上传
  await page.setInputFiles('#l-drop [data-role=file]', AUDIO);
  await page.waitForFunction(
    () => !!document.querySelector('#l-editor .editor canvas'), { timeout: 20000 });
  await page.waitForTimeout(900);

  const loaded = await page.evaluate(() => {
    const c = document.querySelector('#l-editor canvas');
    return {
      badge: document.querySelector('#l-new-badge').textContent.trim(),
      canvasCss: c.clientWidth + 'x' + c.clientHeight,
      canvasBitmap: c.width + 'x' + c.height,
      head: document.querySelector('#l-editor .ed-head').textContent.replace(/\s+/g, ' ').trim(),
      read: document.querySelector('#l-editor .ed-read').textContent.trim(),
      note: document.querySelector('#l-editor .ed-note').textContent.trim(),
      noteClass: document.querySelector('#l-editor .ed-note').className,
    };
  });
  log('上传后:', JSON.stringify(loaded, null, 1));

  // 用键盘把右端从 16.87s 收 to ~5.1s（Shift+← 每步 0.5s）
  await page.focus('#l-editor [data-role=h1]');
  for (let i = 0; i < 24; i++) await page.keyboard.press('Shift+ArrowLeft');
  await page.waitForTimeout(400);

  const trimmed = await page.evaluate(() => ({
    read: document.querySelector('#l-editor .ed-read').textContent.trim(),
    note: document.querySelector('#l-editor .ed-note').textContent.trim(),
    noteClass: document.querySelector('#l-editor .ed-note').className,
    selLeft: document.querySelector('#l-editor [data-role=sel]').style.left,
    selWidth: document.querySelector('#l-editor [data-role=sel]').style.width,
  }));
  log('裁剪后:', JSON.stringify(trimmed, null, 1));

  // 试转写
  await page.click('#l-new-asr');
  await page.waitForFunction(
    () => document.querySelector('#l-new-text').value.length > 0, { timeout: 30000 });
  await page.waitForTimeout(300);
  log('试转写结果:', JSON.stringify(
    await page.$eval('#l-new-text', (e) => e.value)));
  log('保存前提示:', JSON.stringify(
    await page.$eval('#l-new-alerts', (e) => e.textContent.replace(/\s+/g, ' ').trim())));

  await page.screenshot({ path: path.join(OUT, 't3-library-light.png'), fullPage: true });
  await page.click('#theme-btn');
  await page.waitForTimeout(450);
  await page.screenshot({ path: path.join(OUT, 't3-library-dark.png'), fullPage: true });
  await page.click('#theme-btn');
  await page.waitForTimeout(300);

  // 保存
  await page.fill('#l-new-name', TMP);
  await page.click('#l-new-save');
  await page.waitForFunction(
    () => document.querySelector('#l-new-alerts').textContent.includes('已保存'),
    { timeout: 60000 });
  await page.waitForTimeout(600);
  log('保存结果:', JSON.stringify(
    await page.$eval('#l-new-alerts', (e) => e.textContent.replace(/\s+/g, ' ').trim())));
  log('表单已收起:', await page.$eval('#l-savewrap', (e) => e.classList.contains('hidden')));
  log('新音色在列表里:', await page.$$eval('#l-list tbody tr td',
    (ts, n) => ts.some((t) => t.textContent.trim() === n), TMP));

  // 裁剪弹窗（对着刚建的音色）
  await page.$$eval('#l-list tbody tr', (trs, n) => {
    for (const tr of trs) if (tr.textContent.includes(n)) { tr.click(); return; }
  }, TMP);
  await page.waitForTimeout(500);
  await page.click('#l-trim-btn');
  await page.waitForFunction(
    () => document.querySelector('#dlg-trim').open &&
          !!document.querySelector('#dlg-trim-editor canvas'), { timeout: 20000 });
  await page.waitForTimeout(800);
  log('弹窗已开:', await page.$eval('#dlg-trim', (d) => d.open),
      '| 标题:', await page.$eval('#dlg-trim-title', (e) => e.textContent.trim()),
      '| 读数:', await page.$eval('#dlg-trim-editor .ed-read', (e) => e.textContent.trim()));
  await page.screenshot({ path: path.join(OUT, 't4-trim-dialog-light.png') });

  // 全选时保存应被拦下
  await page.click('#dlg-trim-save');
  await page.waitForTimeout(700);
  log('全选保存被拦:', JSON.stringify(
    await page.$eval('#dlg-trim-alerts', (e) => e.textContent.replace(/\s+/g, ' ').trim())));

  await page.click('#dlg-trim .dlg-foot [data-role=close]');
  await page.waitForTimeout(500);
  log('弹窗已关:', !(await page.$eval('#dlg-trim', (d) => d.open)));

  // 清理
  log('清理临时音色:', JSON.stringify(await purge(page)));

  log('\n=== 控制台错误 ===', errors.length ? errors : '无');
  await browser.close();
})().catch((e) => { console.error('脚本失败:', e); process.exit(1); });

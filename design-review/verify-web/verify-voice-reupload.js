/* 「新建音色默认用文件名」+「已保存音色可重新上传音频」实跑验证
 * （web/static/index.html ← 根路径 `/`，服务端 web/server.py）
 *
 * 这两条都是**界面上才看得出来的行为**，所以只能在真浏览器里验：
 *   A. 默认命名：拖进文件后名称框自动填文件名（去扩展名）；**用户改过就不再被覆盖**；
 *      清空上传 / 重开弹窗都要回到初始状态。
 *   B. 非法字符：名字被后端改动了，必须在界面上说明原因（name_note），
 *      不能让人发现自己打的字悄悄变了。
 *   C. 重新上传音频：复用「新建音色」弹窗但换了身份 —— 标题、按钮、隐藏的命名步骤、
 *      「不可撤销」告警；提交后走的是 /api/voices/{name}/audio，名字不变、时长与文本都换新。
 *   D. 参考音频丢失的音色：裁剪/重转按钮会灰掉，但「重新上传音频」必须仍然可用 ——
 *      那正是它存在的场景，用它真的补一份进去，音色就活了。
 *
 * 自造的测试音色一律以 `zz自测-` 开头，跑完即删（包括磁盘上的残留文件）。
 * 用法: NODE_PATH=<node workspace>/node_modules node verify-voice-reupload.js <outDir> <port>
 */
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');

const OUT = process.argv[2] || '.';
const PORT = process.argv[3] || '7866';
const BASE = `http://127.0.0.1:${PORT}/`;
const VW = 1512, VH = 980;

const ROOT = path.resolve(__dirname, '..', '..');
const VOICES_DIR = path.join(ROOT, 'voices');
const UPLOADS_DIR = path.join(ROOT, 'web', '_uploads');
const PREFIX = 'zz自测-';

// 两段真实音频：16.87s 与另一段（时长明显不同，用来证明「音频真的换掉了」）
const FIX_A = path.join(UPLOADS_DIR, '1dd85189a494.mp3');
const FIX_B = path.join(UPLOADS_DIR, '94cc1775070a.mp3');

// 上传时用的**文件名**（服务端只看内容，名字由我们说了算 —— 正好用来验默认命名）
const NAME_A = `${PREFIX}默认命名.mp3`;
const NAME_B = '另一段参考.mp3';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const errors = [];
function check(name, ok, extra) {
  results.push({ name, ok: !!ok, extra });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra !== undefined && extra !== '' ? '  → ' + extra : ''}`);
}

/** 把上传控件当成「用户拖进来一个叫 name 的文件」 */
function payload(file, name) {
  return { name, mimeType: 'audio/mpeg', buffer: fs.readFileSync(file) };
}

const filesOf = (stem) =>
  fs.readdirSync(VOICES_DIR).filter((n) => path.parse(n).name === stem).sort();

const voicesJson = (stem) => {
  const p = path.join(VOICES_DIR, `${stem}.json`);
  return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : null;
};

(async () => {
  for (const f of [FIX_A, FIX_B]) {
    if (!fs.existsSync(f)) { console.error(`缺少测试素材 ${f}`); process.exit(2); }
  }
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, deviceScaleFactor: 1.4 });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  const shot = async (name, full = true) => {
    await page.screenshot({ path: path.join(OUT, name), fullPage: full });
    console.log('shot ->', name);
  };

  const created = [];                       // 自造的音色名，收尾时删掉
  const nDlg = '#dlg-new';
  const nName = '#n-name';
  const nSave = '#n-save';
  const nFile = '#n-drop [data-role=file]';

  const boot = async () => {
    await page.goto(BASE, { waitUntil: 'load' });
    await page.waitForFunction(() => {
      const sub = document.querySelector('#brand-sub');
      return sub && sub.textContent !== 'Studio' && document.querySelector('#s-go');
    }, { timeout: 30000 });
    await page.click('.navitem[data-view="library"]');
    await sleep(400);
  };

  const dlgState = () => page.evaluate(() => {
    const d = document.querySelector('#dlg-new');
    const warn = document.querySelector('#n-warn');
    const nameField = document.querySelector('#n-name').closest('.field');
    const vis = (el) => !!el && getComputedStyle(el).display !== 'none';
    return {
      open: !!(d && d.open),
      title: document.querySelector('#dlg-new-title').textContent.trim(),
      saveText: document.querySelector('#n-save').textContent.trim(),
      warnVisible: vis(warn),
      warnText: warn.textContent.replace(/\s+/g, ' ').trim(),
      nameFieldVisible: vis(nameField),
      step3Visible: vis(document.querySelector('#n-step3')),
      sep3Visible: vis(document.querySelector('#n-sep3')),
      steps: [...document.querySelectorAll('#n-steps .step')]
        .map((el) => `${el.dataset.step}${el.classList.contains('on') ? '*' : ''}`).join(' '),
      name: document.querySelector('#n-name').value,
      badge: document.querySelector('#n-badge').textContent.trim(),
    };
  });

  const detailState = () => page.evaluate(() => ({
    shown: !document.querySelector('#lb-detail').classList.contains('hidden'),
    name: document.querySelector('#lb-name').textContent.trim(),
    badge: document.querySelector('#lb-badge').textContent.trim(),
    reftext: document.querySelector('#lb-reftext').value,
    note: document.querySelector('#lb-note').textContent.replace(/\s+/g, ' ').trim(),
    reupDisabled: document.querySelector('#lb-reup-btn').disabled,
    trimDisabled: document.querySelector('#lb-trim-btn').disabled,
    retransDisabled: document.querySelector('#lb-retrans-btn').disabled,
  }));

  const openVoice = async (name) => {
    await page.click(`#lb-list .vitem:has(.vitem__name:text-is("${name}"))`);
    await sleep(350);
    return detailState();
  };

  const closeDialog = async () => {
    // [data-close] 有两个（右上角 × 和底部「取消」），必须点第一个
    await page.locator('#dlg-new [data-close]').first().click();
    await sleep(300);
  };

  /** 等徽章变成 expect（解码完成才会变）—— sleep 定长在慢机器上不可靠 */
  const waitBadge = (expect) => page.waitForFunction(
    (v) => document.querySelector('#n-badge').textContent.trim() === v, expect, { timeout: 25000 });

  /** 选文件 → 等解码完 */
  const uploadIn = async (file, name, badge) => {
    await page.setInputFiles(nFile, payload(file, name));
    await waitBadge(badge);
    await sleep(150);
    return dlgState();
  };

  try {
    await boot();

    /* =============================================================
       A. 默认命名
       ============================================================= */
    const newBtnState = await page.evaluate(() => {
      const b = document.querySelector('#lb-new');
      return { text: b.textContent.replace(/\s+/g, ' ').trim(), visible: b.offsetParent !== null };
    });
    check('音色库有「新建音色」入口', newBtnState.visible, newBtnState.text);

    await page.click('#lb-new');
    await sleep(300);
    let d = await dlgState();
    check('打开的是新建模式（标题/按钮/步骤都对）',
      d.open && d.title === '新建音色' && d.saveText === '保存音色'
      && d.nameFieldVisible && d.step3Visible && !d.warnVisible, JSON.stringify(d));

    d = await uploadIn(FIX_A, NAME_A, '待保存');
    check('名称默认取上传文件名（去掉扩展名）', d.name === path.parse(NAME_A).name, d.name);
    check('有名字就直接进到第 3 步', d.steps === '1 2 3*' || d.steps.endsWith('3*'), d.steps);
    check('徽章改口成「待保存」', d.badge === '待保存', d.badge);
    await shot('vu-1-default-name.png', false);

    // 用户亲手改过 —— 再换一个文件也不能把他的名字冲掉
    await page.fill(nName, '我自己起的名字');
    await sleep(150);
    d = await uploadIn(FIX_B, NAME_B, '待保存');
    check('用户改过名字后，再上传新文件也不覆盖', d.name === '我自己起的名字', d.name);
    check('文件名作默认值不影响第 3 步状态', d.steps.endsWith('3*'), d.steps);

    // 移除上传 → 回到初始（名字清空、脏标记归零）
    await page.click('#n-drop [data-role=clear]');
    await sleep(400);
    d = await dlgState();
    check('移除音频后回到第 1 步且清空名字',
      d.name === '' && d.badge === '待上传' && d.steps === '1* 2 3', `${d.steps} / ${d.name}`);

    // 重开弹窗 → 默认命名重新生效（脏标记已归零）
    await closeDialog();
    await page.click('#lb-new');
    await sleep(300);
    d = await uploadIn(FIX_A, NAME_A, '待保存');
    check('重开弹窗后默认命名依然生效', d.name === path.parse(NAME_A).name, d.name);

    /* =============================================================
       A2. 直接保存 —— 顺带拿到一个替换用的目标音色
       ============================================================= */
    const V_MAIN = path.parse(NAME_A).name;          // zz自测-默认命名
    await page.click(nSave);
    await page.waitForFunction(() => !document.querySelector('#dlg-new').open, { timeout: 60000 });
    await sleep(600);
    created.push(V_MAIN);
    let det = await detailState();
    check('保存后自动选中新音色并显示详情',
      det.shown && det.name === V_MAIN, `${det.name} / ${det.badge}`);
    check('新音色的参考文本已落盘（同步保存）', det.reftext.trim().length > 0, det.reftext.slice(0, 24));
    const dur1 = det.badge;
    check('落盘文件只有一份音频 + 一个 json',
      filesOf(V_MAIN).every((n) => ['.json', '.mp3', '.wav'].includes(path.extname(n)))
      && filesOf(V_MAIN).filter((n) => ['.mp3', '.wav'].includes(path.extname(n))).length === 1,
      filesOf(V_MAIN).join(','));

    /* =============================================================
       B. 非法字符：改了名字必须说明原因
       ============================================================= */
    const typed = `${PREFIX}非法:名*称`;
    const sanitized = `${PREFIX}非法_名_称`;
    await page.click('#lb-new');
    await sleep(250);
    await uploadIn(FIX_B, NAME_B, '待保存');   // 这里也会先填上文件名默认值
    await page.fill(nName, typed);             // 再手工改成带非法字符的名字
    await sleep(150);
    await page.waitForFunction(() => !document.querySelector('#n-save').disabled);
    await page.click(nSave);
    await page.waitForFunction(() => !document.querySelector('#dlg-new').open, { timeout: 60000 });
    await sleep(600);
    created.push(sanitized);
    det = await detailState();
    check('非法字符被净化后落盘', det.name === sanitized, `${typed} → ${det.name}`);
    check('界面说明了名字被改的原因（name_note）',
      det.note.includes('特殊字符') && det.note.includes('下划线'), det.note.slice(0, 80));
    const toastTxt = await page.evaluate(() =>
      [...document.querySelectorAll('#toasts .toast')].map((t) => t.textContent).join(' | '));
    check('toast 也提示了名称被调整', toastTxt.includes('名称已自动调整'), toastTxt.slice(0, 60));
    await shot('vu-2-name-note.png', false);

    /* =============================================================
       C. 重新上传音频
       ============================================================= */
    det = await openVoice(V_MAIN);
    check('选中音色后详情栏出现（准备替换）', det.shown && det.name === V_MAIN, det.name);
    check('「重新上传音频」按钮可用', det.reupDisabled === false);

    await page.click('#lb-reup-btn');
    await sleep(350);
    d = await dlgState();
    check('替换模式标题带上了音色名', d.open && d.title === `重新上传音频 · ${V_MAIN}`, d.title);
    check('替换模式按钮改口成「替换音频」', d.saveText === '替换音频', d.saveText);
    check('替换模式藏掉了「命名保存」这一步',
      !d.nameFieldVisible && !d.step3Visible && !d.sep3Visible,
      `nameField=${d.nameFieldVisible} step3=${d.step3Visible} sep3=${d.sep3Visible}`);
    check('替换模式给出了「不可撤销」告警',
      d.warnVisible && d.warnText.includes('不可撤销') && d.warnText.includes('作废')
      && d.warnText.includes(V_MAIN), d.warnText.slice(0, 90));
    await shot('vu-3-replace-dialog.png', false);

    d = await uploadIn(FIX_B, NAME_B, '待替换');
    check('替换模式不会拿文件名去填名字（名字这一栏根本不存在）', d.name === '', JSON.stringify(d.name));
    check('替换模式的徽章是「待替换」', d.badge === '待替换', d.badge);

    await page.click(nSave);
    await page.waitForFunction(() => !document.querySelector('#dlg-new').open, { timeout: 60000 });
    await sleep(700);
    det = await detailState();
    check('替换后名字不变（没有另起一个新音色）', det.name === V_MAIN, det.name);
    check('详情栏说明了原音频已作废', det.note.includes('作废'), det.note.slice(0, 90));
    check('时长换成新音频的（16.87s → 约 7.5s）',
      det.badge !== dur1 && Math.abs(parseFloat(det.badge) - 7.49) < 0.4, `${dur1} → ${det.badge}`);
    check('参考文本跟着新音频更新了', det.reftext.trim().length > 0, det.reftext.slice(0, 24));
    check('新音频已生效（列表里该音色仍在，没多出重名项）',
      (await page.locator(`#lb-list .vitem:has(.vitem__name:text-is("${V_MAIN}"))`).count()) === 1);
    const audios = filesOf(V_MAIN).filter((n) => ['.mp3', '.wav'].includes(path.extname(n)));
    check('磁盘上旧音频被清掉、只剩新的一份', audios.length === 1, filesOf(V_MAIN).join(','));
    check('json 里的 ref_audio 指向新落盘的那一份',
      !!voicesJson(V_MAIN).ref_audio && path.basename(voicesJson(V_MAIN).ref_audio) === audios[0],
      `${voicesJson(V_MAIN).ref_audio} vs ${audios[0]}`);
    await shot('vu-4-after-replace.png');

    // 关掉再打开「新建音色」—— 必须回到干净的新建模式（不能被上一轮替换污染）
    await page.click('#lb-new');
    await sleep(300);
    d = await dlgState();
    check('再开「新建音色」已恢复干净（标题/按钮/步骤/无告警）',
      d.title === '新建音色' && d.saveText === '保存音色' && d.nameFieldVisible
      && d.step3Visible && !d.warnVisible && d.name === '', JSON.stringify(d));
    await closeDialog();

    /* =============================================================
       D. 参考音频丢失的音色：只能靠「重新上传音频」救
       ============================================================= */
    const V_LOST = sanitized;
    const jsonPath = path.join(VOICES_DIR, `${V_LOST}.json`);
    const meta = voicesJson(V_LOST);
    const audio = meta.ref_audio;
    // 把音频挪走 + 抹掉 json 里的 ref_audio —— 正是「参考音频丢了」这个状态
    fs.renameSync(audio, audio + '.lost');
    fs.writeFileSync(jsonPath, JSON.stringify({ ...meta, ref_audio: '' }, null, 2), 'utf8');

    await boot();                                    // 重新加载，让前端读到新状态
    det = await openVoice(V_LOST);
    check('音频丢失的音色：裁剪/重转都灰掉',
      det.trimDisabled === true && det.retransDisabled === true,
      `trim=${det.trimDisabled} retrans=${det.retransDisabled}`);
    check('但「重新上传音频」仍然可用（这正是它的场景）', det.reupDisabled === false);
    const lostHint = await page.evaluate(() =>
      document.querySelector('#lb-refplay').textContent.replace(/\s+/g, ' ').trim());
    check('详情栏给出了「音频已丢失」的引导', lostHint.includes('已丢失') && lostHint.includes('重新上传音频'),
      lostHint.slice(0, 70));

    await page.click('#lb-reup-btn');
    await sleep(300);
    await uploadIn(FIX_A, NAME_A, '待替换');
    await page.click(nSave);
    await page.waitForFunction(() => !document.querySelector('#dlg-new').open, { timeout: 60000 });
    await sleep(700);
    det = await detailState();
    check('补上音频后音色就地复活（名字不变、有参考文本）',
      det.name === V_LOST && det.reftext.trim().length > 0, `${det.name} / ${det.badge}`);
    check('磁盘上重新出现了音频文件',
      filesOf(V_LOST).some((n) => ['.mp3', '.wav'].includes(path.extname(n))),
      filesOf(V_LOST).join(','));
    check('json 里的 ref_audio 已指回真实文件',
      !!voicesJson(V_LOST).ref_audio && fs.existsSync(voicesJson(V_LOST).ref_audio),
      voicesJson(V_LOST).ref_audio);
    await shot('vu-5-lost-audio-fixed.png');

    // 收尾：把挪走的那个孤儿音频也清掉
    if (fs.existsSync(audio + '.lost')) fs.unlinkSync(audio + '.lost');

  } catch (e) {
    console.error('脚本异常:', e);
    errors.push('script: ' + e.message);
  } finally {
    // ---------------------------------------------------------------- 清理
    try {
      await page.evaluate(async (names) => {
        await fetch('/api/voices/delete', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ names }),
        });
      }, created);
    } catch (e) { console.error('清理请求失败:', e.message); }
    await sleep(400);
    const left = fs.readdirSync(VOICES_DIR).filter((n) => n.startsWith(PREFIX) || n.startsWith(PREFIX + '_'));
    check('自造音色已清理干净（含磁盘残留）', left.length === 0, left.join(','));
    for (const n of left) {                          // 万一没删掉，也别把垃圾留给用户
      try { fs.unlinkSync(path.join(VOICES_DIR, n)); } catch (_) {}
    }

    console.log('\nJS 错误:', errors.length ? errors.join(' | ') : 'none');
    const failed = results.filter((r) => !r.ok);
    console.log(`\n合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`);
    if (failed.length) {
      console.log('失败项：\n' + failed.map((f) => '  · ' + f.name + (f.extra ? ' → ' + f.extra : '')).join('\n'));
    }
    await browser.close();
    process.exit(failed.length || errors.length ? 1 : 0);
  }
})().catch((e) => { console.error('脚本异常:', e); process.exit(2); });

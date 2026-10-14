/* 新版界面（web/static/index-v2.html）实跑验证
 * 目标：真开一次页面，确认 bootstrap 成功、四个页面都能渲染、浅深主题都正常、
 *       主要交互（音色/语种选择弹窗、新建音色弹窗、切页、环境抽屉）真的能用，
 *       且无 console/pageerror。
 * 用法: NODE_PATH=<node_modules> node verify-redesign.js <outDir> <port>
 */
const { chromium } = require('playwright-core');
const path = require('path');

const OUT = process.argv[2] || '.';
const PORT = process.argv[3] || '7866';
const BASE = `http://127.0.0.1:${PORT}/`;
const VW = 1512, VH = 950;

const errors = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const page = await browser.newPage({
    viewport: { width: VW, height: VH }, deviceScaleFactor: 1.4,
  });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(BASE, { waitUntil: 'load' });

  // 等 bootstrap 完成：品牌副标题不再停在占位值，且主按钮已在
  await page.waitForFunction(() => {
    const app = document.querySelector('#app');
    const sub = document.querySelector('#brand-sub');
    return app && !app.classList.contains('hidden') && sub && sub.textContent !== 'Studio';
  }, { timeout: 25000 });
  await sleep(700);

  const shot = async (name, full = true) => {
    await page.screenshot({ path: path.join(OUT, name), fullPage: full });
    console.log('shot ->', name);
  };

  /* ---------- 浅色：四页 ---------- */
  await shot('r1-synth-light.png');

  await page.click('.navitem[data-view="design"]');   await sleep(350);
  await shot('r2-design-light.png');

  await page.click('.navitem[data-view="library"]');  await sleep(350);
  await shot('r3-library-light.png');

  await page.click('.navitem[data-view="history"]');  await sleep(500);
  await shot('r4-history-light.png');

  /* ---------- 导航高亮：切页后当前项必须仍然被标出来 ----------
     这条断言是补的：曾经 gotoView 把 aria-current 写成 "true"（条件字符串化），
     而样式选的是 [aria-current="page"] —— 首屏正常、一切页高亮就没了。
     截图上看不出来，因为鼠标刚好悬停在刚点的项上，hover 底色冒充了高亮。 */
  const navHi = [];
  let navHiBad = 0;
  for (const v of ['synth', 'design', 'library', 'history', 'api', 'synth']) {
    await page.click(`.navitem[data-view="${v}"]`); await sleep(320);
    const r = await page.evaluate(() => {
      const items = [...document.querySelectorAll('.navitem')];
      const on = items.filter((x) => x.getAttribute('aria-current') === 'page');
      const off = items.find((x) => x.getAttribute('aria-current') !== 'page');
      return {
        marked: on.length,
        active: on[0] ? on[0].dataset.view : null,
        bg: on[0] ? getComputedStyle(on[0]).backgroundColor : null,
        plainBg: off ? getComputedStyle(off).backgroundColor : null,
      };
    });
    const ok = r.marked === 1 && r.active === v && r.bg !== r.plainBg;
    if (!ok) navHiBad++;
    navHi.push(`  ${ok ? '✓' : '✗'} ${v} -> ${JSON.stringify(r)}`);
  }
  console.log('\n导航高亮（marked 必须恒为 1，active 必须等于所点项，bg ≠ plainBg）:');
  console.log(navHi.join('\n'));
  console.log(navHiBad ? `>>> 导航高亮有 ${navHiBad} 项不通过` : '>>> 导航高亮全部通过');

  /* ---------- 交互：音色芯片行（不再是下拉 + 弹窗） ---------- */
  await page.click('.navitem[data-view="synth"]'); await sleep(300);
  await page.fill('#s-text', '这是一段用来验证重做后合成页的测试文本。');

  const noneSelectedYet = await page.evaluate(() => ({
    chips: document.querySelectorAll('#s-voice-chips .vchip').length,
    on: document.querySelectorAll('#s-voice-chips .vchip.is-on').length,
    card: document.querySelector('#s-voice-card').innerText.trim(),
    goDisabled: document.querySelector('#s-go').disabled,
    hint: document.querySelector('#s-go-hint').textContent,
  }));
  await shot('r5-voice-chips.png', false);

  const pickedVoice = await page.evaluate(() => {
    const c = document.querySelector('#s-voice-chips .vchip');
    if (!c) return null;
    c.click();
    return c.dataset.voice;
  });
  await sleep(700);
  const chipAfter = await page.evaluate(() => {
    const on = document.querySelector('#s-voice-chips .vchip.is-on');
    return {
      onCount: document.querySelectorAll('#s-voice-chips .vchip.is-on').length,
      on: on ? on.dataset.voice : null,
      ariaPressed: on ? on.getAttribute('aria-pressed') : null,
      cardText: document.querySelector('#s-voice-card').innerText.replace(/\n+/g, ' | ').slice(0, 70),
      cardHasWave: !!document.querySelector('#s-voice-card canvas'),
      goDisabled: document.querySelector('#s-go').disabled,
      noLeftoverDialog: !document.querySelector('#dlg-pick'),
    };
  });
  await shot('r6-voice-picked.png', false);

  // 换一个音色：必须只有一个芯片处于选中态
  await page.evaluate(() => {
    const cs = document.querySelectorAll('#s-voice-chips .vchip');
    if (cs[1]) cs[1].click();
  });
  await sleep(600);
  const switched = await page.evaluate(() => {
    const on = [...document.querySelectorAll('#s-voice-chips .vchip.is-on')];
    return { onCount: on.length, on: on[0] ? on[0].dataset.voice : null };
  });

  /* ---------- 交互：语种（搬进「生成参数」的原生 select） ---------- */
  const langProbe = await page.evaluate(() => {
    const sel = document.querySelector('#s-details select[data-k=language]');
    const fold = document.querySelector('#s-details details');
    return {
      inSettingsFold: !!fold && fold.contains(sel),
      options: sel ? sel.options.length : 0,
      value: sel ? sel.value : null,
      label: sel && sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].textContent : null,
      // 主区不应再有自己的语种卡片/控件
      strayInStack: !!document.querySelector('#view-synth .stack #s-lang-combo'),
      cardTitles: [...document.querySelectorAll('#view-synth .stack > .card .card__head h2')]
        .map((h) => h.textContent.trim()),
    };
  });
  const stackCardCount = await page.evaluate(() =>
    document.querySelectorAll('#view-synth .stack > .card').length);
  // 「生成参数」默认折叠（语种不再占外部空间）—— 展开才是用户真实到达它的路径
  const foldOpenable = await page.evaluate(() =>
    document.querySelector('#s-details details').open);
  await page.click('#s-details details > summary'); await sleep(350);
  await shot('r6b-lang-select.png', false);

  // 改一下语种：提示文案要跟着动
  await page.selectOption('#s-details select[data-k=language]', 'Chinese'); await sleep(250);
  const langChanged = await page.evaluate(() => ({
    value: document.querySelector('#s-details select[data-k=language]').value,
    note: document.querySelector('#s-details [data-role=language-v]').textContent,
  }));
  // 改回 Auto，别影响后续（这一步只是验证控件可用）
  await page.selectOption('#s-details select[data-k=language]', 'Auto'); await sleep(200);
  await page.click('#s-details details > summary'); await sleep(300);   // 折回去，还原默认态

  /* ---------- 交互：新建音色弹窗 ---------- */
  await page.click('.navitem[data-view="library"]'); await sleep(300);
  await page.click('#lb-new'); await sleep(420);
  await shot('r7-new-modal.png', false);
  const modalOpen = await page.evaluate(() => document.querySelector('#dlg-new').open);
  const editorHidden = await page.evaluate(() =>
    document.querySelector('#n-editor').classList.contains('hidden'));
  await page.keyboard.press('Escape'); await sleep(320);
  const newClosed = await page.evaluate(() => !document.querySelector('#dlg-new').open);

  /* ---------- 环境抽屉 ---------- */
  await page.click('#env-btn'); await sleep(420);
  await shot('r8-env-drawer.png', false);
  const drawerOn = await page.evaluate(() =>
    document.querySelector('#env-drawer').classList.contains('on'));
  await page.click('#scrim'); await sleep(320);

  /* ---------- 深色 ---------- */
  await page.click('#theme-btn'); await sleep(400);
  await page.click('.navitem[data-view="synth"]'); await sleep(400);
  await shot('r9-synth-dark.png');
  await page.click('.navitem[data-view="library"]'); await sleep(400);
  await shot('r10-library-dark.png');
  await page.click('.navitem[data-view="history"]'); await sleep(500);
  await shot('r11-history-dark.png');

  /* ---------- 布局与计算样式取样 ---------- */
  await page.click('#theme-btn'); await sleep(300);
  await page.click('.navitem[data-view="synth"]'); await sleep(400);

  /* 主操作已挪到结果栏：滚到页面最底部，按钮仍应留在视野里 */
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await sleep(450);
  await shot('r12-rail-action.png', false);
  const railProbe = await page.evaluate(() => {
    const go = document.querySelector('#s-go');
    const r = go.getBoundingClientRect();
    return {
      inRail: document.querySelector('#s-rail').contains(go),
      stillInLeftCol: !!document.querySelector('#view-synth .stack #s-go'),
      visibleAfterScrollToBottom: r.top >= 0 && r.bottom <= innerHeight,
      btnTop: Math.round(r.top),
      railChildren: [...document.querySelector('#s-rail').children]
        .map((c) => (c.className || "").split(" ")[0]),
    };
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await sleep(200);

  const probe = await page.evaluate(() => {
    const cs = getComputedStyle(document.documentElement);
    const box = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height), top: Math.round(r.top + scrollY) };
    };
    return {
      tokenAccent: cs.getPropertyValue('--accent').trim(),
      sidebarW: Math.round(document.querySelector('.sidebar').getBoundingClientRect().width),
      hasNavLabel: !!document.querySelector('.nav__lb'),
      navItems: [...document.querySelectorAll('.navitem')].map((n) => n.textContent.trim()),
      voiceCards: document.querySelectorAll('.vitem').length,
      historyGroups: document.querySelectorAll('.hgroup').length,
      recentCards: document.querySelectorAll('[data-role=recent]').length,
      sidebar: box('.sidebar'), primaryBtn: box('#s-go'),
      /* 主题按钮：必须在 .brand 里，且在标题「OmniVoice」的右边、同一行 */
      themeBtnInBrand: !!document.querySelector('.brand #theme-btn'),
      themeBtnLeftOfTitle: (() => {
        const b = document.querySelector('.brand #theme-btn');
        const t = document.querySelector('.brand__t h1');
        if (!b || !t) return null;
        return Math.round(b.getBoundingClientRect().left - t.getBoundingClientRect().right);
      })(),
      themeInFoot: !!document.querySelector('.sidebar__foot #theme-btn'),
    };
  });
  // 主题按钮与标题同一行：两者的垂直区间必须有重叠
  const sameRow = await page.evaluate(() => {
    const b = document.querySelector('.brand #theme-btn').getBoundingClientRect();
    const t = document.querySelector('.brand__t h1').getBoundingClientRect();
    return { overlap: Math.min(b.bottom, t.bottom) - Math.max(b.top, t.top) > 0,
             btnCenterY: Math.round(b.top + b.height / 2),
             titleCenterY: Math.round(t.top + t.height / 2) };
  });

  console.log('\n===== 取样 =====');
  console.log(JSON.stringify(probe, null, 2));
  console.log('\n音色芯片: 初始 ' + JSON.stringify(noneSelectedYet));
  console.log('  选中「' + pickedVoice + '」后 ' + JSON.stringify(chipAfter));
  console.log('  换一个后 ' + JSON.stringify(switched) +
              (switched.onCount === 1 ? ' ✓ 只有一个是选中态' : ' ✗ 选中态数量不对'));
  console.log('语种控件: ' + JSON.stringify(langProbe));
  console.log('  语音合成左栏卡片数 =', stackCardCount, '（应为 3：文本 / 音色 / 生成参数）',
              '| 生成参数默认折叠=' + (foldOpenable === false));
  console.log('  改成 Chinese 后 ' + JSON.stringify(langChanged));
  console.log('主题按钮: 在标题行内=' + probe.themeBtnInBrand +
              ' | 距标题右边缘=' + probe.themeBtnLeftOfTitle + 'px（>0 即在右侧）' +
              ' | 仍留在侧栏底部=' + probe.themeInFoot +
              ' | 与标题同一行=' + sameRow.overlap +
              ' (按钮中心 ' + sameRow.btnCenterY + ' / 标题中心 ' + sameRow.titleCenterY + ')');
  console.log('新建弹窗 open=' + modalOpen + ' | 未上传时波形区隐藏=' + editorHidden +
              ' | Esc 可关闭=' + newClosed + ' | 环境抽屉=' + drawerOn);
  console.log('主操作:', JSON.stringify(railProbe));
  console.log('\n===== JS 错误 =====');
  console.log(errors.length ? errors : 'none');

  await browser.close();
  process.exit(errors.length ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(2); });

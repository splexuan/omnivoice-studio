/* 「API 对接」页实跑验证（web/static/index.html + web/api_docs.py）
 *
 * 2026-10-14：新版界面已扶正为 index.html（根路径 `/`），脚本随之改走 `/` ——
 * 验的就是用户实际打开的那一版，而不是某个候选文件。
 *
 * 目标：不是"能渲染"，而是逐条确认这页**真的在用后端的数据**：
 *   · 切页后接口条数、分组数 == /api/docs 返回的 counts
 *   · 每条的参数表行数 == 该接口 params 的长度
 *   · 「试一试」是真的发请求（拿回 JSON 并在页面上显示）
 *   · 写操作**没有**试一试按钮（点一下就会在建任务/删音色，不能给）
 *   · 复制按钮真的把内容写进了剪贴板
 *   · 文档与代码对不上时，横幅会变 —— 用 route 拦截伪造一次 ghost 条目
 *
 * 用法: NODE_PATH=<node_modules> node verify-api-page.js <outDir> <port>
 */
const { chromium } = require('playwright-core');
const path = require('path');

const OUT = process.argv[2] || '.';
const PORT = process.argv[3] || '7866';
const BASE = `http://127.0.0.1:${PORT}/`;
const VW = 1512, VH = 980;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const errors = [];
function check(name, ok, extra) {
  results.push({ name, ok: !!ok, extra });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? '  → ' + extra : ''}`);
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const ctx = await browser.newContext({
    viewport: { width: VW, height: VH }, deviceScaleFactor: 1.4,
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  const shot = async (name, full = true) => {
    await page.screenshot({ path: path.join(OUT, name), fullPage: full });
    console.log('shot ->', name);
  };

  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForFunction(() => {
    const sub = document.querySelector('#brand-sub');
    return sub && sub.textContent !== 'Studio' && document.querySelector('#s-go');
  }, { timeout: 30000 });
  await sleep(500);

  /* 后端真值：直接读一次 /api/docs，后面拿它当对照 */
  const spec = await page.evaluate(async () => {
    const r = await fetch('/api/docs');
    return r.json();
  });
  console.log('后端 counts :', JSON.stringify(spec.counts));

  // ---------------------------------------------------------------- 导航
  const navCount = await page.locator('.navitem').count();
  check('导航里有 5 项', navCount === 5, `实际 ${navCount}`);

  await page.click('.navitem[data-view="api"]');
  await sleep(250);

  const navState = await page.evaluate(() => {
    const items = Array.from(document.querySelectorAll('.navitem'));
    const cur = items.filter((x) => x.getAttribute('aria-current') === 'page');
    return { cur: cur.map((x) => x.dataset.view), total: items.length };
  });
  check('切到 API 页后高亮唯一且落在 api',
    navState.cur.length === 1 && navState.cur[0] === 'api', JSON.stringify(navState.cur));

  const views = await page.evaluate(() => {
    const on = Array.from(document.querySelectorAll('section.view'))
      .filter((s) => !s.classList.contains('hidden')).map((s) => s.id);
    return on;
  });
  check('只显示 view-api', views.length === 1 && views[0] === 'view-api', views.join(','));

  await page.waitForSelector('#api-body .ep', { timeout: 15000 });
  await sleep(300);

  // ------------------------------------------------------ 一致性横幅
  const banner = await page.evaluate(() => {
    const el = document.querySelector('#api-body .notes .note');
    return el ? { cls: el.className, text: el.textContent.replace(/\s+/g, ' ').trim() } : null;
  });
  check('一致性横幅是"通过"态（note--ok）', banner && banner.cls.includes('note--ok'),
    banner && banner.cls);
  check('横幅写明了核对条数', banner && banner.text.includes(String(spec.counts.matched)),
    banner && banner.text);

  // ------------------------------------------------------ 条数与分组
  const epCount = await page.locator('#api-body details.ep').count();
  check(`接口条目数 == listed(${spec.counts.listed})`, epCount === spec.counts.listed,
    `实际 ${epCount}`);

  const gsecCount = await page.locator('#api-body .gsec').count();
  check(`分组标题数 == groups(${spec.counts.groups})`, gsecCount === spec.counts.groups,
    `实际 ${gsecCount}`);

  const badges = await page.evaluate(() => {
    const out = {};
    document.querySelectorAll('#api-body details.ep').forEach((d) => {
      const m = d.querySelector('summary .m');
      out[d.dataset.ep] = m ? m.textContent.trim() : null;
    });
    return out;
  });
  const badgeBad = Object.entries(badges).filter(([k, v]) => !k.startsWith(v + ' '));
  check('每条接口的方法徽章与路径一致', badgeBad.length === 0,
    badgeBad.length ? JSON.stringify(badgeBad.slice(0, 3)) : `${Object.keys(badges).length} 条全对`);

  // ---------------------------------------------------- 参数表 vs JSON
  const jobs = spec.endpoints.find((e) => e.path === '/api/jobs');
  await page.click('#api-body details.ep[data-ep="POST /api/jobs"] > summary');
  await sleep(200);
  const jobRows = await page.locator('#api-body details.ep[data-ep="POST /api/jobs"] .ptbl tbody tr').count();
  check(`/api/jobs 参数表行数 == params(${jobs.params.length})`, jobRows === jobs.params.length,
    `实际 ${jobRows}`);

  const starRow = await page.evaluate(() => {
    const d = document.querySelector('#api-body details.ep[data-ep="POST /api/jobs"]');
    const tr = Array.from(d.querySelectorAll('.ptbl tbody tr'))
      .find((r) => r.querySelector('.pn').textContent.trim().startsWith('text'));
    return tr ? tr.querySelector('.req') !== null : null;
  });
  check('/api/jobs 的必填项 text 带星号', starRow === true, String(starRow));

  const hasCurl = await page.evaluate(() => {
    const d = document.querySelector('#api-body details.ep[data-ep="POST /api/jobs"]');
    const pre = d.querySelector('.codeblk pre');
    return pre ? pre.textContent.includes('curl -s -X POST') && pre.textContent.includes('/api/jobs') : false;
  });
  check('/api/jobs 生成了可用的 curl 示例', hasCurl);

  /* 后端文档文本是 Markdown（同一份要喂给 Agent），页面上必须渲染出来。
     只查 <pre> 之外的正文 —— 原始 JSON 与 Agent 简报本来就该原样显示 ` 和 **。 */
  const md = await page.evaluate(() => {
    const box = document.querySelector('#api-body').cloneNode(true);
    box.querySelectorAll('pre').forEach((p) => p.remove());
    const t = box.textContent;
    return {
      stars: (t.match(/\*\*/g) || []).length,
      ticks: (t.match(/`/g) || []).length,
      bold: box.querySelectorAll('b').length,
      code: box.querySelectorAll('code.mono').length,
    };
  });
  check('正文里的 Markdown 已渲染（无裸 ** 与反引号）',
    md.stars === 0 && md.ticks === 0, JSON.stringify(md));
  check('确实渲染出了粗体与行内代码', md.bold > 0 && md.code > 0,
    `b=${md.bold} code.mono=${md.code}`);

  // ------------------------------------------------ 写操作不给试一试
  const postTry = await page.locator('#api-body details.ep[data-ep="POST /api/jobs"] [data-try]').count();
  const postHint = await page.evaluate(() => {
    const d = document.querySelector('#api-body details.ep[data-ep="POST /api/jobs"]');
    const s = d.querySelector('.apihint');
    return s ? s.textContent.trim() : null;
  });
  check('POST 接口没有「试一试」按钮', postTry === 0, `按钮数 ${postTry}`);
  check('POST 接口给出原因文案', postHint === '写操作 · 不提供在线调用', postHint);

  const tryCount = await page.locator('#api-body [data-try]').count();
  const tryable = spec.endpoints.filter((e) => e.try).length;
  check(`「试一试」按钮数 == tryable(${tryable})`, tryCount === tryable, `实际 ${tryCount}`);

  // ---------------------------------------------------- 复制：curl 块
  await page.click('#api-body details.ep[data-ep="POST /api/jobs"] .codeblk .copy');
  await sleep(250);
  const copiedCode = await page.evaluate(() => navigator.clipboard.readText());
  check('复制 curl 写进了剪贴板', copiedCode.includes('curl -s -X POST') && copiedCode.includes('/api/jobs'),
    JSON.stringify(copiedCode.slice(0, 40)));
  const copyLabel = await page.evaluate(() => {
    const d = document.querySelector('#api-body details.ep[data-ep="POST /api/jobs"]');
    return d.querySelector('.codeblk .copy').textContent.trim();
  });
  check('复制按钮给出即时反馈', copyLabel === '已复制', copyLabel);

  // ------------------------------------------------ 试一试：真发请求
  await page.click('#api-body details.ep[data-ep="GET /api/voices"] > summary');
  await sleep(200);
  await page.click('#api-body details.ep[data-ep="GET /api/voices"] [data-try]');
  await page.waitForSelector('#api-body .out.out--ok', { timeout: 15000 });
  await sleep(200);
  const tryOut = await page.evaluate(() => {
    const b = document.querySelector('#api-body [data-out="/api/voices"]');
    let parsed = null;
    try { parsed = JSON.parse(b.textContent); } catch (_) {}
    return {
      ok: b.classList.contains('out--ok'),
      isArray: Array.isArray(parsed),
      hasName: Array.isArray(parsed) && parsed.length > 0 && 'name' in parsed[0],
    };
  });
  check('「试一试」拿回了真实 JSON（/api/voices 是裸数组）',
    tryOut.ok && tryOut.isArray && tryOut.hasName, JSON.stringify(tryOut));

  /* 文档写的返回结构必须和真实返回**同形** —— 只核对路径+方法的话，
     「/api/voices 实际是裸数组、文档写成 {voices:[...]}」这种错是抓不到的。
     下面这几条就是专门钉住这一点的（本次就真的抓到过一次）。 */
  const shape = await page.evaluate(async () => {
    const spec = await (await fetch('/api/docs')).json();
    const voices = await (await fetch('/api/voices')).json();
    const hist = await (await fetch('/api/history')).json();
    const docV = spec.endpoints.find((e) => e.method === 'GET' && e.path === '/api/voices');
    const docH = spec.endpoints.find((e) => e.method === 'GET' && e.path === '/api/history');
    return {
      voicesIsArray: Array.isArray(voices),
      docSaysArray: /裸数组/.test(docV.returns),
      histIsObject: !Array.isArray(hist) && Array.isArray(hist.items),
      docSaysItems: /items/.test(docH.returns),
    };
  });
  check('/api/voices 文档与真实返回同形（都是裸数组）',
    shape.voicesIsArray && shape.docSaysArray, JSON.stringify(shape));
  check('/api/history 文档与真实返回同形（{items:[]}）',
    shape.histIsObject && shape.docSaysItems, JSON.stringify(shape));

  // ------------------------------------------- 接入说明与原始数据
  await page.click('#api-copy-brief');
  await sleep(250);
  const brief = await page.evaluate(() => navigator.clipboard.readText());
  check('页头「复制接入说明」复制的是 Markdown 简报',
    brief.startsWith('# OmniVoice Studio') && brief.includes('/api/jobs'),
    JSON.stringify(brief.slice(0, 30)));

  // ------------------------------------------- 下载：接入说明 / 原始 JSON
  const dl1 = page.waitForEvent('download', { timeout: 15000 });
  await page.click('#api-body [data-dl-brief]');
  const d1 = await dl1;
  check('「下载 .md」给出 omnivoice-api.md',
    d1.suggestedFilename() === 'omnivoice-api.md', d1.suggestedFilename());

  await page.click('#api-body details.acc > summary');
  await sleep(250);
  const rawOk = await page.evaluate(() => {
    const pre = document.querySelector('#api-body details.acc pre');
    return !!pre && pre.textContent.includes('"counts"') && pre.textContent.includes('"endpoints"');
  });
  check('原始数据折叠区含完整 spec', rawOk);

  const dl2 = page.waitForEvent('download', { timeout: 15000 });
  await page.click('#api-body [data-dl-raw]');
  const d2 = await dl2;
  check('「下载 JSON」给出 omnivoice-api-docs.json',
    d2.suggestedFilename() === 'omnivoice-api-docs.json', d2.suggestedFilename());
  await page.click('#api-body details.acc > summary');
  await sleep(200);

  await shot('api-1-light.png');
  await page.screenshot({ path: path.join(OUT, 'api-2-light-hero.png'), fullPage: false });
  console.log('shot -> api-2-light-hero.png');

  // ------------------------------------------- 文档偏离代码时的横幅
  await page.route('**/api/docs', async (route) => {
    const r = await page.request.get(`http://127.0.0.1:${PORT}/api/docs`);
    const body = await r.json();
    body.ghost = ['GET /api/does-not-exist'];
    body.undocumented = ['POST /api/brand-new'];
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });
  await page.click('#api-reload');
  await page.waitForFunction(() => {
    const n = document.querySelector('#api-body .notes .note');
    return n && n.className.includes('note--warn');
  }, { timeout: 10000 });
  const warnText = await page.evaluate(() =>
    document.querySelector('#api-body .notes .note').textContent.replace(/\s+/g, ' ').trim());
  check('对不上时横幅转为告警并列出差异',
    warnText.includes('/api/brand-new') && warnText.includes('/api/does-not-exist'), warnText);
  await shot('api-3-mismatch.png', false);
  await page.unroute('**/api/docs');
  await page.click('#api-reload');
  await sleep(400);
  const backOk = await page.evaluate(() =>
    document.querySelector('#api-body .notes .note').className.includes('note--ok'));
  check('恢复正常文档后横幅回到通过态', backOk);

  // ---------------------------------------------------------------- 深色主题
  await page.click('#theme-btn');
  await sleep(400);
  const theme = await page.evaluate(() => document.documentElement.dataset.theme);
  await shot('api-4-dark.png');
  check('深色主题下页面正常渲染', theme === 'dark', theme);

  const bodyTextColor = await page.evaluate(() => {
    const el = document.querySelector('#api-body details.ep .apidesc');
    return el ? getComputedStyle(el).color : null;
  });
  console.log('深色下正文色 :', bodyTextColor);

  await page.click('#theme-btn');
  await sleep(200);

  // ------------------------------------------------------------ 收尾
  const overlays = await page.evaluate(() => {
    const el = document.querySelector('#api-body details.ep .ep__path');
    return { trunc: el ? el.scrollWidth > el.clientWidth + 2 : null };
  });
  console.log('路径是否被截断 :', JSON.stringify(overlays));

  console.log('\nJS 错误:', errors.length ? errors.join(' | ') : 'none');
  const failed = results.filter((r) => !r.ok);
  console.log(`\n合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`);
  if (failed.length) console.log('失败项：\n' + failed.map((f) => '  · ' + f.name + (f.extra ? ' → ' + f.extra : '')).join('\n'));

  await browser.close();
  process.exit(failed.length || errors.length ? 1 : 0);
})().catch((e) => { console.error('脚本异常:', e); process.exit(2); });

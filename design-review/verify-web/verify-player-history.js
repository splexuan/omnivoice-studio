/* 本次三项改动的端到端验证：
 *   ① 波形播放器（结果区 / 音色卡 / 历史记录）：波形渲染 + 播放头 + 剪辑下载
 *   ② 合成记录标签页：列表、失败态、试听、筛选、重新生成回填、删除
 *   ③ 真实合成一次 → 记录自动落盘（顺带证明整条链路仍然是通的）
 *
 * 用法: node verify-player-history.js <outDir> <port> <seedCount>
 */
const { chromium } = require('playwright-core');

const OUT = process.argv[2] || '.';
const PORT = process.argv[3] || '7867';
const SEED = Number(process.argv[4] || 2);
const BASE = `http://127.0.0.1:${PORT}/index-v2.html`;
const log = (...a) => console.log(...a);

/* 波形是否真的画出来了：canvas 里有没有上色的像素 */
const waveStat = (page, sel) => page.evaluate((s) => {
  const c = document.querySelector(s);
  if (!c) return null;
  if (!c.width || c.width < 2) return { w: c.width, h: c.height, inkPx: 0 };
  const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
  let ink = 0;
  for (let i = 3; i < d.length; i += 4) if (d[i] > 0) ink++;
  return { w: c.width, h: c.height, inkPx: ink };
}, sel);

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(600000);           // 首次合成要加载 2.3GB 模型
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));

  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForSelector("#app:not(.hidden)");

  /* ---------- ① 标签页 ---------- */
  log("=== ① 标签页 ===");
  log("标签:", await page.$$eval(".tab", (ts) => ts.map((t) => t.textContent.trim())));

  /* ---------- ② 合成页：音色卡换成波形播放器 ---------- */
  log("\n=== ② 合成页音色卡 ===");
  await page.click("#s-voice");
  await page.waitForTimeout(250);
  await page.click("#s-voice-combo .combo-list li:first-child");
  await page.waitForTimeout(900);
  log("音色卡播放器:", JSON.stringify(await page.evaluate(() => {
    const c = document.querySelector("#s-voice-card .wpl");
    return c ? { hasPlayer: true, time: c.querySelector(".ptime").textContent.trim() } : { hasPlayer: false };
  })));
  log("  波形:", JSON.stringify(await waveStat(page, "#s-voice-card .wpl-wave canvas")));

  /* ---------- ③ 合成记录：种子数据 ---------- */
  log("\n=== ③ 合成记录（种子数据） ===");
  await page.click('#tab-history');
  await page.waitForTimeout(700);
  const seed = await page.evaluate(() => {
    const rows = [...document.querySelectorAll(".hrow")];
    return {
      count: rows.length,
      badge: document.querySelector("#h-count").textContent.trim(),
      okRow: rows.map((r) => r.className).filter((c) => !c.includes("err")).length,
      errRow: rows.map((r) => r.className).filter((c) => c.includes("err")).length,
      firstText: rows[0] ? rows[0].querySelector(".hrow-text").textContent.trim().slice(0, 24) : null,
      acts: rows[0] ? [...rows[0].querySelectorAll(".hrow-acts button, .hrow-acts a")]
        .map((b) => b.textContent.trim() || "下载") : [],
    };
  });
  log("种子记录:", JSON.stringify(seed));

  log("试听展开:", JSON.stringify(await (async () => {
    await page.click(".hrow:not(.err) [data-role=listen]");
    await page.waitForTimeout(1400);
    const st = await page.evaluate(() => {
      const row = document.querySelector(".hrow:not(.err)");
      const slot = row.querySelector(".hrow-slot");
      return { player: !!slot.querySelector(".wpl"),
               btn: row.querySelector("[data-role=listen]").textContent.trim(),
               time: slot.querySelector(".ptime")?.textContent.trim() || null };
    });
    const w = await waveStat(page, ".hrow:not(.err) .hrow-slot .wpl-wave canvas");
    return { ...st, wave: w };
  })()));

  log("筛选「失败」:", JSON.stringify(await (async () => {
    await page.fill("#h-q", "失败");
    await page.waitForTimeout(300);
    const n = await page.$$eval(".hrow", (r) => r.length);
    await page.fill("#h-q", "");
    await page.waitForTimeout(250);
    return { matched: n };
  })()));

  log("重新生成回填:", JSON.stringify(await (async () => {
    await page.click(".hrow:not(.err) [data-role=reuse]");
    await page.waitForTimeout(500);
    const st = await page.evaluate(() => ({
      tab: document.querySelector('.tab[aria-selected="true"]').textContent.trim(),
      text: document.querySelector("#s-text").value.slice(0, 20),
      voice: document.querySelector("#s-voice").value,
    }));
    await page.click('#tab-history');
    await page.waitForTimeout(600);
    return st;
  })()));

  /* ---------- ④ 真实合成 → 记录自动落盘 ---------- */
  log("\n=== ④ 真实合成一次（会加载模型，慢） ===");
  await page.click('#tab-synth');
  await page.waitForTimeout(300);
  await page.fill("#s-text", "这是合成记录功能的验证，用来确认波形播放器与历史记录都工作正常。");
  const t0 = Date.now();
  await page.click("#s-go");
  await page.waitForFunction(
    () => ["已完成", "完成（有提示）", "失败"].some((s) =>
      document.querySelector("#s-result .badge").textContent.includes(s)),
    null, { timeout: 600000, polling: 1000 });
  log(`合成结束，用时 ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  log("结果:", JSON.stringify(await page.evaluate(() => {
    const box = document.querySelector('#s-result [data-role=playerbox]');
    return {
      badge: document.querySelector("#s-result .badge").textContent.trim(),
      playerShown: !box.classList.contains("hidden"),
      time: box.querySelector(".ptime").textContent.trim(),
      metrics: [...box.querySelectorAll(".metric")].map((m) => m.textContent.trim()),
      hasClipBtn: !!box.querySelector('[data-role=toggle]'),
    };
  })));

  /* ---------- ⑤ 结果区剪辑 ---------- */
  log("\n=== ⑤ 结果区剪辑 ===");
  const box = await page.$("#s-result .wpl-wave");
  const bb = await box.boundingBox();
  await page.click("#s-result [data-role=toggle]");
  await page.waitForTimeout(250);
  await page.mouse.move(bb.x + bb.width * 0.10, bb.y + bb.height / 2);
  await page.mouse.down();
  await page.mouse.move(bb.x + bb.width * 0.62, bb.y + bb.height / 2, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(700);
  log("剪辑态:", JSON.stringify(await page.evaluate(() => {
    const w = document.querySelector("#s-result .wpl");
    const a = document.querySelector("#s-result [data-role=dlclip]");
    return {
      cls: w.className,
      foot: document.querySelector("#s-result .wpl-foot").textContent.trim(),
      selVisible: getComputedStyle(document.querySelector("#s-result .ed-sel")).display !== "none",
      dlShown: !a.classList.contains("hidden"),
      dlHref: a.getAttribute("href") ? a.getAttribute("href").slice(0, 12) : null,
      dlName: a.getAttribute("download"),
    };
  })));

  /* ---------- ⑥ 记录里出现这条 ---------- */
  log("\n=== ⑥ 合成记录自动追加 ===");
  await page.click('#tab-history');
  await page.waitForTimeout(700);
  log("列表:", JSON.stringify(await page.evaluate(() => {
    const rows = [...document.querySelectorAll(".hrow")];
    return {
      count: rows.length,
      badge: document.querySelector("#h-count").textContent.trim(),
      topIsNew: rows[0].querySelector(".hrow-text").textContent.includes("波形播放器"),
      topMeta: rows[0].querySelector(".hrow-top").textContent.replace(/\s+/g, " ").trim(),
    };
  })));

  /* ---------- ⑦ 删除种子记录 ---------- */
  log("\n=== ⑦ 删除 ===");
  const delResult = await page.evaluate(async () => {
    const seedIds = (await (await fetch("/api/history")).json()).items
      .filter((x) => String(x.id).startsWith("seedrec")).map((x) => x.id);
    if (!seedIds.length) return { seedIds, note: "无种子记录" };
    const r = await (await fetch("/api/history/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: seedIds }),
    })).json();
    return { removed: r.removed, left: r.items.length };
  });
  log("删除种子:", JSON.stringify(delResult));
  await page.click("#h-refresh");
  await page.waitForTimeout(600);
  log("刷新后:", JSON.stringify(await page.evaluate(() => ({
    count: document.querySelectorAll(".hrow").length,
    badge: document.querySelector("#h-count").textContent.trim(),
  }))));

  /* ---------- ⑧ 截图 ---------- */
  log("\n=== ⑧ 截图 ===");
  for (const [name, tab] of [["h1-synth", "synth"], ["h2-design", "design"],
                             ["h3-library", "library"], ["h4-history", "history"]]) {
    await page.click("#tab-" + tab);
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${OUT}/${name}-light.png`, fullPage: false });
  }
  await page.click("#tab-synth");
  await page.click("#theme-btn");
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/h5-synth-dark.png` });
  await page.click("#theme-btn");
  await page.waitForTimeout(300);
  log("截图完成");

  log("\n=== 控制台错误 ===\n" + (errors.length ? errors.join("\n") : "无"));
  await browser.close();
})().catch((e) => { console.error("验证脚本失败:", e); process.exit(1); });

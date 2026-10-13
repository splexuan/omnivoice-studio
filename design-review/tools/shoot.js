const { chromium } = require('playwright-core');
const path = require('path');

const OUT = process.argv[2] || '.';
const URL = 'http://127.0.0.1:7866';

(async () => {
  const browser = await chromium.launch({ channel: undefined, headless: true });

  async function shoot(name, { dark = false, width = 1440, full = true } = {}) {
    const ctx = await browser.newContext({
      viewport: { width, height: 1000 },
      deviceScaleFactor: 2,
      colorScheme: dark ? 'dark' : 'light',
      locale: 'zh-CN',
    });
    const page = await ctx.newPage();
    await page.goto(URL, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: path.join(OUT, name), fullPage: full });
    await ctx.close();
    console.log('shot ->', name);
  }

  async function withTab(tabName, name, extra) {
    const ctx = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      deviceScaleFactor: 2,
      colorScheme: 'light',
      locale: 'zh-CN',
    });
    const page = await ctx.newPage();
    await page.goto(URL, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(2500);
    const tab = page.getByRole('tab', { name: tabName });
    await tab.click();
    await page.waitForTimeout(1200);
    if (extra) await extra(page);
    await page.screenshot({ path: path.join(OUT, name), fullPage: true });
    await ctx.close();
    console.log('shot ->', name);
  }

  // 1. 默认（音色克隆）浅色 全页
  await shoot('01-clone-light.png');
  // 2. 音色克隆 首屏（不整页，看首屏信息密度）
  await shoot('02-clone-viewport.png', { full: false });
  // 3. 语音设计
  await withTab('语音设计', '03-design-light.png');
  // 4. 音色库
  await withTab('音色库', '04-library-light.png');
  // 5. 展开生成参数的值
  await withTab('音色克隆', '05-clone-settings-open.png', async (page) => {
    const acc = page.getByText('生成参数（可选，不改也能用）').first();
    await acc.click();
    await page.waitForTimeout(1000);
  });
  // 6. 深色模式
  await shoot('06-clone-dark.png', { dark: true });

  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });

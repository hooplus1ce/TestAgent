#!/usr/bin/env node
/**
 * Playwright 交互示例 - 连接 9222 端口并与页面交互
 */
const { chromium } = require('playwright');

async function main() {
  console.log('='.repeat(60));
  console.log('  Playwright 浏览器交互');
  console.log('='.repeat(60));

  // 连接到浏览器
  console.log('\n[1/5] 正在连接...');
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  console.log('✓ 已连接到浏览器');

  // 获取页面信息
  console.log('\n[2/5] 获取页面信息...');
  const title = await page.title();
  const url = page.url();
  console.log(`  标题: ${title}`);
  console.log(`  URL: ${url}`);

  // 获取页面元素概览
  console.log('\n[3/5] 分析页面元素...');

  // 统计按钮
  const buttons = await page.$$('button');
  console.log(`  按钮数量: ${buttons.length}`);
  if (buttons.length > 0) {
    const buttonTexts = await Promise.all(
      buttons.slice(0, 8).map(btn =>
        page.evaluate(el => el.textContent?.trim() || '', btn)
      )
    );
    buttonTexts.forEach((text, i) => {
      if (text) console.log(`    [${i + 1}] ${text.substring(0, 40)}`);
    });
  }

  // 统计链接
  const links = await page.$$('a');
  console.log(`  链接数量: ${links.length}`);

  // 统计输入框
  const inputs = await page.$$('input, textarea, select');
  console.log(`  输入框数量: ${inputs.length}`);

  // 统计 iframe
  const iframes = await page.$$('iframe');
  console.log(`  iframe 数量: ${iframes.length}`);

  // 获取页面文本
  console.log('\n[4/5] 页面内容预览...');
  const pageText = await page.evaluate(() => document.body.innerText || '');
  console.log(pageText.substring(0, 600));
  if (pageText.length > 600) console.log('  ... (已截断)');

  // 截图
  console.log('\n[5/5] 截图...');
  const screenshotPath = '/tmp/playwright_interact.png';
  await page.screenshot({ path: screenshotPath });
  console.log(`✓ 截图已保存: ${screenshotPath}`);

  console.log('\n' + '='.repeat(60));
  console.log('  可用操作示例:');
  console.log('='.repeat(60));
  console.log('  - page.click(selector)       // 点击元素');
  console.log('  - page.fill(selector, text)  // 输入文本');
  console.log('  - page.evaluate(script)     // 执行 JS');
  console.log('  - page.screenshot()         // 截图');
  console.log('  - page.$(selector)          // 获取单个元素');
  console.log('  - page.$$(selector)         // 获取多个元素');
  console.log('='.repeat(60));

  console.log('\n✓ 完成！');
}

main().catch(err => {
  console.error('错误:', err);
  process.exit(1);
});

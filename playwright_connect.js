#!/usr/bin/env node
/**
 * 使用 Playwright 连接到 9222 端口的 Chrome
 */
const { chromium } = require('playwright');
const fs = require('fs');

async function main() {
  console.log('正在连接到 127.0.0.1:9222 ...');

  try {
    // 通过 CDP 连接到现有浏览器
    const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');

    console.log('\n✓ Playwright 连接成功！');

    // 获取所有上下文
    const contexts = browser.contexts();
    console.log(`\n📋 浏览器上下文数量: ${contexts.length}`);

    // 列出所有页面
    let allPages = [];
    for (const context of contexts) {
      const pages = context.pages();
      allPages = allPages.concat(pages);
      console.log(`\n  上下文 ${contexts.indexOf(context)} 页面数: ${pages.length}`);

      for (const page of pages) {
        console.log(`    - [${pages.indexOf(page)}] ${await page.title()}`);
        console.log(`      ${page.url()}`);
      }
    }

    if (allPages.length > 0) {
      const page = allPages[0];
      console.log(`\n📍 当前页面信息:`);
      console.log(`  标题: ${await page.title()}`);
      console.log(`  URL: ${page.url()}`);

      // 获取视口大小
      const viewport = page.viewportSize();
      if (viewport) {
        console.log(`  视口: ${viewport.width} x ${viewport.height}`);
      }

      // 截图保存
      console.log(`\n📸 正在截图...`);
      await page.screenshot({ path: '/tmp/playwright_screenshot.png' });
      console.log(`  已保存到: /tmp/playwright_screenshot.png`);

      // 获取页面文本
      console.log(`\n📄 页面文本 (前800字符):`);
      const text = await page.evaluate(() => document.body.innerText || '');
      console.log(text.substring(0, 800));
      if (text.length > 800) console.log('... (已截断)');
    }

    console.log('\n✓ 完成！浏览器连接已保持。');

    // 不关闭浏览器，只断开连接
    // browser.close() 会关闭用户的浏览器，所以我们不调用它

  } catch (error) {
    console.error('\n✗ 连接失败:', error.message);
    console.error('\n请确认:');
    console.error('  1. Chrome 已启动并开启远程调试端口 9222');
    console.error('  2. 启动参数包含: --remote-debugging-port=9222');
    process.exit(1);
  }
}

main();

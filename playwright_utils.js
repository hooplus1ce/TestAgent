#!/usr/bin/env node
/**
 * Playwright 工具模块 - 连接 9222 端口并提供交互功能
 */
const { chromium } = require('playwright');

class PlaywrightBrowser {
  constructor() {
    this.browser = null;
    this.context = null;
    this.page = null;
  }

  async connect() {
    console.log('正在连接到 127.0.0.1:9222 ...');
    this.browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
    const contexts = this.browser.contexts();
    if (contexts.length > 0) {
      this.context = contexts[0];
      const pages = this.context.pages();
      if (pages.length > 0) {
        this.page = pages[0];
      }
    }
    console.log('✓ 已连接');
    return this;
  }

  async getCurrentPage() {
    if (!this.page) {
      const contexts = this.browser.contexts();
      if (contexts.length > 0) {
        const pages = contexts[0].pages();
        if (pages.length > 0) {
          this.page = pages[0];
        }
      }
    }
    return this.page;
  }

  async getPageInfo() {
    const page = await this.getCurrentPage();
    return {
      title: await page.title(),
      url: page.url(),
      viewport: page.viewportSize()
    };
  }

  async getPageText(maxChars = 1000) {
    const page = await this.getCurrentPage();
    const text = await page.evaluate(() => document.body.innerText || '');
    return text.substring(0, maxChars);
  }

  async screenshot(path = '/tmp/playwright_screenshot.png') {
    const page = await this.getCurrentPage();
    await page.screenshot({ path, fullPage: false });
    return path;
  }

  async click(selector) {
    const page = await this.getCurrentPage();
    await page.click(selector);
  }

  async fill(selector, value) {
    const page = await this.getCurrentPage();
    await page.fill(selector, value);
  }

  async evaluate(script) {
    const page = await this.getCurrentPage();
    return await page.evaluate(script);
  }

  async querySelectorAll(selector) {
    const page = await this.getCurrentPage();
    return await page.$$(selector);
  }

  async querySelector(selector) {
    const page = await this.getCurrentPage();
    return await page.$(selector);
  }
}

module.exports = { PlaywrightBrowser };

// 如果直接运行此脚本，执行简单测试
if (require.main === module) {
  (async () => {
    const browser = new PlaywrightBrowser();
    await browser.connect();
    const info = await browser.getPageInfo();
    console.log('\n页面信息:', info);
    const text = await browser.getPageText(500);
    console.log('\n页面文本:', text);
  })();
}

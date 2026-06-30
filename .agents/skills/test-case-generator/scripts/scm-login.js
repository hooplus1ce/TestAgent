// SCM 系统登录 + Session 维持
// =============================================
// 首次登录：通过浏览器内填表完成
// Session 维持：通过 CDP Network.setCookie 刷新 Cookie
// =============================================

// ============ 首次登录 ============
// Step 1: OCR 验证码（eval py kernel）
// import ddddocr, httpx
// ocr = ddddocr.DdddOcr(show_ad=False)
// ocr.set_ranges("0123456789")
// client = httpx.Client(base_url="https://demo19-scm.hoolinks.com")
// resp = client.get("/validateCode.json", params={"key": "regValidateCode"},
//     headers={"Referer": "https://demo19-scm.hoolinks.com/meLogin.do"},
//     cookies={"SESSION": session_cookie_value})
// vcode = ocr.classification(resp.read())  // → 4位纯数字

// Step 2: 填写登录表单
await page.evaluate((vcode) => {
  document.getElementById('signinValue').textContent = 'Ac123456';
  document.querySelector('input[name="vcode"]').value = vcode;
}, vcode);
await tab.fill('textbox[name="用户名"]', 'Hooplus1ce');
await tab.click('aria-ref=e30');
await new Promise(r => setTimeout(r, 3000));

// Step 3: 保存 Cookie 供后续注入使用
var cookies = await page.cookies();
var sessionValue = cookies.find(function(c){ return c.name === 'SESSION'; }).value;
var ucToken = cookies.find(function(c){ return c.name === 'UCTOKEN'; }).value;
var cookieToken = cookies.find(function(c){ return c.name === 'cookie_token'; }).value;

// ============ Session 维持（Cookie 注入） ============
// 当检测到系统级确认弹窗（提示「您还未登录或登录信息过期，请重新登录」）时执行
//
// ⚠️ 参数来源（v2 优化）：
//   savedSessionValue / savedUcToken / savedCookieToken 三参优先从 cookie-cache.js
//   在 Phase 1 缓存的 page.__scmCookieCache 读取（调用 getRefreshSessionArgs(page)）。
//   避免探索到一半 session 过期却无 cookie 可注入。详见 scripts/cookie-cache.js。
async function refreshSession(page, savedSessionValue, savedUcToken, savedCookieToken) {
  var cdp = await page.createCDPSession();
  await cdp.send('Network.setCookie', {
    name: 'SESSION', value: savedSessionValue,
    domain: '.demo19-scm.hoolinks.com', path: '/'
  });
  await cdp.send('Network.setCookie', {
    name: 'UCTOKEN', value: savedUcToken,
    domain: '.demo19-scm.hoolinks.com', path: '/'
  });
  await cdp.send('Network.setCookie', {
    name: 'cookie_token', value: savedCookieToken,
    domain: '.demo19-scm.hoolinks.com', path: '/'
  });
  await page.reload();
  await new Promise(r => setTimeout(r, 3000));
}

// SCM 会话 Cookie 缓存
// =============================================
// 用途：在 Phase 1 连接浏览器后立即缓存 SESSION / UCTOKEN / cookie_token，
//       供 scm-login.js 的 refreshSession() 在会话中途过期时随时取用，
//       解决"探索到一半 session 过期却无 cookie 可注入"的问题。
// =============================================
// 生命周期：
//   1. Phase 1 开头：调用 cacheSession(page) → 把 cookie 写入 window.__scmCache
//   2. Phase 3 探索中检测到过期弹窗 → 直接调用 getRefreshSessionArgs() 取值注入
//   3. 每次成功注入后重新 cache（刷新令牌）
// =============================================

// 在浏览器上下文执行：缓存当前会话 cookie 到 window 全局
// 返回 { ok, cached, missing[] }
async function cacheSession(page) {
  var cookies = await page.cookies();
  var need = ['SESSION', 'UCTOKEN', 'cookie_token'];
  var map = {};
  var missing = [];
  need.forEach(function (name) {
    var c = cookies.find(function (x) { return x.name === name; });
    if (c && c.value) map[name] = c.value;
    else missing.push(name);
  });
  page.__scmCookieCache = map; // 持久挂在 page 对象上，跨 evaluate 可用
  return { ok: missing.length === 0, cached: Object.keys(map), missing: missing };
}

// 读取已缓存的 cookie 三元组，可直接传给 refreshSession(page, s, u, c)
function getRefreshSessionArgs(page) {
  var m = page.__scmCookieCache || {};
  return [m.SESSION, m.UCTOKEN, m.cookie_token];
}

// 便捷封装：检测到过期弹窗后一键刷新（自包含，无需手动取参）
// 依赖：scm-login.js 中已定义的 refreshSession(page, s, u, c)
async function refreshIfExpired(page) {
  if (!page.__scmCookieCache) {
    return { ok: false, reason: 'cookie 未缓存，请先在 Phase 1 调用 cacheSession(page)' };
  }
  var args = getRefreshSessionArgs(page);
  if (args.some(function (v) { return !v; })) {
    return { ok: false, reason: '缓存中有缺失的 cookie: ' + JSON.stringify(args) };
  }
  try {
    await refreshSession(page, args[0], args[1], args[2]);
    // 刷新成功后重新缓存（cookie 可能已被服务端轮换）
    await cacheSession(page);
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: String(e) };
  }
}

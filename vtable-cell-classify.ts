import puppeteer from "puppeteer-core";

const DEBUG_PORT = 9222;
const TARGET_URL_PATTERN = "hoolinks";

// ==================== 1. 连接浏览器 ====================
async function connectBrowser() {
  console.log("正在连接浏览器...");

  try {
    const resp = await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/version`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const version = await resp.json();
    console.log(`✓ 调试端口可用, Chrome 版本: ${version.Browser}`);
  } catch {
    console.error(`
╔════════════════════════════════════════════════════════╗
║ ❌ 无法连接到 Chrome 调试端口                          ║
║ 请确保 Chrome 以 --remote-debugging-port=9222 启动     ║
║ 运行前设置: set NO_PROXY=127.0.0.1                     ║
╚════════════════════════════════════════════════════════╝`);
    process.exit(1);
  }

  const browser = await puppeteer.connect({
    browserURL: `http://127.0.0.1:${DEBUG_PORT}`,
    defaultViewport: null,
  });

  const pages = await browser.pages();
  console.log("当前打开的页面:");
  pages.forEach((p, i) => console.log(`  [${i}] ${p.url().substring(0, 80)}`));

  const page = pages.find((p) => p.url().includes(TARGET_URL_PATTERN));
  if (!page) {
    console.error(`\n未找到包含 "${TARGET_URL_PATTERN}" 的页面`);
    await browser.disconnect();
    process.exit(1);
  }
  console.log(`✓ 已连接: ${page.url().substring(0, 80)}`);
  return { browser, page };
}

// ==================== 2. 获取活跃 iframe ====================
async function getIframeInfo(page: any) {
  await page.waitForSelector('[role="tabpanel"][aria-hidden="false"] iframe', { timeout: 15000 });

  const info = await page.evaluate(() => {
    const iframe = document.querySelector(
      '[role="tabpanel"][aria-hidden="false"] iframe',
    ) as HTMLIFrameElement;
    if (!iframe) return null;
    const rect = iframe.getBoundingClientRect();
    const name = iframe.name || "";
    const src = iframe.src || "";

    let canvasX = 0,
      canvasY = 0;
    const doc = iframe.contentDocument;
    if (doc) {
      const canvas = doc.querySelector("canvas");
      if (canvas) {
        const cRect = (canvas as HTMLElement).getBoundingClientRect();
        canvasX = cRect.x;
        canvasY = cRect.y;
      }
    }
    return { name, src, totalX: rect.x + canvasX, totalY: rect.y + canvasY };
  });

  if (!info) throw new Error("未找到活跃 iframe");

  const mainFrame = page.mainFrame();
  const childFrames = page.frames().filter((f: any) => f !== mainFrame);

  let iframeFrame = null;
  if (info.name) {
    iframeFrame = childFrames.find((f: any) => f.name() === info.name);
  }
  if (!iframeFrame && info.src) {
    const srcPath = info.src.replace(/^https?:\/\/[^/]+/, "");
    iframeFrame = childFrames.find((f: any) => {
      const fUrl = f.url().replace(/^https?:\/\/[^/]+/, "");
      return fUrl === srcPath || f.url().includes(srcPath.substring(0, 40));
    });
  }
  if (!iframeFrame) {
    iframeFrame = childFrames.find((f: any) => /spo\/outReturnReport|hoolinks/.test(f.url()));
  }
  if (!iframeFrame) throw new Error("未找到 iframe Frame");

  console.log(`✓ iframe name="${iframeFrame.name()}"`);
  return { iframeOffset: info, iframeFrame };
}

// ==================== 3. 注入全部函数 ====================
async function injectAll(iframeFrame: any) {
  // --- VTable 实例获取 ---
  await iframeFrame.evaluate(() => {
    (window as any).__getVTable = function () {
      var el =
        document.querySelector(".vtable") ||
        document.querySelector('[class*="vtable"]') ||
        (document.querySelector("canvas") && document.querySelector("canvas").parentElement);
      if (!el || !el.parentElement) return { ok: false, reason: ".vtable not found" };

      var parent = el.parentElement;
      var fk = Object.keys(parent).find(
        (k) => k.startsWith("__reactFiber") || k.startsWith("__reactInternalInstance"),
      );
      if (!fk) {
        var current = parent;
        for (var i = 0; i < 5; i++) {
          if (!current) break;
          fk = Object.keys(current).find(
            (k) => k.startsWith("__reactFiber") || k.startsWith("__reactInternalInstance"),
          );
          if (fk) {
            parent = current;
            break;
          }
          current = current.parentElement;
        }
      }
      if (!fk) return { ok: false, reason: "fiber key not found" };

      var fiber = (parent as any)[fk];
      var count = 0;
      while (fiber && count < 30) {
        if (fiber.stateNode && fiber.stateNode.vtableInstance) {
          (window as any)._vtable = fiber.stateNode.vtableInstance;
          return { ok: true, levels: count };
        }
        fiber = fiber.return;
        count++;
      }
      return { ok: false, reason: "vtableInstance not found in " + count + " levels" };
    };
  });

  // --- 合并扫描函数：列级 body 行为 + 表头图标坐标 ---
  await iframeFrame.evaluate(() => {
    var MAX_COORD = 1e15;

    // ===== 图标名称 → 功能映射 =====
    function iconFunction(name) {
      var n = (name || "").toLowerCase();
      if (n.indexOf("sort") !== -1) return "排序";
      if (n.indexOf("filter") !== -1) return "筛选";
      if (n.indexOf("dropdown") !== -1 || n.indexOf("downward") !== -1) return "下拉菜单";
      if (n.indexOf("freeze") !== -1 || n.indexOf("frozen") !== -1) return "冻结列";
      if (n.indexOf("checkbox") !== -1) return "复选框";
      if (n.indexOf("radio") !== -1) return "单选";
      if (n.indexOf("switch") !== -1) return "开关";
      if (n.indexOf("expand") !== -1) return "展开";
      if (n.indexOf("collapse") !== -1) return "折叠";
      if (n.indexOf("content") !== -1) return "文本内容";
      if (n === "group" || n === "") return "";
      return name;
    }

    // ===== 获取表头单元格的所有图标坐标 =====
    function getCellIconBounds(vtable, col, row) {
      var cellGroup =
        vtable.scenegraph && vtable.scenegraph.getCell ? vtable.scenegraph.getCell(col, row) : null;
      if (!cellGroup) return [];

      var icons = [];
      var collect = function (node, depth) {
        if (!node || depth > 3) return;
        if (depth > 0) {
          var bounds = node.globalAABBBounds;
          var name = node.name || "";
          if (bounds && typeof bounds.x1 === "number" && name && name !== "text") {
            var x = bounds.x1,
              y = bounds.y1;
            var w = bounds.x2 - bounds.x1;
            var h = bounds.y2 - bounds.y1;
            // 过滤无效坐标（icon-back 等占位节点）
            if (
              Math.abs(x) < MAX_COORD &&
              Math.abs(y) < MAX_COORD &&
              w > 0 &&
              w < 500 &&
              h > 0 &&
              h < 500
            ) {
              icons.push({
                name: name,
                func: iconFunction(name),
                x: Math.round(x * 10) / 10,
                y: Math.round(y * 10) / 10,
                width: Math.round(w * 10) / 10,
                height: Math.round(h * 10) / 10,
                centerX: Math.round(((bounds.x1 + bounds.x2) / 2) * 10) / 10,
                centerY: Math.round(((bounds.y1 + bounds.y2) / 2) * 10) / 10,
              });
            }
          }
        }
        if (node.children) {
          for (var i = 0; i < node.children.length; i++) {
            collect(node.children[i], depth + 1);
          }
        }
      };
      collect(cellGroup, 0);
      return icons;
    }

    // ===== 获取列的第一个 body 行 =====
    function getFirstBodyRow(t, col) {
      var headerLevel = t.columnHeaderLevelCount || t.frozenRowCount || 1;
      for (var r = headerLevel; r < t.rowCount; r++) {
        try {
          if (t.isHeader && !t.isHeader(col, r)) return r;
        } catch (e) {
          return r;
        }
        if (!t.isHeader) return r;
      }
      return headerLevel;
    }

    // ===== 判断列 body 单元格行为（采样第一个 body 行）=====
    function classifyColumnBody(t, col) {
      var sampleRow = getFirstBodyRow(t, col);
      if (sampleRow >= t.rowCount)
        return { behavior: "none", detail: "", editable: false, bodyType: "" };

      var hasEditor = false;
      try {
        hasEditor = !!(t.isHasEditorDefine && t.isHasEditorDefine(col));
      } catch (e) {}
      var isSeries = false;
      try {
        isSeries = !!(t.isSeriesNumber && t.isSeriesNumber(col, sampleRow));
      } catch (e) {}
      var isAgg = false;
      try {
        isAgg = !!(
          t.internalProps &&
          t.internalProps.layoutMap &&
          t.internalProps.layoutMap.isAggregation &&
          t.internalProps.layoutMap.isAggregation(col, sampleRow)
        );
      } catch (e) {}

      var editable = hasEditor && !isSeries && !isAgg;

      var type = "";
      try {
        type = t.getCellType(col, sampleRow) || "";
      } catch (e) {}
      var behavior = "none";
      var detail = "";

      if (type === "checkbox") {
        behavior = "control:checkbox";
        detail = "可勾选";
      } else if (type === "button") {
        behavior = "control:button";
        detail = "可点击";
      } else if (type === "radio") {
        behavior = "control:radio";
        detail = "可单选";
      } else if (type === "switch") {
        behavior = "control:switch";
        detail = "可开关";
      } else if (type === "link") {
        behavior = "link";
        detail = "可跳转";
      } else {
        var hasCustom = false;
        try {
          hasCustom = !!(t.getCustomLayout && t.getCustomLayout(col, sampleRow));
        } catch (e) {}
        if (!hasCustom) {
          try {
            hasCustom = !!(t.getCustomRender && t.getCustomRender(col, sampleRow));
          } catch (e) {}
        }
        if (hasCustom) {
          var hasChart = false;
          try {
            var define = t.getBodyColumnDefine ? t.getBodyColumnDefine(col, sampleRow) : null;
            if (define && define.cellType === "chart") hasChart = true;
          } catch (e) {}
          if (hasChart) {
            behavior = "chart";
            detail = "图表单元格";
          } else {
            behavior = "popup-candidate";
            detail = "自定义弹窗";
          }
        }
        if (behavior === "none") {
          try {
            var style = t.getCellStyle && t.getCellStyle(col, sampleRow);
            if (style && (style.textDecoration === "underline" || style.underline === true)) {
              behavior = "link";
              detail = "可跳转";
            }
          } catch (e) {}
        }
        if (behavior === "none") {
          detail = editable ? "可编辑文本" : "纯文本";
        }
      }

      return { behavior: behavior, detail: detail, editable: editable, bodyType: type };
    }

    // ===== 对外暴露：扫描所有列 =====
    (window as any).__scanColumns = function (maxCol) {
      var t = (window as any)._vtable;
      if (!t) return null;

      var headerLevelCount = t.columnHeaderLevelCount || 1;
      var results = [];

      for (var col = 0; col < maxCol; col++) {
        // ① 获取该列的 body 行为（采样）
        var bodyInfo = classifyColumnBody(t, col);

        // ② 遍历每个表头行
        for (var row = 0; row < headerLevelCount; row++) {
          var isHeader = false;
          try {
            isHeader = !!(t.isHeader && t.isHeader(col, row));
          } catch (e) {}

          // 标题
          var title = "";
          // ① 优先用 getCellValue 获取表头显示标题（中文名）
          try {
            if (t.getCellValue) title = t.getCellValue(col, row) || "";
          } catch (e) {}
          // ② 回退：从列定义中获取 title / caption
          if (!title) {
            try {
              var define = t.getHeaderDefine ? t.getHeaderDefine(col, row) : null;
              if (define) title = define.title || define.caption || "";
            } catch (e) {}
          }
          // ③ 最终回退：getHeaderField
          if (!title) {
            try {
              title = t.getHeaderField ? t.getHeaderField(col, row) || "" : "";
            } catch (e) {}
          }

          // 图标坐标
          var icons = [];
          if (isHeader) {
            icons = getCellIconBounds(t, col, row);
          }

          results.push({
            col: col,
            row: row,
            isHeader: isHeader,
            title:
              typeof title === "string" ? title.substring(0, 30) : String(title).substring(0, 30),
            bodyBehavior: bodyInfo.behavior,
            bodyDetail: bodyInfo.detail,
            bodyType: bodyInfo.bodyType,
            bodyEditable: bodyInfo.editable,
            icons: icons,
          });
        }
      }
      return results;
    };
  });

  console.log("✓ 函数已注入");
}

// ==================== 4. 初始化 VTable ====================
async function initVTable(iframeFrame: any) {
  const result = await iframeFrame.evaluate(() => (window as any).__getVTable());
  if (!result || !result.ok) {
    console.error("✗ VTable 获取失败:", result?.reason || "unknown");
    return false;
  }
  console.log(`✓ VTable 实例已获取 (向上遍历 ${result.levels || "?"} 层 Fiber)`);
  return true;
}

// ==================== 5. 扫描并输出 ====================
async function scanAndPrint(iframeFrame: any, iframeOffset: any) {
  const dims = await iframeFrame.evaluate(() => {
    const t = (window as any)._vtable;
    return { colCount: t.colCount, rowCount: t.rowCount };
  });

  console.log(`\n表格尺寸: ${dims.colCount} 列 × ${dims.rowCount} 行`);

  const scanCols = Math.min(dims.colCount, 50);

  const columns: any[] = await iframeFrame.evaluate(
    (mc) => (window as any).__scanColumns(mc),
    scanCols,
  );

  if (!columns || !columns.length) {
    console.log("未扫描到任何列");
    return;
  }

  // ── 构建合并输出表 ──
  console.log("\n========== 表头单元格属性 + body 列行为 + 图标坐标 ==========\n");
  const rows: any[] = [];
  for (const col of columns) {
    if (col.icons && col.icons.length > 0) {
      for (const icon of col.icons) {
        rows.push({
          列号: col.col,
          行号: col.row,
          标题: col.title || "",
          列单元格行为: col.bodyBehavior,
          列详情: col.bodyDetail,
          列可编辑: col.bodyEditable ? "✓" : "-",
          图标名称: icon.name,
          图标功能: icon.func,
          坐标X: icon.x,
          坐标Y: icon.y,
          宽度: icon.width,
          高度: icon.height,
          中心X: icon.centerX,
          中心Y: icon.centerY,
          视口点击X: iframeOffset
            ? Math.round((iframeOffset.totalX + icon.centerX) * 10) / 10
            : "-",
          视口点击Y: iframeOffset
            ? Math.round((iframeOffset.totalY + icon.centerY) * 10) / 10
            : "-",
        });
      }
    } else {
      rows.push({
        列号: col.col,
        行号: col.row,
        标题: col.title || "",
        列单元格行为: col.bodyBehavior,
        列详情: col.bodyDetail,
        列可编辑: col.bodyEditable ? "✓" : "-",
        图标名称: "-",
        图标功能: "-",
        坐标X: "-",
        坐标Y: "-",
        宽度: "-",
        高度: "-",
        中心X: "-",
        中心Y: "-",
        视口点击X: "-",
        视口点击Y: "-",
      });
    }
  }

  console.table(rows);

  // ── 汇总统计 ──
  console.log("\n========== 汇总统计 ==========\n");

  // body 行为统计（按列去重）
  const bodyStats: Record<string, number> = {};
  const seenCols = new Set<number>();
  for (const col of columns) {
    if (!seenCols.has(col.col)) {
      seenCols.add(col.col);
      bodyStats[col.bodyBehavior] = (bodyStats[col.bodyBehavior] || 0) + 1;
    }
  }
  console.table(
    Object.entries(bodyStats).map(([key, count]) => ({
      body行为: key,
      列数: count,
    })),
  );

  // 图标统计
  const iconStats: Record<string, number> = {};
  for (const col of columns) {
    for (const icon of col.icons || []) {
      iconStats[icon.func] = (iconStats[icon.func] || 0) + 1;
    }
  }
  if (Object.keys(iconStats).length) {
    console.log("\n表头图标功能统计:");
    console.table(
      Object.entries(iconStats).map(([name, count]) => ({
        图标功能: name,
        出现次数: count,
      })),
    );
  }

  const editableCols = Array.from(seenCols).length
    ? columns.filter((c, i, arr) => arr.findIndex((x) => x.col === c.col) === i && c.bodyEditable)
        .length
    : 0;
  const clickableCols = Object.entries(bodyStats)
    .filter(([k]) => k !== "none")
    .reduce((s, [, v]) => s + v, 0);

  console.log(`\n扫描范围: ${scanCols} 列（含 ${columns.length} 个表头单元格）`);
  console.log(`可交互列: ${clickableCols} | 可编辑列: ${editableCols}\n`);
}

// ==================== 主流程 ====================
async function main() {
  const { browser, page } = await connectBrowser();
  try {
    const { iframeOffset, iframeFrame } = await getIframeInfo(page);
    await injectAll(iframeFrame);
    const ok = await initVTable(iframeFrame);
    if (!ok) {
      await browser.disconnect();
      process.exit(1);
    }
    await scanAndPrint(iframeFrame, iframeOffset);
    console.log("✓ 扫描完成");
  } catch (err) {
    console.error("执行出错:", err);
  } finally {
    await browser.disconnect();
    console.log("✓ 已断开连接，浏览器保持运行");
  }
}

main().catch(console.error);

/* CLodop Web 打印服务加载 + 打印面单 HTML。
   CLodop 安装后在本机提供服务：http://localhost:8000/CLodopfuncs.js（https 为 8443）。
   参考官方 LodopFuncs 精简版。 */
(function () {
  var CLodopJS = "CLodop_JS_Tag";
  var head = document.head || document.getElementsByTagName("head")[0] || document.documentElement;

  function loadScript(url) {
    var s = document.createElement("script");
    s.src = url;
    s.id = CLodopJS + Math.random();
    head.insertBefore(s, head.firstChild);
  }

  // 只加载与页面协议匹配的端口：http 页 → 8000，https 页 → 8443。
  // 避免在 http 大屏上去碰 8443（CLodop 的 https 证书常报 SSL 错，且有混合内容问题）。
  function ensureLoaded() {
    if (window._clodopLoading) return;
    window._clodopLoading = true;
    var url = (location.protocol === "https:")
      ? "https://localhost:8443/CLodopfuncs.js"
      : "http://localhost:8000/CLodopfuncs.js";
    loadScript(url);
  }
  ensureLoaded();

  function getLodop() {
    var LODOP;
    try {
      LODOP = window.getCLodop ? window.getCLodop() : (window.CLODOP || null);
    } catch (e) { LODOP = null; }
    return LODOP;
  }

  // 等 CLodop 就绪
  function whenReady(cb, tries) {
    tries = tries || 0;
    var L = getLodop();
    if (L && (L.VERSION || L.PRINT_INIT)) { cb(L); return; }
    if (tries > 40) { cb(null); return; }   // ~8s 超时
    setTimeout(function () { whenReady(cb, tries + 1); }, 200);
  }

  /**
   * 打印一段面单 HTML。
   * @param {string} html  完整面单 HTML（含样式）
   * @param {object} opt   { widthMM, heightMM, printerName }
   * @param {function} done callback(ok, errMsg)
   */
  function printLabelHtml(html, opt, done) {
    opt = opt || {};
    whenReady(function (LODOP) {
      if (!LODOP) { done(false, "未检测到 CLodop 打印服务，请确认已安装并运行"); return; }
      try {
        LODOP.PRINT_INIT("GOFO面单");
        LODOP.SET_PRINT_PAGESIZE(1, (opt.widthMM || 100) + "mm", (opt.heightMM || 150) + "mm", "");
        if (opt.printerName) {
          try { LODOP.SET_PRINTER_INDEXA(opt.printerName); } catch (e) {}
        }
        LODOP.ADD_PRINT_HTM(0, 0, (opt.widthMM || 100) + "mm", (opt.heightMM || 150) + "mm", html);
        // 直接打印，不弹预览（无人值守）
        var r = LODOP.PRINT();
        done(r !== false, r === false ? "打印被取消或失败" : "");
      } catch (e) {
        done(false, "打印异常：" + e);
      }
    });
  }

  window.GofoPrint = { getLodop: getLodop, whenReady: whenReady, printLabelHtml: printLabelHtml };
})();

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

  /**
   * 打印一个 PDF（url 或 base64）。CLodop 用 ADD_PRINT_PDF。
   * @param {string} pdf   PDF 的 URL，或 base64（不含 data: 前缀时会自动加）
   * @param {object} opt   { widthMM, heightMM, printerName }
   * @param {function} done callback(ok, errMsg)
   */
  function printPdf(pdf, opt, done) {
    opt = opt || {};
    whenReady(function (LODOP) {
      if (!LODOP) { done(false, "未检测到 CLodop 打印服务，请确认已安装并运行"); return; }
      try {
        LODOP.PRINT_INIT("GOFO面单");
        LODOP.SET_PRINT_PAGESIZE(1, (opt.widthMM || 100) + "mm", (opt.heightMM || 150) + "mm", "");
        if (opt.printerName) { try { LODOP.SET_PRINTER_INDEXA(opt.printerName); } catch (e) {} }
        LODOP.ADD_PRINT_PDF(0, 0, (opt.widthMM || 100) + "mm", (opt.heightMM || 150) + "mm", pdf);
        var r = LODOP.PRINT();
        done(r !== false, r === false ? "打印被取消或失败" : "");
      } catch (e) {
        done(false, "打印异常：" + e);
      }
    });
  }

  /**
   * 浏览器静默打印一张图（免费，无水印）。配合 Chrome 的 --kiosk-printing 直接打到默认打印机。
   * 用隐藏 iframe 承载「只有这张图 + @page 尺寸」的文档，再调用其 print()。
   */
  function printImageNative(dataurl, opt, done) {
    opt = opt || {};
    var w = opt.widthMM || 100, h = opt.heightMM || 150;
    var html = '<!doctype html><html><head><meta charset="utf-8"><style>'
      + '@page{size:' + w + 'mm ' + h + 'mm;margin:0}'
      + 'html,body{margin:0;padding:0}'
      + 'img{width:' + w + 'mm;height:' + h + 'mm;display:block}'
      + '</style></head><body><img src="' + dataurl + '"></body></html>';
    var ifr = document.createElement("iframe");
    ifr.setAttribute("aria-hidden", "true");
    ifr.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
    document.body.appendChild(ifr);
    var fired = false;
    function fire() {
      if (fired) return; fired = true;
      try {
        ifr.contentWindow.focus();
        ifr.contentWindow.print();
        setTimeout(function () { try { document.body.removeChild(ifr); } catch (e) {} }, 4000);
        done(true, "");
      } catch (e) { done(false, "打印异常：" + e); }
    }
    ifr.onload = function () { setTimeout(fire, 250); };
    try { ifr.srcdoc = html; } catch (e) {
      var d = ifr.contentWindow.document; d.open(); d.write(html); d.close();
    }
    setTimeout(fire, 1500);  // 兜底：onload 未触发也打印
  }

  /** 静默打印一个已渲染好的 iframe（自排版兜底用）。 */
  function printFrameNative(frame, done) {
    try {
      frame.contentWindow.focus();
      frame.contentWindow.print();
      done(true, "");
    } catch (e) { done(false, "打印异常：" + e); }
  }

  window.GofoPrint = { getLodop: getLodop, whenReady: whenReady,
    printLabelHtml: printLabelHtml, printPdf: printPdf,
    printImageNative: printImageNative, printFrameNative: printFrameNative };
})();

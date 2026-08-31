/* 站点大屏：轮询队列状态，渲染签退结果 / 打印面单 / 强弱提醒，处理放行/拒绝与 CLodop 打印。 */
(function () {
  "use strict";
  var UI = { strong_diff: 5, weak_diff: 1, sound_on_strong: true,
             label_width_mm: 100, label_height_mm: 150, printer_name: "",
             print_engine: "chrome" };
  var lastJobId = null;
  var soundedFor = {};      // 已播过的“声音”键：jobId + ':' + name
  var printedFor = {};      // 已送打印的 job id
  var actionJobId = null;   // 当前等待放行/拒绝的 job id

  function playOnce(jobId, name, elId){
    var key = jobId + ":" + name;
    if (soundedFor[key]) return;
    soundedFor[key] = true;
    try { var a = document.getElementById(elId); a.currentTime = 0; a.play().catch(function(){}); } catch(e){}
  }

  function $(id){ return document.getElementById(id); }
  function show(view){
    ["idle","signout","print","error"].forEach(function(v){
      $("view-"+v).classList.toggle("hidden", v !== view);
    });
  }
  function setBodyState(s){ document.body.className = "state-"+s; }

  // ---------------- 状态拉取 ----------------
  function loadState(){
    fetch("/api/state").then(function(r){return r.json();}).then(function(d){
      $("site-name").textContent = d.site_name || "GOFO 站点";
      $("ver").textContent = d.version || "-";
      $("ready-dot").className = "dot " + (d.ready ? "ok" : "bad");
      $("status-text").textContent = d.ready ? (d.print_ready ? "就绪" : "就绪（打印未配置）") : d.message;
      $("idle-text").textContent = d.ready ? "请扫码" : "请先到后台配置";
      if (d.ui) { for (var k in d.ui) UI[k] = d.ui[k]; }
    }).catch(function(){
      $("status-text").textContent = "无法连接本地服务";
    });
  }

  // ---------------- 主轮询 ----------------
  function poll(){
    fetch("/api/current").then(function(r){return r.json();}).then(function(snap){
      $("queue-count").textContent = snap.pending || 0;
      $("queue-badge").classList.toggle("hidden", !(snap.pending > 0));
      var job = snap.current || null;
      if (!job){
        // 没有进行中的：短暂显示上一单结果，否则回空闲
        var last = snap.last_done;
        if (last && recentEnough(last)) { render(last, true); }
        else { show("idle"); setBodyState("idle"); actionJobId = null; }
        return;
      }
      render(job, false);
    }).catch(function(){}).finally(function(){
      setTimeout(poll, 350);
    });
  }

  var lastDoneShownAt = {};
  function recentEnough(job){
    var now = Date.now();
    if (!lastDoneShownAt[job.id]) lastDoneShownAt[job.id] = now;
    var age = now - lastDoneShownAt[job.id];
    var ttl = (job.status === "error" || job.level === "strong") ? 7000 : 3000;
    return age < ttl;
  }

  // ---------------- 渲染 ----------------
  function render(job, isDone){
    if (job.status === "error"){
      show("error"); setBodyState("error");
      $("err-msg").textContent = job.error || job.message || "出错了";
      playOnce(job.id, "error", "snd-error");   // 任何意外 → “签退异常”
      return;
    }
    if (job.kind === "signout"){ renderSignout(job, isDone); return; }
    if (job.kind === "print"){ renderPrint(job); return; }
  }

  function renderSignout(job, isDone){
    show("signout");
    var r = job.result || {};
    $("so-driver").textContent = r.driverName || "（未知司机）";
    $("so-line").textContent = [r.deliveryLineName, r.licensePlateNo].filter(Boolean).join(" · ");
    $("c-be").textContent = num(r.beReceiveCount);
    $("c-rec").textContent = num(r.receivedCount);
    $("c-wrong").textContent = num(r.wrongScanCount);
    $("c-diff").textContent = num(r.diff);
    $("so-msg").textContent = job.message || "";

    // 背景：强红 / 弱黄 / 正常
    setBodyState(job.level === "strong" ? "strong" : (job.level === "weak" ? "weak" : "ok"));

    // 语音（每单每种一次；实领0 只播“请先收件”，不叠加“取件量低”，也不播“签退成功”）
    var rec = Number(r.receivedCount || 0);
    if (rec === 0){
      playOnce(job.id, "collect", "snd-collect");            // 实领0 → 只播“请先收件”
    } else if (job.status === "done"){
      playOnce(job.id, "success", "snd-success");            // 放行/签退成功 → “签退成功”
    } else if (job.status === "awaiting_action" && job.level === "strong" && r.sound_on_strong){
      playOnce(job.id, "strong", "snd-strong");              // 强提醒 → “取件量低”
    }

    // 是否等待人工
    var waiting = job.status === "awaiting_action";
    $("action-bar").classList.toggle("hidden", !waiting);
    $("so-done").classList.toggle("hidden", waiting || job.status === "processing");
    if (waiting){ actionJobId = job.id; }
    else if (job.status === "done"){ $("so-done").textContent = job.message || "已完成"; actionJobId = null; }
  }

  function renderPrint(job){
    show("print"); setBodyState("print");
    var res = job.result || {};
    var label = res.label || {};
    $("print-title").textContent = "打印面单";
    $("print-way").textContent = res.waybillNo || label.waybillNo || job.code || "";
    if (job.status === "printing" && !printedFor[job.id]){
      printedFor[job.id] = true;
      $("print-status").textContent = "正在送打印…";
      if (res.image_b64){ doPrintImage(job.id, res.image_b64); }
      else { doPrint(job.id, label); }
    } else if (job.status === "done"){
      $("print-status").textContent = "✓ " + (job.message || "已打印");
    }
  }

  // 官方面单图片打印。默认走浏览器静默打印（免费无水印）；选 clodop 时用 CLodop。
  function doPrintImage(jobId, dataurl){
    var w = UI.label_width_mm, h = UI.label_height_mm;
    function cb(ok, err){
      $("print-status").textContent = ok ? "✓ 已送打印" : ("打印失败：" + err);
      finishPrint(jobId, ok, err);
    }
    if (UI.print_engine === "clodop"){
      var html = '<img src="' + dataurl + '" style="width:' + w + 'mm;height:' + h + 'mm;display:block">';
      GofoPrint.printLabelHtml(html, { widthMM:w, heightMM:h, printerName:UI.printer_name }, cb);
    } else {
      GofoPrint.printImageNative(dataurl, { widthMM:w, heightMM:h }, cb);
    }
  }

  // ---------------- CLodop 打印（自排版兜底） ----------------
  function doPrint(jobId, label){
    var frame = $("labelframe");
    function afterRender(){
      function cb(ok, err){
        $("print-status").textContent = ok ? "✓ 已送打印" : ("打印失败：" + err);
        finishPrint(jobId, ok, err);
      }
      if (UI.print_engine === "clodop"){
        var html;
        try { html = frame.contentWindow.document.documentElement.outerHTML; }
        catch(e){ finishPrint(jobId, false, "无法读取面单内容"); return; }
        GofoPrint.printLabelHtml(html, {
          widthMM: UI.label_width_mm, heightMM: UI.label_height_mm, printerName: UI.printer_name
        }, cb);
      } else {
        GofoPrint.printFrameNative(frame, cb);   // 浏览器静默打印 iframe
      }
    }
    try {
      frame.contentWindow.renderLabel(label);
      setTimeout(afterRender, 350);   // 等条码/QR 生成
    } catch(e){
      // iframe 可能还没加载完，稍后重试一次
      setTimeout(function(){
        try { frame.contentWindow.renderLabel(label); setTimeout(afterRender, 350); }
        catch(e2){ finishPrint(jobId, false, "面单渲染失败"); }
      }, 500);
    }
  }

  function finishPrint(jobId, ok, err){
    fetch("/api/print-done", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({job_id: jobId, ok: ok, error: err || ""})
    }).catch(function(){});
  }

  // ---------------- 放行 / 拒绝 ----------------
  $("btn-pass").addEventListener("click", function(){
    if (!actionJobId) return;
    var id = actionJobId; actionJobId = null;
    fetch("/api/action/release", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({job_id:id})}).catch(function(){});
  });
  $("btn-reject").addEventListener("click", function(){
    if (!actionJobId) return;
    var id = actionJobId; actionJobId = null;
    fetch("/api/action/intercept", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({job_id:id, remark:""})}).catch(function(){});
  });

  // ---------------- 键盘/HID 扫码兜底 ----------------
  var kb = $("kb");
  function refocus(){ try { kb.focus(); } catch(e){} }
  document.addEventListener("click", refocus);
  setInterval(refocus, 1500);
  refocus();
  kb.addEventListener("keydown", function(e){
    if (e.key === "Enter"){
      var code = kb.value.trim();
      kb.value = "";
      if (code){
        fetch("/api/scan", {method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({code: code, source:"kb"})}).catch(function(){});
      }
      e.preventDefault();
    }
  });

  function num(v){ return (v===null||v===undefined) ? "0" : String(v); }

  loadState();
  setInterval(loadState, 8000);
  poll();
})();

/* 后台逻辑：登录、DMS 账号、打印 token、提醒设置、串口、更新、日志、改密码。 */
(function(){
  "use strict";
  function $(id){ return document.getElementById(id); }
  function api(path, opt){ return fetch(path, opt).then(function(r){ return r.json(); }); }
  function post(path, body){
    return api(path, { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body||{}) });
  }
  function msg(el, text, ok){ el.textContent = text; el.className = "msg " + (ok===false?"bad":(ok?"good":"")); }

  // ---------- 登录 ----------
  function doLogin(){
    post("/api/admin/login", { password: $("login-pw").value }).then(function(d){
      if (d.ok){ enter(); } else { $("login-err").textContent = d.message || "登录失败"; }
    });
  }
  $("login-btn").addEventListener("click", doLogin);
  $("login-pw").addEventListener("keydown", function(e){ if(e.key==="Enter") doLogin(); });

  function enter(){
    $("login").classList.add("hidden");
    $("app").classList.remove("hidden");
    api("/api/admin/me").then(function(d){
      $("ver").textContent = d.version || "-";
      $("enc-note").textContent = d.encryption ? "密码加密存储：已启用" : "⚠ 未启用加密（缺 cryptography），密码将明文存储";
      var hint = $("auto-hint"), btn = $("auto-login");
      if (d.captcha_solver === false){
        if (hint) hint.innerHTML = "⚠ 本机未安装验证码识别库 <code>ddddocr</code>，自动登录不可用，请用下面「方式二」手动输码。";
        if (btn) btn.disabled = true;
      }
    });
    loadDms(); loadPrint(); loadSettings(); loadUpdate();
  }

  // 首次判断是否已登录
  api("/api/admin/me").then(function(d){ if (d.logged_in) enter(); });

  $("logout").addEventListener("click", function(){
    post("/api/admin/logout", {}).then(function(){ location.reload(); });
  });

  // ---------- Tabs ----------
  document.querySelectorAll(".tabs button").forEach(function(b){
    b.addEventListener("click", function(){
      document.querySelectorAll(".tabs button").forEach(function(x){ x.classList.remove("active"); });
      b.classList.add("active");
      var tab = b.getAttribute("data-tab");
      document.querySelectorAll(".panel").forEach(function(p){
        p.classList.toggle("hidden", p.getAttribute("data-panel") !== tab);
      });
      if (tab === "serial") loadSerial();
      if (tab === "logs") loadLogs();
      if (tab === "alert") loadSettings();
    });
  });

  // ---------- DMS ----------
  function loadDms(){
    api("/api/admin/dms").then(function(d){
      if (!d.ok) return;
      var m = d.dms;
      $("dms-name").value = m.name || "";
      $("dms-username").value = m.username || "";
      $("dms-timezone").value = m.timezone || "America/Los_Angeles";
      $("dms-site").value = m.site_perm || "";
      $("dms-password").placeholder = m.has_password ? "已设置（留空不改）" : "DMS 密码";
    });
  }
  $("dms-save").addEventListener("click", function(){
    post("/api/admin/dms", {
      name:$("dms-name").value, username:$("dms-username").value,
      password:$("dms-password").value, timezone:$("dms-timezone").value,
      site_perm:$("dms-site").value
    }).then(function(d){ msg($("dms-msg"), d.ok?"已保存":"保存失败", d.ok); $("dms-password").value=""; loadDms(); });
  });
  $("dms-test").addEventListener("click", function(){
    msg($("dms-msg"), "测试中…");
    post("/api/admin/dms/test", {
      username:$("dms-username").value, password:$("dms-password").value,
      timezone:$("dms-timezone").value
    }).then(function(d){ msg($("dms-msg"), (d.message||"") + (d.sites&&d.sites.length?("｜可用站点："+d.sites.join(", ")):""), d.ok); });
  });

  // ---------- 打印 token ----------
  function loadPrint(){ /* token 不回显，仅可覆盖 */ }

  // 自动登录（ddddocr）
  $("auto-login").addEventListener("click", function(){
    msg($("auto-msg"), "自动识别验证码登录中，请稍候…");
    post("/api/admin/print-login/auto", {}).then(function(d){
      msg($("auto-msg"), d.message, d.ok);
    });
  });

  var capUuid = "";
  function loadCaptcha(){
    msg($("login-msg"), "获取验证码中…");
    api("/api/admin/print-login/captcha").then(function(d){
      if(!d.ok){ msg($("login-msg"), d.message||"取验证码失败", false); return; }
      capUuid = d.uuid;
      var img=$("cap-img"); img.src=d.img; img.style.display="inline-block";
      $("cap-code").style.display="inline-block"; $("cap-login").style.display="inline-block";
      $("cap-code").value=""; $("cap-code").focus();
      msg($("login-msg"), "请输入图中验证码后点「登录并保存 token」");
    });
  }
  $("cap-get").addEventListener("click", loadCaptcha);
  $("cap-img").addEventListener("click", loadCaptcha);   // 点图换一张
  $("cap-login").addEventListener("click", function(){
    msg($("login-msg"), "登录中…");
    post("/api/admin/print-login", { code:$("cap-code").value, uuid:capUuid }).then(function(d){
      msg($("login-msg"), d.message, d.ok);
      if(!d.ok) loadCaptcha();   // 失败自动换一张验证码
    });
  });
  $("cap-code").addEventListener("keydown", function(e){ if(e.key==="Enter") $("cap-login").click(); });
  $("token-save").addEventListener("click", function(){
    post("/api/admin/print-token", { token: $("print-token").value }).then(function(d){
      msg($("print-msg"), d.ok?"token 已保存":"保存失败", d.ok);
    });
  });
  $("token-test").addEventListener("click", function(){
    msg($("print-msg"), "查单测试中…");
    post("/api/admin/print-token/test", {
      token: $("print-token").value || null, scanNumber: $("token-test-no").value
    }).then(function(d){ msg($("print-msg"), d.message, d.ok); });
  });

  // ---------- 设置（提醒 + 尺寸）----------
  var CURRENT = {};
  function loadSettings(){
    api("/api/admin/settings").then(function(d){
      if(!d.ok) return; CURRENT = d.settings; var s=d.settings;
      $("s-strong").value=s.strong_diff; $("s-weak").value=s.weak_diff;
      $("s-wrong").checked=!!s.strong_on_wrongscan; $("s-sound").checked=!!s.sound_on_strong;
      $("s-autopass").value=s.auto_pass_diff;
      $("s-engine").value=s.print_engine||"chrome";
      $("s-lw").value=s.label_width_mm; $("s-lh").value=s.label_height_mm; $("s-printer").value=s.printer_name||"";
      $("s-baud").value=s.serial_baud;
    });
  }
  $("alert-save").addEventListener("click", function(){
    post("/api/admin/settings", {
      strong_diff:parseInt($("s-strong").value||"0",10),
      weak_diff:parseInt($("s-weak").value||"0",10),
      strong_on_wrongscan:$("s-wrong").checked,
      sound_on_strong:$("s-sound").checked,
      auto_pass_diff:parseInt($("s-autopass").value||"0",10),
      print_engine:$("s-engine").value,
      label_width_mm:parseInt($("s-lw").value||"100",10),
      label_height_mm:parseInt($("s-lh").value||"150",10),
      printer_name:$("s-printer").value
    }).then(function(d){ msg($("alert-msg"), d.ok?"已保存":"保存失败", d.ok); });
  });

  // ---------- 串口 ----------
  function loadSerial(){
    if ($("s-baud").value==="" ) loadSettings();
    api("/api/admin/settings").then(function(s){
      var chosen = (s.settings && s.settings.serial_ports) || [];
      api("/api/admin/serial-ports").then(function(d){
        var ports = d.ports || [];
        if (!ports.length){ $("serial-list").innerHTML = "未检测到串口（未安装 pyserial 或无设备）。可在此手动填：<input id='serial-manual' placeholder='COM3,COM4' style=\"width:220px\">"; return; }
        var html = ports.map(function(p){
          var on = chosen.indexOf(p.device)>=0 ? "checked" : "";
          return "<label class='ck'><input type='checkbox' class='sp' value='"+p.device+"' "+on+"> "+p.device+" — "+(p.desc||"")+"</label>";
        }).join("");
        $("serial-list").innerHTML = html;
      });
    });
  }
  $("serial-refresh").addEventListener("click", loadSerial);
  $("serial-save").addEventListener("click", function(){
    var chosen = Array.prototype.map.call(document.querySelectorAll(".sp:checked"), function(x){return x.value;});
    var manual = $("serial-manual");
    if (manual && manual.value) chosen = manual.value.split(",").map(function(x){return x.trim();}).filter(Boolean);
    post("/api/admin/settings", { serial_ports: chosen, serial_baud: parseInt($("s-baud").value||"9600",10) })
      .then(function(d){ msg($("serial-msg"), d.ok?"已保存，重启程序生效":"保存失败", d.ok); });
  });

  // ---------- 更新 ----------
  function loadUpdate(){
    api("/api/admin/settings").then(function(d){
      if(!d.ok) return; $("u-repo").value=d.settings.update_repo||""; $("u-auto").checked=!!d.settings.auto_update;
    });
    api("/api/admin/version").then(function(d){ $("u-cur").textContent=d.version; });
  }
  $("u-save").addEventListener("click", function(){
    post("/api/admin/settings", { update_repo:$("u-repo").value, auto_update:$("u-auto").checked })
      .then(function(d){ msg($("update-msg"), d.ok?"已保存":"保存失败", d.ok); });
  });
  $("u-check").addEventListener("click", function(){
    msg($("update-msg"), "检查中…");
    post("/api/admin/update/check", {}).then(function(d){
      if (d.error){ msg($("update-msg"), d.error, false); return; }
      msg($("update-msg"), d.has_update ? ("发现新版本 v"+d.latest+"："+(d.notes||"")) : ("已是最新 v"+d.current), true);
    });
  });
  $("u-apply").addEventListener("click", function(){
    if(!confirm("立即从 GitHub 拉取更新并重启程序？")) return;
    msg($("update-msg"), "更新中，请稍候…");
    post("/api/admin/update/apply", {}).then(function(d){
      msg($("update-msg"), d.message || (d.ok?"完成":"失败"), d.ok);
    });
  });

  // ---------- 日志 ----------
  function loadLogs(){
    api("/api/admin/logs?limit=150").then(function(d){
      if(!d.ok){ $("logs-table").textContent="加载失败"; return; }
      if(!d.logs.length){ $("logs-table").innerHTML="<div class='muted'>暂无日志</div>"; return; }
      var rows = d.logs.map(function(e){
        var extra = [];
        ["level","diff","wrong","driver","action","result_msg","error","message"].forEach(function(k){
          if(e[k]!==undefined && e[k]!==null && e[k]!=="") extra.push(k+"="+e[k]);
        });
        return "<tr><td>"+(e.ts||"")+"</td><td>"+(e.kind||"")+"</td><td class='mono'>"+(e.code||"")+"</td><td>"+extra.join(" · ")+"</td></tr>";
      }).join("");
      $("logs-table").innerHTML = "<table><thead><tr><th>时间</th><th>类型</th><th>单号</th><th>详情</th></tr></thead><tbody>"+rows+"</tbody></table>";
    });
  }
  $("logs-refresh").addEventListener("click", loadLogs);
  $("logs-download").addEventListener("click", function(){
    var a=document.createElement("a");
    a.href="/api/admin/logs/download"; a.download="";
    document.body.appendChild(a); a.click(); a.remove();
  });

  // ---------- 改密码 ----------
  $("pw-save").addEventListener("click", function(){
    post("/api/admin/password", { password: $("new-pw").value }).then(function(d){
      msg($("pw-msg"), d.ok?"已修改":(d.message||"失败"), d.ok); if(d.ok) $("new-pw").value="";
    });
  });
})();

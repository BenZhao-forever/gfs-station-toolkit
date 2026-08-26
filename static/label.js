/* 把 getLabelInfo 返回的 data[0] 渲染成 4x6 面单。 */
function _t(v){ return (v === null || v === undefined || v === "") ? "" : String(v); }

function renderLabel(d){
  d = d || {};
  var waybill = _t(d.waybillNo) || _t(d.scanNumber) || _t(d.orderNo);

  // 标题 / 产品类型
  document.getElementById("l-title").textContent = _t(d.labelTitle) || "GOFO";
  document.getElementById("l-prod").textContent = (_t(d.productType) || "ECO").toUpperCase();

  // 收件人
  document.getElementById("l-name").textContent = _t(d.consigneeName) || "—";
  var street = [ _t(d.consigneeStreet), _t(d.consigneeNumIn) ].filter(Boolean).join(" ");
  document.getElementById("l-street").textContent = street || "—";
  var cs = [ _t(d.consigneeCity), _t(d.consigneeState) ].filter(Boolean).join(", ");
  cs = [cs, _t(d.consigneeCode)].filter(Boolean).join("  ");
  document.getElementById("l-citystate").textContent = cs || "—";

  // 条码（Code128，横向）+ 可读单号
  document.getElementById("l-waybill").textContent = waybill || "—";
  if (waybill && window.JsBarcode){
    try {
      JsBarcode("#l-barcode", waybill, {
        format:"CODE128", displayValue:false, margin:0,
        width:2, height:80
      });
    } catch(e){ console.warn("barcode", e); }
  }

  // 路由三格：region | pickupPoint | roadArea
  document.getElementById("l-region").textContent  = _t(d.region) || "—";
  document.getElementById("l-pickup").textContent  = _t(d.pickupPoint) || "—";
  document.getElementById("l-roadarea").textContent = _t(d.roadArea) || "—";

  // 寄件人
  var from = [ _t(d.shipperName), _t(d.shipperStreet),
    [_t(d.shipperCity), _t(d.shipperState), _t(d.shipperCode)].filter(Boolean).join(" ") ]
    .filter(Boolean).join("\n");
  document.getElementById("l-from").textContent = from || "—";
  document.getElementById("l-from").style.whiteSpace = "pre-line";

  // QR：编码单号，便于二次扫描
  var qrEl = document.getElementById("l-qr");
  qrEl.innerHTML = "";
  if (waybill && window.QRCode){
    try {
      var holder = document.createElement("div");
      new QRCode(holder, { text: waybill, width:120, height:120,
        correctLevel: QRCode.CorrectLevel.M });
      // 转成 <img data-url>，保证 CLodop 的 HTML 打印能捕获（canvas 不会随 HTML 序列化）
      var cv = holder.querySelector("canvas");
      if (cv){
        var img = new Image();
        img.src = cv.toDataURL("image/png");
        qrEl.appendChild(img);
      } else {
        qrEl.appendChild(holder);   // 降级：table 版 QR
      }
    } catch(e){ console.warn("qr", e); }
  }

  // 分拣标记
  document.getElementById("l-sortcode").textContent = _t(d.sortingCode) || "—";
  document.getElementById("l-fourcode").textContent = _t(d.fourCode) || "";
  // 大字标记：优先 roadArea，退到 fourCode 末段
  var mark = _t(d.roadArea);
  if (!mark && d.fourCode){ var seg = String(d.fourCode).split("-"); mark = seg[seg.length-1]; }
  document.getElementById("l-mark").textContent = mark || "";
}

window.renderLabel = renderLabel;

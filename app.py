# -*- coding: utf-8 -*-
"""
GOFO 站点工具包 · 本地服务（每台机器本地运行）。

一台机器 = 一个本地 Flask + 全屏 Chromium 大屏（/kiosk）+ 本地后台（/admin）。
两把 USB-COM 扫码枪由 serial_reader 读入，统一进一个串行队列：
  - 扫到 QR_ 开头 → 司机签退（scanSignOut）。数对不上/有错扫/接口要求 → 阻塞队列，
    等员工在大屏点“放行/拒绝”。强提醒红底+播“取件量低”，弱提醒黄底。
  - 其余条码 → 自动打印面单（getLabelInfo → 大屏 CLodop 打印）。
队列串行：签退需人工放行时，后面的面单会排队等待，放行/拒绝后才继续。
"""

import os
import re
import queue
import threading
import time

from flask import (
    Flask, Response, jsonify, redirect, render_template, request, session,
)

from store import Store, encryption_active
from gfs_client import GfsClient, LoginError
from dms_web_client import DmsWebClient, WebAuthError
import updater
import serial_reader

APP_VERSION = updater.LOCAL_VERSION

app = Flask(__name__)
store = Store()
app.secret_key = store.secret_key()
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# 签退码：QR_ 开头（QR_<uuid>）。其余一律当面单条码打印。
SIGNOUT_RE = re.compile(r"^QR_", re.IGNORECASE)


def is_signout_code(code):
    return bool(SIGNOUT_RE.match(code or ""))


# ================================================================= 客户端（按 DMS 账号构建，签名变化即重建）
_client_lock = threading.Lock()
_pda = {"sig": None, "client": None}
_web = {"token": None, "client": None}


def pda_client():
    """返回 (client, dms) 或 (None, dms)。"""
    dms = store.get_dms(with_password=True)
    if not dms.get("username") or not dms.get("password"):
        return None, dms
    sig = store.dms_signature()
    with _client_lock:
        if _pda["sig"] != sig or _pda["client"] is None:
            _pda["client"] = GfsClient(
                username=dms["username"], password=dms["password"],
                timezone=dms.get("timezone"), site_perm=dms.get("site_perm"),
            )
            _pda["sig"] = sig
    return _pda["client"], dms


def web_client():
    token = store.get_print_token()
    dms = store.get_dms(with_password=True)
    with _client_lock:
        if _web["client"] is None:
            _web["client"] = DmsWebClient(
                username=dms.get("username"), password=dms.get("password"),
                token=token, timezone=dms.get("timezone"),
            )
            _web["token"] = token
        elif _web["token"] != token:
            _web["client"].set_token(token)
            _web["token"] = token
    return _web["client"]


def _invalidate_clients():
    with _client_lock:
        _pda["client"] = None
        _pda["sig"] = None
        _web["client"] = None
        _web["token"] = None


# ================================================================= 串行队列
class Job:
    _seq = 0
    _seq_lock = threading.Lock()

    def __init__(self, code, source="scan"):
        with Job._seq_lock:
            Job._seq += 1
            self.id = Job._seq
        self.code = code
        self.source = source
        self.kind = "signout" if is_signout_code(code) else "print"
        self.status = "queued"      # queued|processing|awaiting_action|printing|done|error
        self.result = None           # 展示数据
        self.level = "none"          # none|weak|strong（签退提醒等级）
        self.message = ""
        self.error = ""
        self.created = time.time()
        self.finished = None

    def public(self):
        return {
            "id": self.id, "code": self.code, "kind": self.kind,
            "status": self.status, "level": self.level,
            "message": self.message, "error": self.error,
            "result": self.result,
        }


class Worker:
    """单线程串行处理。当前 job 需要人工/打印回执时阻塞，直到大屏回传决定。"""

    def __init__(self):
        self.incoming = queue.Queue()
        self.cond = threading.Condition()
        self.current = None          # 正在处理/等待的 Job
        self.last_done = None        # 最近完成的 Job（供大屏短暂展示）
        self._action = None          # 大屏回传：{"type":"release/intercept/print_done", ...}
        self._pending = 0
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    # 入队
    def enqueue(self, code, source="scan"):
        code = (code or "").strip()
        if not code:
            return None
        job = Job(code, source)
        with self.cond:
            self._pending += 1
        self.incoming.put(job)
        return job

    def queue_len(self):
        with self.cond:
            return self._pending

    # 大屏回传动作
    def submit_action(self, job_id, action):
        with self.cond:
            if self.current and self.current.id == job_id and \
                    self.current.status in ("awaiting_action", "printing"):
                self._action = action
                self.cond.notify_all()
                return True
        return False

    def snapshot(self):
        with self.cond:
            cur = self.current.public() if self.current else None
            last = self.last_done.public() if self.last_done else None
            return {"current": cur, "last_done": last, "pending": self._pending}

    def _wait_action(self, job, valid_types, timeout=None):
        """阻塞等待大屏回传指定类型的动作，返回 action dict；超时返回 None。
        timeout=None 表示无限等待（用于等员工放行/拒绝）。"""
        deadline = None if timeout is None else (time.time() + timeout)
        with self.cond:
            while True:
                if self._action and self._action.get("type") in valid_types:
                    a = self._action
                    self._action = None
                    return a
                if deadline is not None:
                    remain = deadline - time.time()
                    if remain <= 0:
                        return None
                    self.cond.wait(timeout=min(2.0, remain))
                else:
                    self.cond.wait(timeout=2.0)

    def _run(self):
        while True:
            job = self.incoming.get()
            with self.cond:
                self.current = job
                job.status = "processing"
            try:
                if job.kind == "signout":
                    self._do_signout(job)
                else:
                    self._do_print(job)
            except Exception as e:  # noqa
                job.status = "error"
                job.error = str(e)
                store.append_log({"kind": "exception", "code": job.code, "error": str(e)})
            job.finished = time.time()
            with self.cond:
                self._pending = max(0, self._pending - 1)
                self.last_done = job
                self.current = None
                self._action = None

    # ---------- 签退 ----------
    def _do_signout(self, job):
        client, dms = pda_client()
        if client is None:
            job.status = "error"
            job.error = "未配置 DMS 站点账号，请在后台设置"
            return
        res = client.sign_out(job.code)
        s = store.get_settings()
        diff = res.get("diff", 0)
        wrong = res.get("wrongScanCount", 0)
        # 提醒等级
        strong = (
            diff > s["strong_diff"]
            or (s["strong_on_wrongscan"] and wrong > 0)
            or (res.get("result") not in (1, None))
        )
        if strong:
            level = "strong"
        elif diff >= s["weak_diff"]:
            level = "weak"
        else:
            level = "none"
        job.level = level
        job.result = {
            "beReceiveCount": res.get("beReceiveCount"),
            "receivedCount": res.get("receivedCount"),
            "wrongScanCount": res.get("wrongScanCount"),
            "returnCount": res.get("returnCount"),
            "diff": diff,
            "driverName": res.get("driverName"),
            "licensePlateNo": res.get("licensePlateNo"),
            "deliveryLineName": res.get("deliveryLineName"),
            "signTime": res.get("signTime"),
            "signResult": res.get("result"),
            "sound_on_strong": bool(s["sound_on_strong"]),
        }
        job.message = res.get("message") or ""

        # 是否需要人工：接口要求 / 数对不上 / 有错扫
        needs_action = res.get("needs_release") or diff != 0 or wrong > 0
        if not needs_action:
            job.status = "done"
            job.message = "签退成功"
            return

        # 阻塞队列，等大屏放行/拒绝
        job.status = "awaiting_action"
        if res.get("result") != 1:
            store.append_log({
                "kind": "signout_hold", "code": job.code, "level": level,
                "diff": diff, "wrong": wrong, "driver": res.get("driverName"),
                "message": job.message, "raw": res.get("raw"),
            })
        action = self._wait_action(job, ("release", "intercept"))

        srid = res.get("signRecordId")
        duid = res.get("driverUserId")
        try:
            if action["type"] == "release":
                if srid is not None:
                    ok, msg = client.sign_out_pass(srid, duid)
                else:
                    ok, msg = True, "已放行"
                job.status = "done" if ok else "error"
                job.message = "已放行" if ok else f"放行失败：{msg}"
                if not ok:
                    job.error = msg
            else:  # intercept
                remark = action.get("remark") or ""
                if srid is not None:
                    ok, msg = client.sign_out_intercept(srid, duid, remark)
                else:
                    ok, msg = True, "已拒绝"
                job.status = "done" if ok else "error"
                job.message = "已拒绝" if ok else f"拒绝失败：{msg}"
                if not ok:
                    job.error = msg
        except Exception as e:  # noqa
            job.status = "error"
            job.error = str(e)
            job.message = f"操作异常：{e}"
        store.append_log({
            "kind": "signout_action", "code": job.code,
            "action": action["type"], "result_msg": job.message,
        })

    # ---------- 打印 ----------
    def _do_print(self, job):
        web = web_client()
        if not web.has_token:
            job.status = "error"
            job.error = "未配置打印 token，请在后台设置"
            store.append_log({"kind": "print_no_token", "code": job.code})
            return
        try:
            ok, data = web.get_label_info(job.code)
        except WebAuthError as e:
            job.status = "error"
            job.error = str(e)
            store.append_log({"kind": "print_auth_error", "code": job.code, "error": str(e)})
            return
        if not ok:
            job.status = "error"
            job.error = data
            store.append_log({"kind": "print_query_fail", "code": job.code, "error": data})
            return

        job.result = {"label": data}
        job.message = f"打印面单 {data.get('waybillNo') or job.code}"
        job.status = "printing"
        # 等大屏 CLodop 打印回执（90s 超时，避免大屏未开时永久卡队列）
        action = self._wait_action(job, ("print_done",), timeout=90)
        if action is None:
            job.status = "error"
            job.error = "打印超时（大屏未响应/未开）"
            store.append_log({"kind": "print_timeout", "code": job.code})
            return
        if action.get("ok"):
            job.status = "done"
            job.message = f"已打印 {data.get('waybillNo') or job.code}"
        else:
            job.status = "error"
            job.error = action.get("error") or "打印失败"
            store.append_log({"kind": "print_fail", "code": job.code, "error": job.error})


worker = Worker()


def _print_token_keepalive():
    """定期用 getInfo 续期打印 token，避免空闲时过期。失效则记日志（需人工重新在线登录）。"""
    warned = False
    while True:
        time.sleep(600)  # 每 10 分钟
        try:
            if not store.get_print_token():
                continue
            web = web_client()
            alive = web.keepalive()
            if alive:
                warned = False
            elif not warned:
                warned = True
                store.append_log({"kind": "print_token_expired",
                                  "message": "打印 token 已失效，请到后台「打印」页在线重新登录"})
        except Exception:
            pass


threading.Thread(target=_print_token_keepalive, daemon=True).start()


# ================================================================= 页面
@app.route("/")
def root():
    return redirect("/kiosk")


@app.route("/kiosk")
def kiosk_page():
    return render_template("kiosk.html", version=APP_VERSION)


@app.route("/admin")
def admin_page():
    return render_template("admin.html", version=APP_VERSION)


@app.route("/label")
def label_page():
    return render_template("label.html")


# ================================================================= 运行时（大屏）
@app.route("/api/state")
def api_state():
    dms = store.get_dms()
    s = store.get_settings()
    web = web_client()
    ready = bool(dms.get("username") and dms.get("has_password"))
    msg = "就绪，请扫码" if ready else "未配置 DMS 账号，请到后台设置"
    return jsonify({
        "ready": ready,
        "message": msg,
        "site_name": dms.get("name") or dms.get("site_perm") or dms.get("username") or "",
        "print_ready": web.has_token,
        "version": APP_VERSION,
        "ui": {
            "strong_diff": s["strong_diff"],
            "weak_diff": s["weak_diff"],
            "sound_on_strong": s["sound_on_strong"],
            "label_width_mm": s["label_width_mm"],
            "label_height_mm": s["label_height_mm"],
            "printer_name": s["printer_name"],
        },
    })


@app.route("/api/current")
def api_current():
    return jsonify(worker.snapshot())


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """键盘/网页输入的扫码入口（串口扫码枪走 serial_reader 直接 enqueue）。"""
    body = request.get_json(silent=True) or {}
    code = (body.get("code") or "").strip()
    if not code:
        return jsonify({"ok": False, "message": "扫码内容为空"}), 400
    job = worker.enqueue(code, source=body.get("source") or "web")
    return jsonify({"ok": True, "job_id": job.id, "kind": job.kind})


@app.route("/api/action/release", methods=["POST"])
def api_release():
    body = request.get_json(silent=True) or {}
    ok = worker.submit_action(int(body.get("job_id") or 0), {"type": "release"})
    return jsonify({"ok": ok})


@app.route("/api/action/intercept", methods=["POST"])
def api_intercept():
    body = request.get_json(silent=True) or {}
    ok = worker.submit_action(
        int(body.get("job_id") or 0),
        {"type": "intercept", "remark": body.get("remark") or ""},
    )
    return jsonify({"ok": ok})


@app.route("/api/print-done", methods=["POST"])
def api_print_done():
    body = request.get_json(silent=True) or {}
    ok = worker.submit_action(
        int(body.get("job_id") or 0),
        {"type": "print_done", "ok": bool(body.get("ok")), "error": body.get("error") or ""},
    )
    return jsonify({"ok": ok})


# ================================================================= 后台鉴权
def _require_admin():
    return bool(session.get("admin"))


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    body = request.get_json(silent=True) or {}
    if store.check_admin_password(body.get("password", "")):
        session["admin"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "message": "密码错误"}), 403


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/admin/me")
def admin_me():
    return jsonify({
        "logged_in": _require_admin(),
        "encryption": encryption_active(),
        "version": APP_VERSION,
    })


@app.route("/api/admin/password", methods=["POST"])
def admin_password():
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    body = request.get_json(silent=True) or {}
    try:
        store.set_admin_password(body.get("password", ""))
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    return jsonify({"ok": True})


# ---------------- DMS 账号
@app.route("/api/admin/dms")
def admin_get_dms():
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    return jsonify({"ok": True, "dms": store.get_dms()})


@app.route("/api/admin/dms", methods=["POST"])
def admin_set_dms():
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    b = request.get_json(silent=True) or {}
    store.set_dms(
        username=b.get("username"), password=b.get("password") or "",
        timezone=b.get("timezone"), site_perm=b.get("site_perm"), name=b.get("name"),
    )
    _invalidate_clients()
    return jsonify({"ok": True})


@app.route("/api/admin/dms/test", methods=["POST"])
def admin_test_dms():
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    b = request.get_json(silent=True) or {}
    username = (b.get("username") or "").strip()
    password = b.get("password") or ""
    if not username or not password:
        # 用已保存的
        saved = store.get_dms(with_password=True)
        username = username or saved.get("username")
        password = password or saved.get("password")
    if not username or not password:
        return jsonify({"ok": False, "message": "请填账号和密码"}), 200
    client = GfsClient(username=username, password=password,
                       timezone=b.get("timezone"))
    try:
        client.login()
    except LoginError as e:
        return jsonify({"ok": False, "message": str(e)}), 200
    except Exception as e:  # noqa
        return jsonify({"ok": False, "message": f"网络/服务器异常：{e}"}), 200
    sites, current = [], None
    try:
        for st in client.list_sites():
            if st.get("groupName"):
                sites.append(st["groupName"])
                if st.get("selected"):
                    current = st["groupName"]
    except Exception:
        pass
    msg = "登录成功，账号可用" + (f"（当前站点 {current}）" if current else "")
    return jsonify({"ok": True, "message": msg, "sites": sites, "current": current})


# ---------------- 打印 token
@app.route("/api/admin/print-token", methods=["POST"])
def admin_set_print_token():
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    b = request.get_json(silent=True) or {}
    store.set_print_token(b.get("token") or "")
    _invalidate_clients()
    return jsonify({"ok": True})


@app.route("/api/admin/print-login/captcha")
def admin_print_captcha():
    """取一张 DMS 网页版登录验证码，供后台在线登录获取打印 token。"""
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    web = web_client()
    try:
        cap = web.get_captcha()
    except Exception as e:  # noqa
        return jsonify({"ok": False, "message": f"取验证码失败：{e}"}), 200
    return jsonify({"ok": True, **cap})


@app.route("/api/admin/print-login", methods=["POST"])
def admin_print_login():
    """用已保存的 DMS 账号密码 + 验证码在线登录，拿到打印 token 并保存。"""
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    dms = store.get_dms()
    if not dms.get("username") or not dms.get("has_password"):
        return jsonify({"ok": False, "message": "请先在「DMS 账号」保存账号密码"}), 200
    b = request.get_json(silent=True) or {}
    web = web_client()
    try:
        ok, msg = web.login_with_captcha(b.get("code"), b.get("uuid"))
    except Exception as e:  # noqa
        return jsonify({"ok": False, "message": f"登录异常：{e}"}), 200
    if ok:
        store.set_print_token(web._token)
        _invalidate_clients()
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/admin/print-token/test", methods=["POST"])
def admin_test_print_token():
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    b = request.get_json(silent=True) or {}
    scan = (b.get("scanNumber") or "").strip()
    if not scan:
        return jsonify({"ok": False, "message": "请填一个测试单号"}), 200
    token = b.get("token")
    web = DmsWebClient(token=token) if token else web_client()
    try:
        ok, data = web.get_label_info(scan)
    except WebAuthError as e:
        return jsonify({"ok": False, "message": str(e)}), 200
    except Exception as e:  # noqa
        return jsonify({"ok": False, "message": f"异常：{e}"}), 200
    if not ok:
        return jsonify({"ok": False, "message": data}), 200
    return jsonify({"ok": True, "message": f"查单成功：{data.get('waybillNo')}（{data.get('region')} / {data.get('roadArea')}）"})


# ---------------- 设置
@app.route("/api/admin/settings")
def admin_get_settings():
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    return jsonify({"ok": True, "settings": store.get_settings()})


@app.route("/api/admin/settings", methods=["POST"])
def admin_set_settings():
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    patch = request.get_json(silent=True) or {}
    s = store.set_settings(patch)
    return jsonify({"ok": True, "settings": s})


# ---------------- 日志
@app.route("/api/admin/serial-ports")
def admin_serial_ports():
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    return jsonify({"ok": True, "ports": serial_reader.list_ports()})


@app.route("/api/admin/logs")
def admin_logs():
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        limit = 100
    return jsonify({"ok": True, "logs": store.read_logs(limit)})


# ---------------- 更新
@app.route("/api/admin/version")
def admin_version():
    return jsonify({"ok": True, "version": APP_VERSION})


@app.route("/api/admin/update/check", methods=["POST"])
def admin_update_check():
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    repo = store.get_settings().get("update_repo") or ""
    info = updater.check_update(repo)
    return jsonify({"ok": True, **info})


@app.route("/api/admin/update/apply", methods=["POST"])
def admin_update_apply():
    if not _require_admin():
        return jsonify({"ok": False, "message": "未登录"}), 403
    repo = store.get_settings().get("update_repo") or ""
    res = updater.apply_update(repo)
    return jsonify(res)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    # 后台自动检查更新（若开启且配了仓库）
    try:
        updater.maybe_background_update(store)
    except Exception:
        pass
    # 启动串口扫码枪读取（USB-COM），扫到即入队
    try:
        s = store.get_settings()
        serial_reader.start_readers(
            s.get("serial_ports"), s.get("serial_baud") or 9600,
            lambda code, port: worker.enqueue(code, source=f"serial:{port}"),
        )
    except Exception as e:  # noqa
        print("[serial] 启动失败：", e)
    app.run(host="127.0.0.1", port=port, threaded=True)

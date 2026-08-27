# -*- coding: utf-8 -*-
"""
DMS 网页版客户端（用于自动打印面单）。

与签退用的 PDA 端（dms-public-api）不同，换单打印接口在网页版网关：
  POST https://dms.gofoexpress.com/prod-api/ops/scan/labelReplace/getLabelInfo
  body {"scanNumber": "<条码>"}
  需要 Authorization: Bearer <网页版 JWT>

网页版与 PDA 端账号密码相同，用户登录一次即可拿到两个 token。本类支持两种拿 token 方式：
  1) set_token(token)：手动/临时贴一个 Bearer token（测试期用）。
  2) login(username, password)：用账号密码自动登录网页版拿 token。
     —— 登录接口(WEB_LOGIN_PATH)待抓包确认后填入；确认前 login() 会抛 NotImplementedError，
        不影响用 set_token 的打印流程。
"""

import threading
import requests


WEB_BASE_URL = "https://dms.gofoexpress.com/prod-api"
GET_LABEL_PATH = "/ops/scan/labelReplace/getLabelInfo"

# DMS 网页版（RuoYi 框架）：登录带图形验证码。
CAPTCHA_PATH = "/captchaImage"     # GET → {code,uuid,img(base64 jpeg)}
WEB_LOGIN_PATH = "/login"          # POST {username,password,code,uuid} → {code,token}
GETINFO_PATH = "/getInfo"          # GET（带 token）→ 保活 / 校验 token 是否有效

SUCCESS_CODE = 200
DEFAULT_TIMEZONE = "America/Los_Angeles"


class WebAuthError(RuntimeError):
    """网页版鉴权失败（token 过期或账号密码错误），需要重新登录/更新 token。"""


class DmsWebClient:
    def __init__(self, username=None, password=None, token=None,
                 timezone=DEFAULT_TIMEZONE, timeout=15):
        self.username = username
        self.password = password
        self.timezone = timezone or DEFAULT_TIMEZONE
        self.timeout = timeout
        self._session = requests.Session()
        self._token = (token or "").strip() or None
        self._lock = threading.Lock()

    # ---------- token ----------
    def set_token(self, token):
        """手动设置一个 Bearer token（可带或不带 'Bearer ' 前缀）。"""
        token = (token or "").strip()
        with self._lock:
            self._token = token or None

    @property
    def has_token(self):
        return bool(self._token)

    def _auth_value(self):
        t = self._token or ""
        if not t:
            return ""
        return t if t.lower().startswith("bearer ") else "Bearer " + t

    def _headers(self):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://dms.gofoexpress.com",
            "devicePlatform": "PDA",
            "lang": "en",
            "auth-tag-x": "unauthorized",
            "timeZone": self.timezone,
            "User-Time-Zone": self.timezone,
        }
        av = self._auth_value()
        if av:
            h["Authorization"] = av
        return h

    # ---------- 登录（图形验证码） ----------
    def get_captcha(self):
        """取一张登录验证码。返回 {"uuid","img"}，img 为可直接放 <img src> 的 data URL。"""
        r = self._session.get(WEB_BASE_URL + CAPTCHA_PATH, timeout=self.timeout)
        r.raise_for_status()
        d = r.json()
        img = d.get("img") or ""
        if img and not img.startswith("data:"):
            img = "data:image/jpeg;base64," + img
        return {"uuid": d.get("uuid") or "", "img": img}

    def login_with_captcha(self, code, uuid):
        """用保存的账号密码 + 验证码登录，成功则缓存 token。返回 (ok, msg)。"""
        if not self.username or not self.password:
            return False, "未配置 DMS 账号密码"
        if not code or not uuid:
            return False, "请填写验证码"
        body = {
            "username": self.username, "password": self.password,
            "code": code, "uuid": uuid,
        }
        r = self._session.post(WEB_BASE_URL + WEB_LOGIN_PATH, json=body, timeout=self.timeout)
        r.raise_for_status()
        try:
            d = r.json()
        except ValueError:
            return False, "登录响应不是合法 JSON"
        if d.get("code") != SUCCESS_CODE:
            return False, d.get("msg") or "登录失败"
        token = d.get("token")
        if not token:
            return False, "登录成功但没拿到 token"
        self.set_token(token)
        return True, "登录成功，打印 token 已获取"

    def keepalive(self):
        """用 getInfo 校验/续期 token。返回 True=有效，False=已失效需重新登录。"""
        if not self.has_token:
            return False
        try:
            r = self._session.get(
                WEB_BASE_URL + GETINFO_PATH, headers=self._headers(), timeout=self.timeout)
        except Exception:
            return True  # 网络抖动不当作失效
        if r.status_code in (401, 403):
            return False
        try:
            return r.json().get("code") == SUCCESS_CODE
        except Exception:
            return r.status_code == 200

    def login(self):
        # 网页版登录需验证码，无法无人值守自动登录；改用 login_with_captcha()。
        raise WebAuthError("网页版登录需图形验证码，请在后台「打印」页在线登录获取 token。")

    def ensure_token(self):
        if not self.has_token:
            self.login()

    # ---------- 查单（取面单数据） ----------
    def get_label_info(self, scan_number):
        """
        返回 (ok, data_or_msg)：
          ok=True  → data 是 data[0]（整张面单字段），交给前端 label.html 渲染
          ok=False → data 是错误提示字符串
        token 过期(401)时，若配了登录接口会自动重登一次；否则抛 WebAuthError 让上层提示更新 token。
        """
        return self._get_label_once(scan_number, allow_relogin=True)

    def _get_label_once(self, scan_number, allow_relogin):
        self.ensure_token()
        resp = self._session.post(
            WEB_BASE_URL + GET_LABEL_PATH,
            json={"scanNumber": scan_number},
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code in (401, 403):
            raise WebAuthError("网页版 token 已失效，请在后台「打印」页在线登录重新获取。")
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            return False, "查单响应不是合法 JSON。"
        code = data.get("code")
        if code == 401:
            raise WebAuthError("网页版 token 已失效，请在后台「打印」页在线登录重新获取。")
        if code != SUCCESS_CODE:
            return False, data.get("msg") or "查单失败"
        rows = data.get("data") or []
        if not rows:
            return False, "未查到该单号的面单数据"
        return True, rows[0]

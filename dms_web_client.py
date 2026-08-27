# -*- coding: utf-8 -*-
"""
DMS 网页版客户端（用于自动打印面单）。

与签退用的 PDA 端（dms-public-api）不同，换单打印接口在网页版网关：
  POST https://dms.gofoexpress.com/prod-api/ops/scan/labelReplace/getLabelInfo
  body {"scanNumber": "<条码>"}
  需要 Authorization: Bearer <网页版 JWT>

网页版与 PDA 端账号密码相同。拿 token 三种方式（优先级从高到低）：
  1) login_auto()：ddddocr 本地识别验证码，账号密码全自动登录——无人值守（推荐）。
  2) login_with_captcha(code, uuid)：人工看图输验证码登录（ddddocr 不可用时的兜底）。
  3) set_token(token)：手动贴一个 Bearer token（应急）。

登录要点（见 DMS接入要点.md）：RuoYi 框架，密码需 AES-CBC 加密（固定 key/iv），
登录后必须调一次 getInfo 加载数据权限，否则接口"200 成功但空数据"。
"""

import base64
import threading
import requests

# 验证码本地识别（可选）。装不上（如 Win7 的 onnxruntime）则降级为人工输码。
try:
    import ddddocr
except Exception:
    ddddocr = None

# 密码 AES 加密（登录必需）。缺失则 login 抛错提示安装。
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
except Exception:
    AES = None
    pad = None


WEB_BASE_URL = "https://dms.gofoexpress.com/prod-api"
GET_LABEL_PATH = "/ops/scan/labelReplace/getLabelInfo"

# DMS 网页版（RuoYi 框架）：登录带图形验证码。
CAPTCHA_PATH = "/captchaImage"     # GET → {code,uuid,img(base64 jpeg)}
WEB_LOGIN_PATH = "/login"          # POST {username,password(AES),code,uuid} → {code,token}
GETINFO_PATH = "/getInfo"          # GET（带 token）→ 加载数据权限 / 保活 / 校验

# 前端固定的 AES key/iv（见 DMS接入要点.md）
_AES_KEY = b"59SO+p2dXTeghIqm"

SUCCESS_CODE = 200
DEFAULT_TIMEZONE = "America/Los_Angeles"

# ddddocr 单例（懒加载，首次用时加载内置模型）
_ocr = None
_ocr_lock = threading.Lock()


def _get_ocr():
    global _ocr
    if ddddocr is None:
        return None
    if _ocr is None:
        with _ocr_lock:
            if _ocr is None:
                _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def _enc_pwd(plain):
    """AES-CBC 加密密码 → base64（DMS 网页登录约定）。"""
    if AES is None:
        raise WebAuthError("缺少 pycryptodome，无法加密登录密码。请 pip install pycryptodome")
    c = AES.new(_AES_KEY, AES.MODE_CBC, _AES_KEY)
    return base64.b64encode(c.encrypt(pad((plain or "").encode(), 16))).decode()


def captcha_solver_available():
    return ddddocr is not None


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
            "lang": "zh",
            "auth-tag-x": "unauthorized",
            "Channel-Id": "us",
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

    def _do_login(self, code, uuid):
        """一次登录尝试。返回 (ok, msg, is_captcha_err)。"""
        body = {
            "username": self.username, "password": _enc_pwd(self.password),
            "code": code, "uuid": uuid,
        }
        r = self._session.post(WEB_BASE_URL + WEB_LOGIN_PATH, json=body, timeout=self.timeout)
        r.raise_for_status()
        try:
            d = r.json()
        except ValueError:
            return False, "登录响应不是合法 JSON", False
        if d.get("code") != SUCCESS_CODE:
            msg = d.get("msg") or "登录失败"
            is_cap = "captcha" in msg.lower() or "验证码" in msg
            return False, msg, is_cap
        token = d.get("token")
        if not token:
            return False, "登录成功但没拿到 token", False
        self.set_token(token)
        # ★ 登录后必须调 getInfo，否则很多接口"200 成功但空数据"
        try:
            self._session.get(WEB_BASE_URL + GETINFO_PATH, headers=self._headers(),
                              timeout=self.timeout)
        except Exception:
            pass
        return True, "登录成功，打印 token 已获取", False

    def login_with_captcha(self, code, uuid):
        """人工输验证码登录。返回 (ok, msg)。"""
        if not self.username or not self.password:
            return False, "未配置 DMS 账号密码"
        if not code or not uuid:
            return False, "请填写验证码"
        try:
            ok, msg, _ = self._do_login(code, uuid)
        except WebAuthError as e:
            return False, str(e)
        return ok, msg

    def login_auto(self, max_tries=6):
        """ddddocr 自动识别验证码登录（无人值守）。返回 (ok, msg)。"""
        if not self.username or not self.password:
            return False, "未配置 DMS 账号密码"
        ocr = _get_ocr()
        if ocr is None:
            return False, "未安装验证码识别库 ddddocr，请改用人工输码登录"
        last = "登录失败"
        for _ in range(max_tries):
            try:
                cap = self._session.get(WEB_BASE_URL + CAPTCHA_PATH, timeout=self.timeout).json()
                img = cap.get("img") or ""
                if img.startswith("data:"):
                    img = img.split(",", 1)[1]
                code = ocr.classification(base64.b64decode(img))
                ok, msg, is_cap = self._do_login(code, cap.get("uuid"))
                last = msg
                if ok:
                    return True, msg
                if not is_cap:
                    # 账号/密码类错误，重试无意义
                    return False, msg
            except WebAuthError as e:
                return False, str(e)
            except Exception as e:  # noqa
                last = f"登录异常：{e}"
        return False, f"自动识别多次失败：{last}"

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
        """自动登录（ddddocr）。装了识别库就无人值守；否则抛错让上层提示人工输码。"""
        if _get_ocr() is None:
            raise WebAuthError("网页版登录需验证码，请在后台「打印」页在线输码登录，或安装 ddddocr 实现自动。")
        ok, msg = self.login_auto()
        if not ok:
            raise WebAuthError(msg)

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
        expired = resp.status_code in (401, 403)
        data = None
        if not expired:
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                return False, "查单响应不是合法 JSON。"
            if data.get("code") == 401:
                expired = True
        # token 失效：装了 ddddocr 就自动重登一次再重试
        if expired:
            if allow_relogin and _get_ocr() is not None:
                ok, _msg = self.login_auto()
                if ok:
                    return self._get_label_once(scan_number, allow_relogin=False)
            raise WebAuthError("网页版 token 已失效，请在后台「打印」页登录重新获取（或装 ddddocr 自动登录）。")
        if data.get("code") != SUCCESS_CODE:
            return False, data.get("msg") or "查单失败"
        rows = data.get("data") or []
        if not rows:
            return False, "未查到该单号的面单数据"
        return True, rows[0]

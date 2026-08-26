# -*- coding: utf-8 -*-
"""
GFS Station PDA 接口客户端。

从 gfs_pda_signin_test.py 里验证过的接口重构而来，封装成一个可复用的类：
- login()   登录拿 token
- sign_in() 扫码签到
- 自动处理 token 过期：签到时如果返回鉴权/系统异常，会用保存的账号密码
  重新登录一次再重试，让日常使用不需要人工干预。

线程安全：内部用一把锁保护 token，Flask 多线程下也能安全共享一个实例。
"""

import threading
import requests


BASE_URL = "https://dms-public-api.gofoexpress.com"

LOGIN_PATH = "/app/auth/sitePda/login"
SIGN_IN_PATH = "/apple/deliver/sign/scanSignIn"
SIGN_OUT_PATH = "/apple/deliver/signOut/scanSignOut"
# 签退放行 / 拦截：反编译自 SiteApiService.signOutPass / signOutIntercept
SIGN_OUT_PASS_PATH = "/apple/deliver/signOut/pass"
SIGN_OUT_INTERCEPT_PATH = "/apple/deliver/signOut/intercept"
# 签到放行：反编译自 StaSignActivity 的「放行」按钮，POST body {signRecordId}
SIGN_PASS_PATH = "/apple/deliver/sign/pass"
# 用户信息（含可切换的站点组 groups / 当前 selectedGroup），需 type:4 头
USERINFO_PATH = "/app/auth/userInfo/pda/V2"
# 切换站点权限：PUT /app/auth/changeDept/{groupId}（反编译 changeGroup(int)）
CHANGE_DEPT_PATH = "/app/auth/changeDept/{}"

# 接口成功返回的业务状态码（不是 HTTP 状态码），实测为 200
SUCCESS_CODE = 200

DEFAULT_TIMEZONE = "America/Los_Angeles"


class LoginError(RuntimeError):
    """账号密码错误 / 登录被拒绝，需要人工去设置页处理。"""


class GfsClient:
    """一个站点账号对应一个 GfsClient 实例。"""

    def __init__(self, username, password, timezone=DEFAULT_TIMEZONE, timeout=15,
                 site_perm=None):
        self.username = username
        self.password = password
        self.timezone = timezone or DEFAULT_TIMEZONE
        self.timeout = timeout
        # 站点权限（groupName，如 "SFO01"/"SMF01"）。为空则不切换，沿用账号当前站点。
        self.site_perm = (site_perm or "").strip() or None

        self._session = requests.Session()
        self._token = None
        self._current_site = None
        self._lock = threading.Lock()

    # ---------- 请求头 ----------
    def _headers(self, with_token=False):
        headers = {
            "devicePlatform": "PDA",
            "lang": "en",
            "type": "4",  # 反编译 HeaderInterceptor：真机每次都带，userInfo/changeDept 必需
            "Content_Type": "application/json",
            "charset": "UTF-8",
            "timeZone": self.timezone,
            "auth-tag-x": "unauthorized",
            "applicationRegion": "US-US",
            "deviceModel": "SelfServiceKiosk-RPi",
            "deviceSystemVersion": "1.0.0",
        }
        if with_token and self._token:
            headers["Authorization"] = self._token
        return headers

    # ---------- 登录 ----------
    def login(self):
        """登录并缓存 token。失败抛 LoginError。"""
        url = BASE_URL + LOGIN_PATH
        payload = {
            "username": self.username,
            "mobile": None,
            "password": self.password,
            "code": None,
        }
        resp = self._session.post(
            url, json=payload, headers=self._headers(), timeout=self.timeout
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            raise LoginError("登录响应不是合法 JSON，接口地址或网络可能有问题。")

        if data.get("code") != SUCCESS_CODE:
            raise LoginError(f"登录失败：{data.get('msg') or '账号或密码错误'}")

        token = (data.get("data") or {}).get("token")
        if not token:
            raise LoginError("登录成功但没拿到 token，接口结构可能变了。")

        with self._lock:
            self._token = token

        # 登录后按配置切换站点权限（SFO01 / SMF01）
        if self.site_perm:
            self._apply_site_perm()
        return token

    # ---------- 站点权限（切换组织/组）----------
    def get_user_info(self):
        """GET userInfo，返回 data（含 selectedGroup 与可切换的 groups）。"""
        resp = self._session.get(
            BASE_URL + USERINFO_PATH,
            headers=self._headers(with_token=True),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != SUCCESS_CODE:
            raise RuntimeError(data.get("msg") or "获取用户信息失败")
        return data.get("data") or {}

    def list_sites(self):
        """返回该账号可用的站点列表 [{groupId, groupName, selected}]，当前选中在内。"""
        info = self.get_user_info()
        selected = info.get("selectedGroup") or {}
        pool = list(info.get("groups") or [])
        if selected:
            pool = [selected] + pool
        seen, out = set(), []
        for g in pool:
            gid = g.get("groupId")
            if gid in seen:
                continue
            seen.add(gid)
            out.append(
                {
                    "groupId": gid,
                    "groupName": g.get("groupName"),
                    "selected": gid == selected.get("groupId"),
                }
            )
        return out

    def _apply_site_perm(self):
        """把当前会话切换到 self.site_perm 指定的站点组（按 groupName 匹配）。"""
        info = self.get_user_info()
        selected = info.get("selectedGroup") or {}
        if selected.get("groupName") == self.site_perm:
            self._current_site = self.site_perm
            return  # 已经在目标站点

        pool = list(info.get("groups") or [])
        if selected:
            pool.append(selected)
        match = next(
            (g for g in pool if g.get("groupName") == self.site_perm), None
        )
        if not match:
            raise LoginError(f"该账号没有 {self.site_perm} 的站点权限")

        gid = match.get("groupId")
        resp = self._session.put(
            BASE_URL + CHANGE_DEPT_PATH.format(gid),
            headers=self._headers(with_token=True),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != SUCCESS_CODE:
            raise LoginError(f"切换到 {self.site_perm} 失败：{data.get('msg')}")
        self._current_site = self.site_perm

    @property
    def current_site(self):
        return self._current_site

    @property
    def logged_in(self):
        return self._token is not None

    def ensure_login(self):
        """没有 token 就登录一次。"""
        if not self.logged_in:
            self.login()

    # ---------- 签到 ----------
    def sign_in(self, qr_uuid):
        """
        扫码签到。返回一个统一结构的 dict：
            {
              "ok": bool,          # 业务是否成功（signResult == 1）
              "result": int|None,  # 原始 signResult：1成功 2失败 3其他
              "message": str,      # 给屏幕看的中文提示
              "driverName": str,
              "licensePlateNo": str,
              "signTime": str,
              "raw": dict,         # 原始响应，排查用
            }
        token 过期会自动重登一次再重试。
        """
        data = self._sign_in_once(qr_uuid, allow_relogin=True)
        return self._interpret(data)

    def _sign_in_once(self, qr_uuid, allow_relogin):
        self.ensure_login()
        url = BASE_URL + SIGN_IN_PATH
        resp = self._session.get(
            url,
            params={"uuid": qr_uuid},
            headers=self._headers(with_token=True),
            timeout=self.timeout,
        )

        # HTTP 401/403：token 失效，重登重试
        if resp.status_code in (401, 403) and allow_relogin:
            self.login()
            return self._sign_in_once(qr_uuid, allow_relogin=False)

        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError("签到响应不是合法 JSON，请检查网络或接口。")

        # 业务 code 非 200：可能是 token 过期/系统异常，重登重试一次
        if data.get("code") != SUCCESS_CODE and allow_relogin:
            self.login()
            return self._sign_in_once(qr_uuid, allow_relogin=False)

        return data

    @staticmethod
    def _interpret(data):
        if data.get("code") != SUCCESS_CODE:
            return {
                "ok": False,
                "result": None,
                "category": "fail",
                "needs_release": False,
                "message": data.get("msg") or "接口返回异常，请重试或联系管理员",
                "driverName": "",
                "licensePlateNo": "",
                "signTime": "",
                "signRecordId": None,
                "driverUserId": None,
                "returnFlag": None,
                "returnCount": None,
                "raw": data,
            }

        payload = data.get("data") or {}
        result = payload.get("signResult")
        sign_record_id = payload.get("signRecordId")
        return_flag = payload.get("returnFlag")
        return_count = payload.get("returnCount")

        # 判定类别（依据反编译，见 API_NOTES.md；returnFlag 取值待真实样本确认）
        # - signResult==1：成功
        # - signResult==3 且有 signRecordId：需放行（无任务 / 退件）
        # - 其它（含 signResult==2 硬失败）：失败，不自动放行
        if result == 1:
            category = "success"
            needs_release = False
        elif result == 3 and sign_record_id is not None:
            needs_release = True
            # 退件信号：returnFlag/returnCount 有值，或原因文本提到退件/未退回
            # （实测退件时 returnFlag/returnCount 为空，靠 signFailedReason，
            #   例如 "Some packages have not been returned"）
            reason = (payload.get("signFailedReason") or "").lower()
            is_return = (
                bool(return_flag)
                or bool(return_count)
                or "return" in reason
                or "退件" in reason
            )
            category = "return" if is_return else "no_task"
        else:
            category = "fail"
            needs_release = False

        ok = result == 1
        base_msg = {
            "success": "签到成功",
            "no_task": "无任务，需放行",
            "return": "退件，需放行",
            "fail": payload.get("signFailedReason") or "签到失败",
        }
        return {
            "ok": ok,
            "result": result,
            "category": category,
            "needs_release": needs_release,
            "message": base_msg.get(category, "未知状态，请人工确认"),
            "driverName": payload.get("driverName") or "",
            "licensePlateNo": payload.get("licensePlateNo") or "",
            "signTime": payload.get("signTime") or "",
            "signRecordId": sign_record_id,
            "driverUserId": payload.get("driverUserId"),
            "returnFlag": return_flag,
            "returnCount": return_count,
            "raw": data,
        }

    # ================================================================
    #                         司机签退
    # ================================================================
    def sign_out(self, qr_uuid):
        """
        扫码签退。GET /apple/deliver/signOut/scanSignOut?uuid=
        返回统一 dict（含应取/实取/错扫三数，供 App 层套强弱提醒阈值）：
            {
              "ok": bool,            # signResult == 1
              "result": int|None,
              "beReceiveCount": int, # 应取件量
              "receivedCount": int,  # 实取件量
              "wrongScanCount": int, # 错扫件量
              "wrongScanReturnCount": int,
              "returnCount": int,
              "diff": int,           # 应取 - 实取
              "needs_release": bool, # 有 signRecordId 且业务未通过 → 需人工放行/拦截
              "message": str,
              "driverName","licensePlateNo","deliveryLineName","signTime": str,
              "signRecordId","driverUserId": int|None,
              "raw": dict,
            }
        token 过期自动重登一次再重试。
        """
        data = self._scan_once(SIGN_OUT_PATH, qr_uuid, allow_relogin=True)
        return self._interpret_signout(data)

    def _scan_once(self, path, qr_uuid, allow_relogin):
        self.ensure_login()
        resp = self._session.get(
            BASE_URL + path,
            params={"uuid": qr_uuid},
            headers=self._headers(with_token=True),
            timeout=self.timeout,
        )
        if resp.status_code in (401, 403) and allow_relogin:
            self.login()
            return self._scan_once(path, qr_uuid, allow_relogin=False)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError("签退响应不是合法 JSON，请检查网络或接口。")
        if data.get("code") != SUCCESS_CODE and allow_relogin:
            self.login()
            return self._scan_once(path, qr_uuid, allow_relogin=False)
        return data

    @staticmethod
    def _interpret_signout(data):
        if data.get("code") != SUCCESS_CODE:
            return {
                "ok": False, "result": None,
                "beReceiveCount": 0, "receivedCount": 0, "wrongScanCount": 0,
                "wrongScanReturnCount": 0, "returnCount": 0, "diff": 0,
                "needs_release": False,
                "message": data.get("msg") or "接口返回异常，请重试或联系管理员",
                "driverName": "", "licensePlateNo": "", "deliveryLineName": "",
                "signTime": "", "signRecordId": None, "driverUserId": None,
                "raw": data,
            }
        p = data.get("data") or {}
        be = int(p.get("beReceiveCount") or 0)
        rec = int(p.get("receivedCount") or 0)
        wrong = int(p.get("wrongScanWaybillCount") or 0)
        wrong_ret = int(p.get("wrongScanReturnWaybillCount") or 0)
        ret = int(p.get("returnCount") or 0)
        result = p.get("signResult")
        srid = p.get("signRecordId")
        ok = result == 1
        # 需人工处理：业务未通过且拿到了 signRecordId（可放行/拦截）
        needs_release = (not ok) and (srid is not None)
        return {
            "ok": ok,
            "result": result,
            "beReceiveCount": be,
            "receivedCount": rec,
            "wrongScanCount": wrong,
            "wrongScanReturnCount": wrong_ret,
            "returnCount": ret,
            "diff": be - rec,
            "needs_release": needs_release,
            "message": p.get("signFailedReason") or ("签退成功" if ok else "需人工确认"),
            "driverName": p.get("driverName") or "",
            "licensePlateNo": p.get("licensePlateNo") or "",
            "deliveryLineName": p.get("deliveryLineName") or "",
            "signTime": p.get("signTime") or "",
            "signRecordId": srid,
            "driverUserId": p.get("driverUserId"),
            "raw": data,
        }

    def sign_out_pass(self, sign_record_id, driver_user_id):
        """签退放行。POST /apple/deliver/signOut/pass body {signRecordId, driverUserId}"""
        return self._signout_action(
            SIGN_OUT_PASS_PATH,
            {"signRecordId": sign_record_id, "driverUserId": driver_user_id},
            allow_relogin=True, ok_msg="放行成功",
        )

    def sign_out_intercept(self, sign_record_id, driver_user_id, remark=""):
        """签退拦截/拒绝。POST /apple/deliver/signOut/intercept
        body {signRecordId, driverUserId, interceptRemark}"""
        return self._signout_action(
            SIGN_OUT_INTERCEPT_PATH,
            {
                "signRecordId": sign_record_id,
                "driverUserId": driver_user_id,
                "interceptRemark": remark or "",
            },
            allow_relogin=True, ok_msg="拦截成功",
        )

    def _signout_action(self, path, payload, allow_relogin, ok_msg):
        self.ensure_login()
        resp = self._session.post(
            BASE_URL + path,
            json=payload,
            headers=self._headers(with_token=True),
            timeout=self.timeout,
        )
        if resp.status_code in (401, 403) and allow_relogin:
            self.login()
            return self._signout_action(path, payload, allow_relogin=False, ok_msg=ok_msg)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError("响应不是合法 JSON。")
        if data.get("code") != SUCCESS_CODE:
            if allow_relogin:
                self.login()
                return self._signout_action(path, payload, allow_relogin=False, ok_msg=ok_msg)
            return False, data.get("msg") or "操作失败"
        return True, ok_msg

    # ---------- 签到放行 ----------
    def release(self, sign_record_id):
        """
        调用「放行」接口 POST /apple/deliver/sign/pass  body {signRecordId}。
        token 过期自动重登一次再重试。返回 (ok, message)。
        """
        ok, msg = self._release_once(sign_record_id, allow_relogin=True)
        return ok, msg

    def _release_once(self, sign_record_id, allow_relogin):
        self.ensure_login()
        url = BASE_URL + SIGN_PASS_PATH
        resp = self._session.post(
            url,
            json={"signRecordId": sign_record_id},
            headers=self._headers(with_token=True),
            timeout=self.timeout,
        )
        if resp.status_code in (401, 403) and allow_relogin:
            self.login()
            return self._release_once(sign_record_id, allow_relogin=False)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError("放行响应不是合法 JSON。")
        if data.get("code") != SUCCESS_CODE:
            if allow_relogin:
                self.login()
                return self._release_once(sign_record_id, allow_relogin=False)
            return False, data.get("msg") or "放行失败"
        return True, "放行成功"

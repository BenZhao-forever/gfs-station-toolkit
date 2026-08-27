# -*- coding: utf-8 -*-
"""
本地存储（每台机器独立跑，单管理员）。

数据落在 data/config.json（与程序同目录）。内容：
- admin：本地后台的一个管理员密码（werkzeug 哈希）。首次用 DEFAULT_ADMIN_PASSWORD。
- dms：站点 DMS 账号密码（PDA 与网页版通用；密码用 Fernet 加密）+ 时区 + 站点权限。
- print_token：网页版打印 token（测试期手动贴；接入网页登录后自动刷新）。
- settings：强/弱提醒阈值、声音开关、打印机名、串口(COM)配置、更新开关等。
- logs：签退/打印的异常与流水（滚动保留最近 N 条）。

加密密钥来自环境变量 STATION_ENC_KEY；缺省则用机器本地生成的随机密钥（存 data/.enckey）。
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import time

from werkzeug.security import generate_password_hash, check_password_hash

try:
    from cryptography.fernet import Fernet
except Exception:
    Fernet = None

_LOCK = threading.Lock()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("STATION_DATA_DIR") or os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
ENCKEY_PATH = os.path.join(DATA_DIR, ".enckey")

DEFAULT_ADMIN_PASSWORD = os.environ.get("DEFAULT_ADMIN_PASSWORD", "admin123")
MAX_LOGS = 500

DEFAULT_SETTINGS = {
    # 强/弱提醒：diff = 应取(beReceiveCount) - 实取(receivedCount)
    "strong_diff": 5,          # 差 > strong_diff → 强提醒（红 + 声音 + 需放行）
    "weak_diff": 1,            # 差 >= weak_diff（且 <= strong_diff）→ 弱提醒（黄）
    "strong_on_wrongscan": True,   # 错扫 > 0 也算强提醒
    "sound_on_strong": True,       # 强提醒播放 “取件量低”
    # 自动放行：应领-实领 差值 < auto_pass_diff 且无错扫、实领>0 时，自动放行（免人工）
    "auto_pass_diff": 3,
    # 打印
    "print_engine": "chrome",  # chrome=浏览器静默打印(免费,推荐) / clodop=CLodop(未注册会有水印页)
    "printer_name": "",        # 仅 CLodop 引擎用；chrome 引擎用系统默认打印机
    "label_width_mm": 100,
    "label_height_mm": 150,
    # 串口扫码枪（USB-COM）。留空则只接受键盘/网页输入。
    "serial_ports": [],        # ["COM3","COM4"]
    "serial_baud": 9600,
    # 自动更新
    "auto_update": True,
    "update_repo": "BenZhao-forever/gfs-station-toolkit",  # 公开仓库，可在后台改
}

DEFAULT_CONFIG = {
    "admin_password_hash": None,
    "secret_key": None,
    "dms": {                   # 站点 DMS 账号（PDA + 网页版通用）
        "username": "",
        "password_enc": None,
        "password": None,
        "timezone": "America/Los_Angeles",
        "site_perm": "",       # 如 SFO01 / SMF01；空则沿用账号当前站点
        "name": "",            # 展示名
    },
    "print_token": "",         # 网页版打印 token（Bearer）
    "settings": dict(DEFAULT_SETTINGS),
    "logs": [],
}


# ---------------------------------------------------------------- 加密
def _enc_secret():
    key = os.environ.get("STATION_ENC_KEY")
    if key:
        return key
    # 无环境变量：用本机持久随机密钥
    try:
        if os.path.exists(ENCKEY_PATH):
            with open(ENCKEY_PATH, "r", encoding="utf-8") as f:
                return f.read().strip()
        os.makedirs(DATA_DIR, exist_ok=True)
        k = secrets.token_urlsafe(32)
        with open(ENCKEY_PATH, "w", encoding="utf-8") as f:
            f.write(k)
        return k
    except Exception:
        return None


def _fernet():
    key = _enc_secret()
    if not key or not Fernet:
        return None
    try:
        derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
        return Fernet(derived)
    except Exception:
        return None


def encryption_active():
    return _fernet() is not None


class Store:
    def __init__(self):
        self._data = None
        self._load()

    # ---------------------------------------------------------- 文件
    def _load(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        data = dict(DEFAULT_CONFIG)
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                data = _merge(DEFAULT_CONFIG, loaded)
            except Exception:
                pass
        # 首次播种：管理员密码 + session 密钥
        changed = False
        if not data.get("admin_password_hash"):
            data["admin_password_hash"] = generate_password_hash(DEFAULT_ADMIN_PASSWORD)
            changed = True
        if not data.get("secret_key"):
            data["secret_key"] = secrets.token_hex(32)
            changed = True
        self._data = data
        if changed:
            self._save()

    def _save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)

    def secret_key(self):
        return self._data["secret_key"]

    # ---------------------------------------------------------- 管理员
    def check_admin_password(self, password):
        return check_password_hash(self._data["admin_password_hash"], password or "")

    def set_admin_password(self, password):
        password = (password or "").strip()
        if len(password) < 4:
            raise ValueError("密码至少 4 位")
        with _LOCK:
            self._data["admin_password_hash"] = generate_password_hash(password)
            self._save()

    # ---------------------------------------------------------- DMS 账号
    def get_dms(self, with_password=False):
        d = dict(self._data.get("dms") or {})
        out = {
            "username": d.get("username") or "",
            "timezone": d.get("timezone") or "America/Los_Angeles",
            "site_perm": d.get("site_perm") or "",
            "name": d.get("name") or "",
            "has_password": bool(d.get("password_enc") or d.get("password")),
        }
        if with_password:
            out["password"] = _decrypt_dms(d)
        return out

    def set_dms(self, username, password, timezone=None, site_perm=None, name=None):
        with _LOCK:
            d = dict(self._data.get("dms") or {})
            d["username"] = (username or "").strip()
            if password:  # 空密码表示不改
                enc = _encrypt_dms((password or ""))
                d.pop("password", None)
                d.pop("password_enc", None)
                d.update(enc)
            if timezone is not None:
                d["timezone"] = timezone or "America/Los_Angeles"
            if site_perm is not None:
                d["site_perm"] = site_perm or ""
            if name is not None:
                d["name"] = name or ""
            self._data["dms"] = d
            self._save()

    def dms_signature(self):
        d = self._data.get("dms") or {}
        return "|".join([
            d.get("username") or "", _decrypt_dms(d),
            d.get("timezone") or "", d.get("site_perm") or "",
        ])

    # ---------------------------------------------------------- 打印 token
    def get_print_token(self):
        return self._data.get("print_token") or ""

    def set_print_token(self, token):
        with _LOCK:
            self._data["print_token"] = (token or "").strip()
            self._save()

    # ---------------------------------------------------------- 设置
    def get_settings(self):
        s = dict(DEFAULT_SETTINGS)
        s.update(self._data.get("settings") or {})
        return s

    def set_settings(self, patch):
        with _LOCK:
            s = dict(DEFAULT_SETTINGS)
            s.update(self._data.get("settings") or {})
            for k, v in (patch or {}).items():
                if k in DEFAULT_SETTINGS:
                    s[k] = v
            self._data["settings"] = s
            self._save()
            return s

    # ---------------------------------------------------------- 日志
    def append_log(self, entry):
        with _LOCK:
            logs = self._data.get("logs") or []
            entry = dict(entry)
            entry.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
            logs.append(entry)
            if len(logs) > MAX_LOGS:
                logs = logs[-MAX_LOGS:]
            self._data["logs"] = logs
            self._save()

    def read_logs(self, limit=100, kind=None):
        logs = list(self._data.get("logs") or [])
        if kind:
            logs = [x for x in logs if x.get("kind") == kind]
        return list(reversed(logs[-limit:]))


# ---------------------------------------------------------------- 辅助
def _encrypt_dms(plain):
    f = _fernet()
    if f and plain:
        return {"password_enc": f.encrypt(plain.encode()).decode()}
    return {"password": plain}


def _decrypt_dms(d):
    if d.get("password_enc"):
        f = _fernet()
        if f:
            try:
                return f.decrypt(d["password_enc"].encode()).decode()
            except Exception:
                return ""
        return ""
    return d.get("password") or ""


def _merge(base, loaded):
    out = dict(base)
    for k, v in (loaded or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            out[k] = _merge(base[k], v)
        else:
            out[k] = v
    return out

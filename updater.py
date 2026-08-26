# -*- coding: utf-8 -*-
"""
自动更新：从公开 GitHub 仓库比对版本并覆盖更新。

机制：
- 本地版本读 version.json 的 "version"。
- 远端取仓库 main 分支上的 version.json（raw）比对；有更新则下载该仓库的 zip 压缩包，
  解压后把代码文件覆盖到程序目录（保留 data/ 与 .enckey，不动用户数据），然后请求重启。
- 重启由启动脚本（run.bat 的循环）完成：更新后进程以退出码 3 结束，脚本重新拉起。

公开仓库无需 token；未配置仓库(update_repo)时所有函数安全空转。
"""

import io
import json
import os
import shutil
import threading
import time
import zipfile

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_PATH = os.path.join(BASE_DIR, "version.json")
RESTART_EXIT_CODE = 3

# 覆盖更新时保留（不覆盖、不删除）的路径
KEEP = {"data", ".enckey", ".git", "__pycache__"}


def _read_local_version():
    try:
        with open(VERSION_PATH, "r", encoding="utf-8") as f:
            return (json.load(f).get("version") or "0.0.0").strip()
    except Exception:
        return "0.0.0"


LOCAL_VERSION = _read_local_version()


def _vtuple(v):
    out = []
    for part in str(v).split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def _raw_url(repo, path):
    return f"https://raw.githubusercontent.com/{repo}/main/{path}"


def check_update(repo, timeout=10):
    """返回 {current, latest, has_update, notes, error?}。"""
    current = _read_local_version()
    if not repo:
        return {"current": current, "latest": current, "has_update": False,
                "notes": "", "error": "未配置更新仓库"}
    try:
        r = requests.get(_raw_url(repo, "version.json"),
                         timeout=timeout, headers={"Cache-Control": "no-cache"})
        r.raise_for_status()
        meta = r.json()
        latest = (meta.get("version") or "0.0.0").strip()
        notes = meta.get("notes") or ""
        has = _vtuple(latest) > _vtuple(current)
        return {"current": current, "latest": latest, "has_update": has, "notes": notes}
    except Exception as e:  # noqa
        return {"current": current, "latest": current, "has_update": False,
                "notes": "", "error": f"检查失败：{e}"}


def _download_zip(repo, timeout=60):
    """下载仓库 main 分支 zip，返回 ZipFile。"""
    url = f"https://codeload.github.com/{repo}/zip/refs/heads/main"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(r.content))


def apply_update(repo, timeout=60, restart=True):
    """下载并覆盖更新。成功后按需触发重启（退出码 3）。"""
    info = check_update(repo, timeout=10)
    if info.get("error"):
        return {"ok": False, "message": info["error"]}
    if not info.get("has_update"):
        return {"ok": True, "updated": False, "message": "已是最新版本",
                "current": info["current"]}
    try:
        zf = _download_zip(repo, timeout=timeout)
    except Exception as e:  # noqa
        return {"ok": False, "message": f"下载失败：{e}"}

    # zip 顶层是 "<repo>-main/"，取其内部相对路径
    names = zf.namelist()
    root = names[0].split("/")[0] + "/" if names else ""
    tmp = os.path.join(BASE_DIR, "_update_tmp")
    if os.path.exists(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    zf.extractall(tmp)
    src_root = os.path.join(tmp, root.rstrip("/"))

    try:
        _copy_over(src_root, BASE_DIR)
    except Exception as e:  # noqa
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "message": f"覆盖失败：{e}"}
    shutil.rmtree(tmp, ignore_errors=True)

    new_version = _read_local_version()
    result = {"ok": True, "updated": True, "message": f"已更新到 {new_version}",
              "current": new_version, "restart": restart}
    if restart:
        threading.Thread(target=_delayed_exit, daemon=True).start()
    return result


def _copy_over(src_root, dst_root):
    for name in os.listdir(src_root):
        if name in KEEP:
            continue
        s = os.path.join(src_root, name)
        d = os.path.join(dst_root, name)
        if os.path.isdir(s):
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)


def _delayed_exit():
    time.sleep(1.5)
    os._exit(RESTART_EXIT_CODE)


def maybe_background_update(store):
    """启动时后台检查+应用一次（仅当开启 auto_update 且配了仓库）。"""
    s = store.get_settings()
    if not s.get("auto_update") or not s.get("update_repo"):
        return

    def _job():
        time.sleep(3)
        info = check_update(s["update_repo"])
        if info.get("has_update"):
            apply_update(s["update_repo"])

    threading.Thread(target=_job, daemon=True).start()

# -*- coding: utf-8 -*-
"""
USB-COM 扫码枪读取。两把（或多把）扫码枪各接一个虚拟串口(COM)，
每把枪一个独立线程读取，一行一码（以回车/换行结尾），直接入队。
串口互相独立，不会像 HID 键盘那样字符交错。

未装 pyserial 或未配置串口时安全空转（可用键盘/网页 /api/scan 输入）。
"""

import threading
import time

try:
    import serial  # pyserial
except Exception:
    serial = None


def list_ports():
    """列出本机可用串口，供后台下拉选择。"""
    try:
        from serial.tools import list_ports as _lp
        return [{"device": p.device, "desc": p.description} for p in _lp.comports()]
    except Exception:
        return []


def _reader_loop(port, baud, on_code, stop_evt):
    while not stop_evt.is_set():
        try:
            with serial.Serial(port, baud, timeout=1) as ser:
                buf = bytearray()
                while not stop_evt.is_set():
                    chunk = ser.read(64)
                    if not chunk:
                        continue
                    for byte in chunk:
                        if byte in (10, 13):  # \n \r
                            if buf:
                                code = buf.decode("utf-8", "ignore").strip()
                                buf = bytearray()
                                if code:
                                    try:
                                        on_code(code, port)
                                    except Exception:
                                        pass
                        else:
                            buf.append(byte)
        except Exception:
            # 串口不存在/被占用/拔出：等一下重试
            time.sleep(2)


def start_readers(ports, baud, on_code):
    """为每个串口起一个读取线程。返回一个 stop() 函数。"""
    if serial is None:
        print("[serial] 未安装 pyserial，跳过串口扫码枪（可用键盘/网页输入）")
        return lambda: None
    ports = [p for p in (ports or []) if p]
    if not ports:
        print("[serial] 未配置串口，跳过串口扫码枪")
        return lambda: None
    stop_evt = threading.Event()
    threads = []
    for port in ports:
        t = threading.Thread(target=_reader_loop, args=(port, baud, on_code, stop_evt), daemon=True)
        t.start()
        threads.append(t)
        print(f"[serial] 已监听扫码枪串口 {port} @ {baud}")

    def stop():
        stop_evt.set()
    return stop

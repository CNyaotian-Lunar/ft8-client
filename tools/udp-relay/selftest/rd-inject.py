#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rd-inject.py —— 独立审查实测：中继会不会把【任意第三方报文】投递进 wsjtx 的入站端口

与 rd-hijack.py 同一个中继，换一个问法：
  A 假装本机 wsjtx（发 id="WSJT-X" 的心跳，让 relay 学到它）
  B 假装局域网里随便一台机器，发一条 type=Close(6) —— MessageClient 收到会 Q_EMIT close()
    而 mainwindow.cpp:725 是 connect(close, &MainWindow::close) ⇒ 直接关掉 WSJT-X！
  判据：A 能收到这条 Close ⇒ 中继就是一条【无鉴权的控制面投递通道】
（本脚本只在本机回环跑，不碰电台、不发任何真实无线电报文）
"""
import os
import socket
import struct
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
RELAY = r"<WORKSPACE>\projects\ft8-client\tools\udp-relay\smart_relay.py"
PY = r"D:\Tools\Python312\python.exe"
HOST = "127.0.0.1"
PORT = 22393
MAGIC = bytes([0xAD, 0xBC, 0xCB, 0xDA])
NAMES = {0: "Heartbeat", 2: "Decode", 6: "Close", 8: "HaltTx", 9: "FreeText", 15: "Configure"}


def build(typ, ident, payload=b""):
    return (MAGIC + struct.pack("!I", 3) + struct.pack("!I", typ)
            + struct.pack("!I", len(ident)) + ident.encode() + payload)


def recv_one(sock, seconds=1.5):
    sock.settimeout(0.25)
    end = time.time() + seconds
    while time.time() < end:
        try:
            return sock.recvfrom(65535)
        except socket.timeout:
            continue
    return None


def main():
    log = open(os.path.join(HERE, "rd-inject-relay.log"), "wb")
    proc = subprocess.Popen([PY, RELAY, "--listen", "%s:%d" % (HOST, PORT), "--verbose",
                             "--stats-every", "3600"],
                            stdout=log, stderr=subprocess.STDOUT,
                            cwd=os.path.dirname(RELAY))
    time.sleep(2.0)
    if proc.poll() is not None:
        print("relay 起不来"); return 3
    try:
        A = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); A.bind((HOST, 0))
        B = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); B.bind((HOST, 0))
        print("A(本机wsjtx) 端口=%d   B(任意局域网主机) 端口=%d" % (A.getsockname()[1], B.getsockname()[1]))

        A.sendto(build(0, "WSJT-X", b"\x00\x00\x00\x03" + b"v1"), (HOST, PORT))
        time.sleep(0.6)
        print("[1] A 发心跳，relay 已学到 A（无回应属正常）")

        for typ in (6, 8, 15):          # Close / HaltTx / Configure
            B.sendto(build(typ, "attacker-any-id", b"\x00" * 8), (HOST, PORT))
            got = recv_one(A)
            if got:
                data, peer = got
                t = struct.unpack("!I", data[8:12])[0]
                idlen = struct.unpack("!I", data[12:16])[0]
                ident = data[16:16 + idlen].decode("utf-8", "replace")
                print("   ★ A 收到了 type=%d(%s)  来自 %s:%d  id=%r  %d 字节"
                      % (t, NAMES.get(t, "?"), peer[0], peer[1], ident, len(data)))
                print("     ⇒ 若 A 是 wsjtx，这一条会触发 MessageClient 对应信号："
                      + {6: " close() ⇒ MainWindow::close()（程序关闭）",
                         8: " halt_tx()",
                         15: " configure()"}.get(t, " ?"))
            else:
                print("   A 没收到 type=%d" % typ)
        print()
        # ⭐ 结论必须按【实际结果】生成，不能写死 ——
        #   独立审查原版这里硬编码了"零校验"，于是在修复之后重跑会打出与实际相反的结论
        #   （而实际三行全是"A 没收到"），看日志的人会被误导。
        if got:
            print("结论：❌ 控制报文【被投递进去了】—— 中继没有拦住控制类报文，局域网内可远程操纵 wsjtx。")
            print("      ⇒ 检查 smart_relay.py 的 --allow-types 默认值，别把它放宽成 '*'。")
        else:
            print("结论：✅ 控制报文【全被拦住了】—— 中继只放行数据类（--allow-types 默认不含控制类）。")
            print("      ⇒ 这是期望行为。若将来要开反控，必须显式 --allow-types 放开，并先想清楚鉴权。")

        log.flush()
        txt = open(os.path.join(HERE, "rd-inject-relay.log"), "rb").read().decode("utf-8", "replace")
        print("\n--- relay 日志 ---")
        for line in txt.splitlines():
            if any(k in line for k in ("学到", "推给", "→")):
                print("   " + line)
        return 0
    finally:
        proc.terminate()
        try: proc.wait(timeout=3)
        except Exception: proc.kill()
        log.close()
        print("\nrelay 已停")


if __name__ == "__main__":
    sys.exit(main())

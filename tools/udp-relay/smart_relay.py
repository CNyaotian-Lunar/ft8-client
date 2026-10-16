#!/usr/bin/env python3
"""smart_relay.py —— 智能 UDP 中继（本机侧）。

【为什么需要它】WSJT-X 的 UDP 是**请求-回复模型**：
  · 它 `bind(AnyIPv4)` —— **不带端口**，所以它的接收端口是【临时分配】的
    （MessageClient.cpp:177-187 的注释原话："bind to an ephemeral port ... for sending datagrams"）
  · 它往 `UDPServer:UDPServerPort` 发心跳；**谁想回它，得从心跳的源端口看出来**
  ⇒ 所以"把远端的 Decode 推给它的界面"，**必须先学到它的端口**。

【它干三件事】
  ① 听 `--listen` ——
     · wsjtx 的心跳/状态（它的 UDPServer 指向这里）⇒ ⭐ **从源端口学出"它是谁"**
     · 电台机转发来的远端的报文（Decode 等）⇒ 推给学到的那个端口
  ② **认出 wsjtx**：靠报文的 `id` 字段（我们的 probe 发 "ft8-headless-probe"，wsjtx 发 "WSJT-X"）
  ③ 把允许的报文推给 wsjtx

================================ 安全设计（2026-10-17 加）================================
⚠️ 第一版是"零校验"的，独立审查（独立审查）**实测攻破了两条**：

  D1【高】学端口零校验 ⇒ 伪造一条 `id=="WSJT-X"` 的心跳就能把 `wsjtx_addr` 改成攻击者的地址，
          之后所有远端解码只推给攻击者、真 wsjtx 一条收不到（实测劫持成功）。
  D2【高】无来源校验地原样转发【任意类型】⇒ `Close(6)` / `HaltTx(8)` / `Configure(15)`
          全部被投进 wsjtx 的入站端口（实测三条全部投递成功）。
          而 wsjtx 那边 `mainwindow.cpp:725` `connect(close, &MainWindow::close)`
          ⇒ ⚠️ **局域网里任意主机能远程关掉本机的 WSJT-X**。

⇒ 现在加三道闸（**默认全部打开**）：
  ① `--allow-ip`：**只接受白名单来源**（默认 `127.0.0.1`）—— 转发和学端口都受它约束
  ② `--allow-types`：**只转发数据类报文**（默认 `Decode` / `Clear` / `Status` / `WSPRDecode`），
     **控制类（Reply/Close/HaltTx/FreeText/Configure/Location/HighlightCallsign/…）默认拒收** ——
     ⚠️ 反控将来要用，**那时候要在调用方显式 `--allow-types` 放开**，别默认开门
  ③ `--lock-first`：**学到 wsjtx 地址后锁死**（防止被后来的报文改掉）

usage:
  python smart_relay.py --listen 127.0.0.1:2238 [--verbose]
  # 电台机的转发脚本把目标指到这台机的 2238：
  #   python udp_fwd.py --listen 127.0.0.1:2237 --target <本机IP>:2238

⚠️ 默认 `--listen 0.0.0.0` 是为了兼容"电台机跨机转发"的场景；
   如果 relay 和 wsjtx 在同机（推荐），**用 `--listen 127.0.0.1:2238` 把攻击面收到本机**。
"""
import argparse
import socket
import struct
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MAGIC = bytes([0xAD, 0xBC, 0xCB, 0xDA])
TYPE = {0: "Heartbeat", 1: "Status", 2: "Decode", 3: "Clear", 4: "Reply", 5: "QSOLogged",
        6: "Close", 7: "Replay", 8: "HaltTx", 9: "FreeText", 10: "WSPRDecode", 11: "Location",
        12: "LoggedADIF", 13: "HighlightCallsign", 14: "SwitchConfiguration", 15: "Configure",
        16: "AnnotationInfo"}
TYPE_BY_NAME = {v.lower(): k for k, v in TYPE.items()}

# ⭐ 默认只放【数据类】：这些是"远端解码流"需要的，推错了最多显示错行
#   ⚠️ 控制类（4 Reply / 6 Close / 8 HaltTx / 9 FreeText / 14 / 15 / 16 …）**默认不转**
DEFAULT_ALLOW_TYPES = ["decode", "clear", "status", "wsprdecode", "qsologged", "loggedadif"]


def parse_header(data):
    """返回 (type_num, type_name, id_str) 或 None。"""
    if len(data) < 16 or data[:4] != MAGIC:
        return None
    typ = struct.unpack("!I", data[8:12])[0]
    idlen = struct.unpack("!I", data[12:16])[0]
    ident = ""
    if 0 < idlen < 64 and len(data) >= 16 + idlen:
        ident = data[16:16 + idlen].decode("utf-8", "replace")
    return typ, TYPE.get(typ, "type%d" % typ), ident


def parse_addr(s):
    host, _, port = s.rpartition(":")
    if not host or not port:
        raise argparse.ArgumentTypeError("地址要写成 host:port")
    return host, int(port)


def main():
    ap = argparse.ArgumentParser(description="智能 UDP 中继：学会 wsjtx 的端口，把远端报文推给它")
    ap.add_argument("--listen", default="0.0.0.0:2238", type=parse_addr)
    ap.add_argument("--wsjtx-id", default="WSJT-X", help="哪个 id 算作本机 wsjtx（默认 WSJT-X）")
    ap.add_argument("--verbose", action="store_true", help="每条报文都打")
    ap.add_argument("--stats-every", type=int, default=30, help="每 N 秒打一次统计（默认 30）")

    # ⭐ 三道安全闸（默认最严）
    ap.add_argument("--allow-ip", default="127.0.0.1",
                    help="只接受这个来源 IP 的报文（逗号分隔多个；传 '*' 放开，⚠️不推荐）")
    ap.add_argument("--allow-types", default=",".join(DEFAULT_ALLOW_TYPES),
                    help="只转发这些类型的报文（逗号分隔的【名字】，如 Decode,Clear,Status）；传 '*' 放开全部")
    ap.add_argument("--lock-first", action="store_true", default=True,
                    help="学到 wsjtx 地址后锁死（默认开；--no-lock-first 可关）")
    ap.add_argument("--no-lock-first", dest="lock_first", action="store_false")
    args = ap.parse_args()

    allow_ips = None if args.allow_ip.strip() == "*" else {x.strip() for x in args.allow_ip.split(",") if x.strip()}
    if args.allow_types.strip() == "*":
        allow_types = None
    else:
        allow_types = set()
        for nm in args.allow_types.split(","):
            nm = nm.strip().lower()
            if not nm:
                continue
            if nm in TYPE_BY_NAME:
                allow_types.add(TYPE_BY_NAME[nm])
            elif nm.isdigit():
                allow_types.add(int(nm))
            else:
                print("[relay] ⚠️ 不认识的类型名：%s（忽略）" % nm, file=sys.stderr, flush=True)

    lhost, lport = args.listen
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rx.bind((lhost, lport))
    rx.settimeout(1.0)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print("[relay] 听 %s:%d" % (lhost, lport), flush=True)
    print("[relay] 安全闸：来源 IP = %s · 允许类型 = %s · 学到后锁定 = %s"
          % (("任意(!)" if allow_ips is None else ",".join(sorted(allow_ips)))
             , ("任意(!)" if allow_types is None else ",".join(sorted(TYPE[t] for t in allow_types)))
             , args.lock_first), flush=True)
    print("[relay] 等本机 wsjtx 发心跳（它的 UDPServer 要指向这个地址）...", flush=True)

    wsjtx_addr = None
    locked = False
    t0 = time.time()
    last_stats = time.time()
    n_total = n_to_wsjtx = n_from_wsjtx = n_rejected = 0

    while True:
        try:
            data, peer = rx.recvfrom(65535)
        except socket.timeout:
            data = None

        if data is not None:
            n_total += 1

            # ⭐ 闸①：来源 IP 白名单（转发与学端口都受它约束）
            if allow_ips is not None and peer[0] not in allow_ips:
                n_rejected += 1
                print("  ⛔ 拒收：来源 %s:%d 不在白名单" % (peer[0], peer[1]), flush=True)
                data = None

        if data is not None:
            h = parse_header(data)
            if h is None:
                n_rejected += 1
                if args.verbose:
                    print("  ⛔ 拒收：非 WSJT-X 报文 %d 字节 来自 %s:%d" % (len(data), peer[0], peer[1]), flush=True)
            else:
                typ, tname, ident = h
                if ident == args.wsjtx_id:
                    # ⭐ 这是本机 wsjtx 发出来的 ⇒ 记下它的端口
                    if locked and wsjtx_addr != peer:
                        n_rejected += 1
                        print("  ⛔ 拒收：wsjtx 地址已锁定为 %s:%d，不接受 %s:%d 的改写"
                              % (wsjtx_addr[0], wsjtx_addr[1], peer[0], peer[1]), flush=True)
                    elif wsjtx_addr != peer:
                        print("  ⭐ 学到 wsjtx 的地址: %s:%d（它发了 %s）" % (peer[0], peer[1], tname), flush=True)
                        wsjtx_addr = peer
                        if args.lock_first:
                            locked = True
                            print("  🔒 地址已锁定（--no-lock-first 可关）", flush=True)
                    n_from_wsjtx += 1
                    if args.verbose:
                        print("  ← wsjtx %-14s (%d 字节)" % (tname, len(data)), flush=True)
                elif wsjtx_addr is not None:
                    # ⭐ 闸②：类型白名单 —— **控制类默认不转**
                    if allow_types is not None and typ not in allow_types:
                        n_rejected += 1
                        print("  ⛔ 拒收：类型 %s 不在允许列表（控制类默认不转）来自 %s:%d"
                              % (tname, peer[0], peer[1]), flush=True)
                    else:
                        try:
                            tx.sendto(data, wsjtx_addr)
                            n_to_wsjtx += 1
                            print("  → 推给 wsjtx: %-14s id=%s (%d 字节)" % (tname, ident, len(data)), flush=True)
                        except OSError as e:
                            print("  ⚠️ 推给 wsjtx 失败: %s" % e, flush=True)
                else:
                    n_rejected += 1
                    print("  ⚠️ 还没学到 wsjtx 的地址，%s 的报文先丢（等它发心跳）" % tname, flush=True)

        if time.time() - last_stats >= args.stats_every:
            last_stats = time.time()
            print("[relay] 累计: 收 %d · 来自wsjtx %d · 推给wsjtx %d · 拒收 %d · wsjtx=%s%s"
                  % (n_total, n_from_wsjtx, n_to_wsjtx, n_rejected, wsjtx_addr,
                     "（已锁定）" if locked else ""), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[relay] 停", flush=True)

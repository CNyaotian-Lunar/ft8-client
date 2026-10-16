#!/usr/bin/env python3
"""udp_fwd.py —— 极简 UDP 转发（单播）。

干什么：把一个端口上收到的东西，原样转发到另一个地址:端口。
为什么要它：我们把 WSJT-X 的【引擎】和【UI】拆到了两台机器上 ——
  电台机跑 headless-probe（发 UDP 到 127.0.0.1:2237）
  本机跑 wsjtx.exe（当 UI，它自己 bind 2237 收命令）
  ⇒ 需要两道转发把两边接起来。

典型用法
  电台机：python udp_fwd.py --listen 127.0.0.1:2237 --target <TARGET-IP>:2238
  本机  ：python udp_fwd.py --listen 0.0.0.0:2238     --target 127.0.0.1:2237

⚠️ 三条纪律：
  ① 只转发，不解析（解析留给真正的消费者；这样出问题时"转发对不对"和"解析对不对"能分开验）
  ② 每条只打一行摘要（--quiet 关掉），并且【限速打印】—— 否则高频报文会把日志刷爆
  ③ 用 SO_REUSEADDR 但【记录】它：端口被别的进程占着时 bind 仍会成功、包会进错进程
     （本项目踩过：一个残留的消费者占着 2237，我们 bind "成功"却收不到任何东西）—— 见 --check-port
"""
import argparse
import socket
import struct
import sys
import time

MAGIC = bytes([0xAD, 0xBC, 0xCB, 0xDA])
TYPE_NAMES = {
    0: "Heartbeat", 1: "Status", 2: "Decode", 3: "Clear", 4: "Reply", 5: "QSOLogged",
    6: "Close", 7: "Replay", 8: "HaltTx", 9: "FreeText", 10: "WSPRDecode", 11: "Location",
    12: "LoggedADIF", 13: "HighlightCallsign", 14: "SwitchConfiguration", 15: "Configure",
    16: "AnnotationInfo",
}


def parse_addr(s):
    host, _, port = s.rpartition(":")
    if not host or not port:
        raise argparse.ArgumentTypeError("地址要写成 host:port（如 127.0.0.1:2237）")
    return host, int(port)


def summarize(data):
    """只做最小解析：magic / type / id —— 够用来判断'转发的是不是我要的东西'。"""
    if len(data) < 16 or data[:4] != MAGIC:
        return "非 WSJT-X 报文 (%d 字节)" % len(data)
    typ = struct.unpack("!I", data[8:12])[0]
    idlen = struct.unpack("!I", data[12:16])[0]
    ident = ""
    if 0 < idlen < 64 and len(data) >= 16 + idlen:
        ident = data[16:16 + idlen].decode("utf-8", "replace")
    return "%s id=%s (%d 字节)" % (TYPE_NAMES.get(typ, "type%d" % typ), ident, len(data))


def main():
    ap = argparse.ArgumentParser(description="极简 UDP 转发（单播）")
    ap.add_argument("--listen", required=True, type=parse_addr,
                    help="收在哪个地址（如 127.0.0.1:2237 / 0.0.0.0:2238）")
    ap.add_argument("--target", required=True, type=parse_addr,
                    help="转发到哪（如 <TARGET-IP>:2238）")
    ap.add_argument("--quiet", action="store_true", help="不打印每条报文摘要")
    ap.add_argument("--print-every", type=int, default=1, metavar="N",
                    help="每 N 条打印一次摘要（默认 1；高流量时调大）")
    ap.add_argument("--check-port", action="store_true",
                    help="启动前检查 listen 端口是否已被别人占着（SO_REUSEADDR 会掩盖这种占用）")
    args = ap.parse_args()

    lhost, lport = args.listen
    thost, tport = args.target

    if args.check_port:
        # 只读探测：试 bind 且【不】设 SO_REUSEADDR —— 被别人占着就会失败
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.bind((lhost, lport))
            print("[fwd] 端口 %s:%d 空闲 ✅" % (lhost, lport), file=sys.stderr, flush=True)
        except OSError as e:
            print("[fwd] ⚠️ 端口 %s:%d 已被占用：%s" % (lhost, lport, e), file=sys.stderr, flush=True)
            print("[fwd]    （SO_REUSEADDR 下你能 bind 成功但收不到包 —— 先查是谁占着）",
                  file=sys.stderr, flush=True)
        finally:
            probe.close()

    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rx.bind((lhost, lport))

    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)   # 允许广播（WSJT-X 也能发广播）

    print("[fwd] %s:%d  →  %s:%d   （Ctrl+C 停）" % (lhost, lport, thost, tport),
          file=sys.stderr, flush=True)

    n = 0
    t0 = time.time()
    try:
        while True:
            data, peer = rx.recvfrom(65535)
            n += 1
            try:
                tx.sendto(data, (thost, tport))
            except OSError as e:
                print("[fwd] 发送失败：%s" % e, file=sys.stderr, flush=True)
                continue
            if not args.quiet and (n % max(args.print_every, 1) == 0):
                print("[fwd] #%d  from %s:%d  %s" % (n, peer[0], peer[1], summarize(data)),
                      file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        dt = max(time.time() - t0, 1e-9)
        print("[fwd] 共转发 %d 条 / %.1f 秒（%.2f 条/秒）" % (n, dt, n / dt),
              file=sys.stderr, flush=True)
        rx.close()
        tx.close()


if __name__ == "__main__":
    main()

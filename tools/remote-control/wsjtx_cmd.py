#!/usr/bin/env python3
"""wsjtx_cmd.py —— 往 WSJT-X 发【控制类】UDP 命令（反向控制那一半）。

════════════════════════════════════════════════════════════════════════════
⚠️ 这个脚本会真的改变对端状态（有的还会引发发射请求）。用之前请读完下面这段。
════════════════════════════════════════════════════════════════════════════

【它能发什么】（类型号来自 `Network/NetworkMessage.hpp:547-563`）
    4  Reply               ⚠️ 会触发一次"回应呼叫"（WSJT-X 里会 En Tx / 排队发信标）
    8  HaltTx             ⚠️ 会停止发射（含"停掉别人正在进行的 QSO"）
    9  FreeText            ⚠️ 会改/发自由文本消息
    13 HighlightCallsign   安全（只高亮）
    15 Configure           ⚠️ 会改模式/频率容差/Rx DF/DX 呼号网格等

【约束（本项目）】
  · ⭐ **不发射**：`Reply` 是"请求 WSJT-X 发射"，**本项目当前阶段不使用它**；
    本脚本把它实现出来是为了**将来**（且必须用户在场 + 接天线 + 驻波保护），
    ⚠️ **默认不提供 `--reply`**（要用得显式 `--i-know-this-transmits`）。
  · SWR 只告警不停发（那是电台侧的事，这里不涉及）。
  · ⚠️ **`AcceptUDPRequests=true` 是前提** —— 而它一开，**整套控制面同时对外可用**（独立审查 D2）。
    ⇒ 所以**用 relay 转发时，`--allow-types` 默认不含这些类型**（要显式放开）。

【用法】
    # 查状态（最安全，先用它确认端口通）
    python wsjtx_cmd.py --target 127.0.0.1:2237 --halt-tx       # 停发射（幂等，最常用）
    python wsjtx_cmd.py --target 127.0.0.1:2237 --highlight N0CALL
    python wsjtx_cmd.py --target 127.0.0.1:2237 --configure --mode FT8 --rx-df 1500 --dx-call JA1ABC --dx-grid PM95
    # ⚠️ 下面这个会发射，要显式确认：
    python wsjtx_cmd.py --target ... --reply --call JA1ABC --grid PM95 --snr -7 --i-know-this-transmits

【怎么找 --target】
    ⚠️ WSJT-X 的**接收端口是临时分配的**（它 `bind(AnyIPv4)` 不带端口）⇒ 不能猜。
    · 本机自测：`Get-NetUDPEndpoint -OwningProcess <wsjtx pid> | 排除 2237/2238` 看那个临时端口
    · 经 relay：发到 relay 的 `--listen`（但 relay 默认会【拒收】控制类，见上）
"""
import argparse
import socket
import struct
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MAGIC = bytes([0xAD, 0xBC, 0xCB, 0xDA])
SCHEMA = 2
ID = "ft8-client-ctl"          # ⭐ 用我们自己的 id（别冒充 "WSJT-X"，否则 relay 会误学地址）
TYPE = {"Reply": 4, "HaltTx": 8, "FreeText": 9, "HighlightCallsign": 13, "Configure": 15}

QB = lambda s: struct.pack("!I", len(s.encode())) + s.encode()      # QByteArray（长度=字节数）


def envelope(typ, payload=b""):
    return MAGIC + struct.pack("!I", SCHEMA) + struct.pack("!I", typ) + QB(ID) + payload


def mk_halt_tx(auto_only=False):
    # NetworkMessage::HaltTx —— `bool auto_only`
    return envelope(TYPE["HaltTx"], b"\x01" if auto_only else b"\x00")


def mk_highlight(callsign, bg=(0, 0, 0), fg=(0, 0, 0), last_only=False):
    # NetworkMessage::HighlightCallsign —— QString callsign, QColor bg, QColor fg, bool last_only
    # ⚠️ QColor 在 QDataStream 里按 **QVariant** 发（`QColor::name()` 那种字符串形式最稳：
    #    Qt 把它序列化成 `QVariant(QString)` ⇒ 见下。这里用"QString 形式的颜色名"。
    def qs(s):
        b = s.encode("utf-16-be")
        return struct.pack("!I", len(s)) + b
    def qcolor(rgb):
        return qs("#%02x%02x%02x" % rgb)         # ⚠️ 简化实现：按 QString 发（上游 QColor 走 QVariant）
    return envelope(TYPE["HighlightCallsign"],
                    qs(callsign) + qcolor(bg) + qcolor(fg) + (b"\x01" if last_only else b"\x00"))


def mk_free_text(text, send=False):
    # NetworkMessage::FreeText —— QString text, bool send
    def qs(s):
        b = s.encode("utf-16-be")
        return struct.pack("!I", len(s)) + b
    return envelope(TYPE["FreeText"], qs(text) + (b"\x01" if send else b"\x00"))


def mk_configure(mode=None, freq_tol=None, submode=None, fast_mode=None, tr_period=None,
                 rx_df=None, dx_call=None, dx_grid=None, generate_messages=None):
    # NetworkMessage::Configure（mainwindow.cpp:15373 remote_configure）
    #   QString mode, quint32 frequency_tolerance, QString submode, bool fast_mode,
    #   quint32 tr_period, quint32 rx_df, QString dx_call, QString dx_grid, bool generate_messages
    #   ⚠️ 空字符串 / 0 表示"不改这一项"
    def qs(s):
        b = s.encode("utf-16-be")
        return struct.pack("!I", len(s)) + b
    def qb(s):
        b = s.encode()
        return struct.pack("!I", len(b)) + b
    return envelope(TYPE["Configure"],
                    qb(mode or "") + struct.pack("!I", freq_tol or 0) + qb(submode or "")
                    + (b"\x01" if fast_mode else b"\x00") + struct.pack("!I", tr_period or 0)
                    + struct.pack("!I", rx_df or 0) + qb(dx_call or "") + qb(dx_grid or "")
                    + (b"\x01" if generate_messages else b"\x00"))


def mk_reply(time_ms, snr, dt, df, mode, message, low_confidence=False, modifiers=0):
    # NetworkMessage::Reply —— QTime time, qint32 snr, float dt(→8B double), quint32 df,
    #                          QByteArray mode, QByteArray message, bool low_confidence, quint8 modifiers
    return envelope(TYPE["Reply"],
                    struct.pack("!I", time_ms) + struct.pack("!i", snr) + struct.pack("!d", dt)
                    + struct.pack("!I", df) + QB(mode) + QB(message)
                    + (b"\x01" if low_confidence else b"\x00") + struct.pack("!B", modifiers))


def main():
    ap = argparse.ArgumentParser(description="往 WSJT-X 发控制类 UDP 命令")
    ap.add_argument("--target", required=True, help="host:port（⚠️ 接收端口是临时的，别猜 2237）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要发的字节，不真发")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--halt-tx", action="store_true")
    g.add_argument("--highlight", metavar="CALL")
    g.add_argument("--free-text", metavar="TEXT")
    g.add_argument("--configure", action="store_true")
    g.add_argument("--reply", action="store_true", help="⚠️ 会触发发射")
    ap.add_argument("--auto-only", action="store_true", help="HaltTx: 只停自动发射")
    ap.add_argument("--send", action="store_true", help="FreeText: 立刻发出去（否则只设置）")
    ap.add_argument("--last-only", action="store_true", help="HighlightCallsign: 只高亮最后一个")
    # configure / reply 的参数
    ap.add_argument("--mode"); ap.add_argument("--submode"); ap.add_argument("--rx-df", type=int)
    ap.add_argument("--dx-call"); ap.add_argument("--dx-grid"); ap.add_argument("--tr-period", type=int)
    ap.add_argument("--freq-tol", type=int); ap.add_argument("--fast-mode", action="store_true")
    ap.add_argument("--generate-messages", action="store_true")
    ap.add_argument("--call"); ap.add_argument("--grid"); ap.add_argument("--snr", type=int, default=-7)
    ap.add_argument("--dt", type=float, default=0.0); ap.add_argument("--df", type=int, default=1500)
    ap.add_argument("--time-ms", type=int, default=0)
    ap.add_argument("--i-know-this-transmits", action="store_true",
                    help="⭐ --reply 的必要确认（本项目的约束：不发射）")
    args = ap.parse_args()

    if args.halt_tx:
        pkt, what = mk_halt_tx(args.auto_only), "HaltTx(auto_only=%s)" % args.auto_only
    elif args.highlight:
        pkt, what = mk_highlight(args.highlight, last_only=args.last_only), "HighlightCallsign(%s)" % args.highlight
    elif args.free_text is not None:
        pkt, what = mk_free_text(args.free_text, args.send), "FreeText(%r, send=%s)" % (args.free_text, args.send)
    elif args.configure:
        pkt = mk_configure(args.mode, args.freq_tol, args.submode, args.fast_mode, args.tr_period,
                           args.rx_df, args.dx_call, args.dx_grid, args.generate_messages)
        what = "Configure(mode=%s rx_df=%s dx_call=%s)" % (args.mode, args.rx_df, args.dx_call)
    else:  # reply
        if not args.i_know_this_transmits:
            print("⛔ 拒绝：--reply 会触发发射，而本项目当前约束是【不发射】。", file=sys.stderr)
            print("   确实要做（用户在场 + 接天线 + 驻波保护）再加 --i-know-this-transmits。", file=sys.stderr)
            return 2
        if not (args.call and args.grid):
            print("⛔ --reply 需要 --call 和 --grid", file=sys.stderr)
            return 2
        pkt = mk_reply(args.time_ms, args.snr, args.dt, args.df, "~",
                       "REPLY %s %s" % (args.call, args.grid), modifiers=0)
        what = "Reply(%s %s)" % (args.call, args.grid)

    host, _, port = args.target.rpartition(":")
    port = int(port)
    print("[cmd] 目标 %s:%d" % (host, port))
    print("[cmd] 要发：%s" % what)
    print("[cmd] 字节数：%d" % len(pkt))
    print("[cmd] hex：%s" % pkt.hex(" "))
    if args.dry_run:
        print("[cmd] （--dry-run，没有发出）")
        return 0

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(pkt, (host, port))
    s.close()
    print("[cmd] ✅ 已发出")
    print("[cmd] ⚠️ 这个脚本【不知道对端收没收到】—— UDP 无确认，而且 WSJT-X 不会回执")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# udp-relay —— 把"电台机解出来的解码"送到"本机的 UI"

## 为什么需要它（**这条决定整个架构**）

WSJT-X 的 UDP **不是"我在某个端口听"**：

```cpp
// Network/MessageClient.cpp:177-187
QHostAddress interface_addr {AnyIPv4};
if (localAddress () != interface_addr) {
    // ⭐ 上游注释原话：bind to an ephemeral port ... set up for **sending** datagrams
    bind (interface_addr);          // ← 不带端口 ⇒ 绑到【临时端口】
}
```
⇒ ⭐ **`UDPServer` / `UDPServerPort`（2237）是"我【发】到哪里"，不是"我听在哪个端口"**。
⇒ ⭐⭐ **它的接收端口每次启动都变**（实测 `53092` / `59419` / `62494` / `52096` / `60929` / `64854` …）。
⇒ ⭐⭐⭐ **所以"往它推解码"必须先【学到它的端口】** —— 只能从**它自己发出来的心跳**里学。

⚠️ **"2237 上 netstat 什么都没有"是正常的**（2237 是**目标端口**）。要看它的端口，看**它发出来的报文的源端口**。

## 两个脚本

| 脚本 | 干什么 | 用法 |
|---|---|---|
| `udp_fwd.py` | **傻转发**（收 → 原样发） | `python udp_fwd.py --listen 127.0.0.1:2237 --target <TARGET-IP>:2238` |
| `smart_relay.py` | **智能中继**：从心跳学出 wsjtx 的端口，再把别处来的报文推给它 | `python smart_relay.py --listen 127.0.0.1:2238 --verbose` |

### 推荐拓扑

```
电台机： headless-probe ──► 127.0.0.1:2237 ──► udp_fwd.py ──► 本机:2238
本机　： smart_relay.py（听 2238）──► 学到 wsjtx 的临时端口 ──► 推给它 ──► 界面显示
        ⚠️ 前提：把 wsjtx 的 WSJTX-X.ini 里 UDPServerPort 指向 2238（这样它才会往那儿发心跳）
```

---

## 🔒 安全设计（2026-10-17 加，**默认全开**）

⭐ **第一版是"零校验"的，独立审查（独立审查）实测攻破了两条**，所以现在有三道闸：

| 闸 | 参数 | 默认 | 挡什么 |
|---|---|---|---|
| ① 来源 IP 白名单 | `--allow-ip` | **`127.0.0.1`** | D1：**伪造 `id=="WSJT-X"` 的心跳抢走地址**（实测可劫持：真 UI 静默收不到） |
| ② 类型白名单 | `--allow-types` | **数据类**（`Decode`/`Clear`/`Status`/`WSPRDecode`/`QSOLogged`/`LoggedADIF`） | D2：**`Close(6)` / `HaltTx(8)` / `Configure(15)` 被原样投进 wsjtx** ⇒ ⚠️ `mainwindow.cpp:725` `connect(close,&MainWindow::close)` ⇒ **LAN 内任意主机能远程关掉本机 WSJT-X** |
| ③ 学到后锁定 | `--lock-first` | **开** | D1 的另一半：地址学到后**不接受改写** |

### 🩸 一个必须知道的耦合（**写进部署文档**）

```
入站 Decode 要生效 ⇒ 必须开 wsjtx 的 `AcceptUDPRequests=true`
                      （`Configuration.cpp:2670` 默认 **false**）
⚠️ 而一开，MessageClient 的**整套入站控制面同时可用**
   （Reply / Close / HaltTx / FreeText / Configure / Location / HighlightCallsign …）
⇒ "不改配置收不到远端解码；一改配置，全套控制面一起对外"
⇒ ⭐ 所以 relay 的类型闸不是"可选加固"，而是【默认必须】——
   将来要开反控，**先想清鉴权，再显式 `--allow-types` 放开**
```

### ⚠️ 监听地址

默认 `--listen 0.0.0.0:2238` 是为了兼容"电台机跨机转发"。
⭐ **如果 relay 和 wsjtx 同机（推荐），用 `--listen 127.0.0.1:2238`** —— 直接把攻击面收回本机。

---

## 回归测试（`selftest/`）

`selftest/rd-hijack.py` / `selftest/rd-inject.py` —— **独立审查当时用来攻破的两个脚本**，留下来当回归。

```powershell
# 它们直接 Popen 上面那两个脚本（指向本目录，不是副本）
python selftest\rd-hijack.py     # 期望："★ 未劫持：仍然是 A 收到，C 没收到"
python selftest\rd-inject.py     # 期望："结论：✅ 控制报文【全被拦住了】"
```

⭐ **"用攻击方自己的脚本验自己的修复"** 比"自己写个测试说没问题"硬得多 ——
它证明**修的是那个洞**，而不是修了别的东西。

⚠️ **`rd-inject.py` 的结论已改成按实际结果生成** ——
独立审查原版把"零校验"写死在 `print` 里，于是**修好之后它会打出与实际相反的文案**（实测三行全是"A 没收到"，结论却说"零校验"）。
🩸 **测试脚本里硬编码的结论比它要测的 bug 更坏**：它让日志骗人。

---

## 两个 Python 侧的坑（都已在脚本里处理）

- ⭐ **`--check-port`**（`udp_fwd.py`）：**`SO_REUSEADDR` 会让"端口已被占用"表现为"bind 成功但收不到包"**。
  本项目踩过：一个残留的消费者占着端口，我们 bind"成功"却什么都收不到。
- ⭐ **`sys.stdout.reconfigure(encoding='utf-8')` 是每段脚本的第一行** ——
  ⚠️ 不加的话，往 GBK 控制台打 `⇒` 这类字符会直接 `UnicodeEncodeError` 崩掉（本项目踩过 N 次）。

## 还没做的（别忘）

- ⚠️ **反控方向是断的**：relay 目前**丢弃** wsjtx 的全部出站（`id=="WSJT-X"` 的报文只用来学地址）——
  ⇒ 本机 wsjtx 自己的解码 / Status / QSO 日志**到不了电台机**。当前目标（只把远端解码显示出来）不受影响。
- ⚠️ `Decode` 的 `message` **没有长度上限**（审查实测 60 KB 被完整接受 ⇒ 可能拖慢 UI）。

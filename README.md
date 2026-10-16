# ft8-client tools / 工具集

Working tools from the FT8/FT4 client project (a WSJT-X fork).
本项目的实用工具集（WSJT-X fork 的配套件）。

## UDP relay / UDP 中继

WSJT-X binds an *ephemeral* port for receiving (`MessageClient.cpp:177-187`), so `UDPServer`
is where it **sends**, not where it listens. The receiving port changes every launch, which
means it can only be learned from the heartbeat it emits.

WSJT-X 的接收端口是**临时分配**的（源码里 `bind(AnyIPv4)` 不带端口），所以 `UDPServer` 是
"我**发**到哪里"而不是"我听在哪个端口"；那个端口每次启动都变，只能从它主动发的心跳里学。

- `udp-relay/udp_fwd.py` — dumb forwarder / 傻转发
- `udp-relay/smart_relay.py` — learns the port, then pushes datagrams back / 学会端口再推
  - three gates by default: source-IP allowlist, type allowlist (data classes only), lock-after-learn
  - 默认三道闸：来源白名单 · 类型白名单（只放数据类）· 学到后锁定
- `udp-relay/selftest/` — the attack scripts used during review, kept as regression tests
  / 审查期间的攻击脚本，留作回归测试

## Deploy / 部署

- `deploy/pack-wsjtx-runtime.ps1` + `pack-deps.sh` — assemble a runnable WSJT-X directory
  from a build tree: recursive dependency collection plus a self-check of the six items that
  each cost real debugging time.
  从构建目录组装出能跑的运行目录：递归收依赖 + 六项自检（每一项都曾真的坑过）。

## Verify / 验收

- `verify/read-band-activity.ps1` — read WSJT-X's Band Activity pane over UI Automation and
  assert on it. Qt's `DisplayText` exposes UIA `TextPattern` (not `ValuePattern`).
  用 UI Automation 读 WSJT-X 的「波段活动」栏并做断言。

## Remote control / 反向控制

- `remote-control/wsjtx_cmd.py` — sends the control-class UDP messages.
  `Reply` is implemented but gated behind an explicit flag; this project does not transmit.
  发控制类 UDP 报文；`Reply` 已实现但需显式开关（本项目不发射）。

---

Licence / 许可：GPL-3.0 (this project forks WSJT-X, which is GPL-3.0).

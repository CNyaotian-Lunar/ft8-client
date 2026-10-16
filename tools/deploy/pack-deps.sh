#!/usr/bin/env bash
# pack-deps.sh —— 把 MSYS2 里 Qt 程序需要的东西拷到目标目录
#
# 由 pack-wsjtx-runtime.ps1 调用；也可以单独用：
#   bash pack-deps.sh <目标目录-Windows路径> <MSYS2根-Windows路径> [force]
#
# ⚠️ 这个脚本【故意不接收任何注入的变量】—— 参数走位置参数。
#    （2026-10-16 踩过：在 PowerShell 的双引号 here-string 里拼 bash，
#      `$OUT` / `$sub:` 会被 PowerShell 先展开一次，报"变量未设置" / "变量引用无效"。
#      拼字符串这条路走不通，就别走。）

set -euo pipefail

# ⚠️ 必须先把 MSYS2 的 bin 加进 PATH —— 否则连 tr / sed 都找不到
#   （2026-10-16 踩过：从 PowerShell 调 bash，PATH 是 Windows 的，`tr: command not found`，
#    导致下面的路径转换整行垮掉、传出 `//tmp/...` 这种坏路径）
export PATH="/usr/bin:/bin:$PATH"

OUT_WIN="$1"
MSYS_WIN="$2"
FORCE="${3:-}"
BUILD_WIN="${4:-$1}"     # ⭐ 第 4 参数 = 构建目录（exe 的来源）。不传则假定 = 目标目录

# Windows 路径 → MSYS2 路径（D:\a\b → /d/a/b）
win2msys() {
    local p="$1"
    p="${p//\\//}"                       # 反斜杠 → 正斜杠
    if [[ "$p" =~ ^([A-Za-z]):(.*)$ ]]; then
        local drive
        drive="$(printf '%s' "${BASH_REMATCH[1]}" | tr 'A-Z' 'a-z')"
        printf '/%s%s\n' "$drive" "${BASH_REMATCH[2]}"
    else
        printf '%s\n' "$p"
    fi
}

OUT="$(win2msys "$OUT_WIN")"
MSYS="$(win2msys "$MSYS_WIN")"
BUILD="$(win2msys "$BUILD_WIN")"
MINGW="$MSYS/mingw64"

echo "  目标(msys): $OUT"
echo "  来源(msys): $BUILD"
echo "  MSYS2(msys): $MSYS"
echo ""

if [ ! -d "$OUT" ]; then echo "❌ 目标目录不存在：$OUT_WIN" >&2; exit 2; fi

echo "=== 0) 先把【可执行文件】拷过来 ==="
#
# 🩸 2026-10-17 踩的：这个脚本原来只拷【依赖】，隐含前提是"目标目录里已经有 exe"。
#    作者第一次验的时候恰好是【先手动拷了两个 exe】再跑的，所以"通过"了；
#    等真的往一个空目录组装时，它拷出 13 个 DLL、**一个 exe 都没有** ——
#    而"缺 exe"这件事在依赖清单里【根本不会出现】（没有 exe 就没有 ldd 的输入）。
#    ⇒ 判据："我这个脚本，在【完全空】的目标目录下跑过吗？"
EXES="wsjtx.exe jt9.exe"
copied_exe=0
for x in $EXES; do
    if [ -f "$BUILD/$x" ]; then
        if [ ! -f "$OUT/$x" ] || [ -n "$FORCE" ]; then
            cp -f "$BUILD/$x" "$OUT/" && copied_exe=$((copied_exe+1)) && echo "  + $x"
        fi
    else
        echo "  ⚠️ 构建目录里没有 $x（$BUILD）"
    fi
done
echo "  拷了 $copied_exe 个 exe"

echo ""
echo "=== 1) 收集依赖：读 ldd 的【not found】行 ==="
#
# ⚠️ 2026-10-16 踩过两次，这里是最容易做错的地方：
#   第一次：`ldd exe | grep mingw64` —— 只抓【解析到路径】的行。
#           ⚠️ 但目标是"把 DLL 拷进这个目录"，而 ldd 就在这个目录里解析：
#           目录还空着的时候，依赖全是 `=> not found`（那行【没有路径】），grep 一条都抓不到 ⇒
#           脚本报"需要 1 个"、拷出来只有 1 个 —— 而它在【已经拷好的目录】里跑是绿的（假绿！）。
#   正解：**读 `not found` 的行**（那才是"运行时真缺什么"），再去 MSYS2 找同名文件。
#
# 两种来源都算：
#   ① 目标目录里 exe 的 not found 项（要从 MSYS2 补）
#   ② qsqlite 插件自己的依赖（它不在目标目录里，直接对 MSYS2 的那份跑 ldd）
: > /tmp/ft8miss.txt
#
# ⭐ 关键：**只读 `not found` 的行，三个文件都读**
#
# 为什么不能再用 `grep -i mingw64`（2026-10-16 血的教训，就是这条让 libsqlite3-0.dll 漏掉）：
#   `ldd` 是按【它自己的 PATH / RPATH】解析的。MSYS2 的 `ldd` 默认 PATH 里没有 /mingw64/bin，
#   于是 **连 qsqlite.dll 都会把 libsqlite3-0.dll 报成 `=> not found`** ——
#   那一行【不含 mingw64 字样】，`grep -i mingw64` 直接漏掉。
#   ⇒ 而 libsqlite3-0.dll 恰恰是那条"漏了就卡在 Fatal error"的命根子（作者花了 10 轮）。
#   ⇒ 所以判据是 **"ldd 说 not found"**，不是 **"路径里带 mingw64"**。
#
# not found 行有两种形态（都要，取 $1 后统一取 basename）：
#   "\tlibsqlite3-0.dll => not found"                  ⇒ $1 = 文件名
#   "\t/mingw64/share/.../qsqlite.dll => not found"    ⇒ $1 = 整条路径
for f in "$OUT/wsjtx.exe" "$OUT/jt9.exe" "$MINGW/share/qt5/plugins/sqldrivers/qsqlite.dll"; do
    if [ -f "$f" ]; then
        ldd "$f" 2>/dev/null | awk '/not found/ {print $1}' | tr -d '\r' >> /tmp/ft8miss.txt || true
    fi
done

# ⭐⭐ 递归收敛：`ldd exe` 只报【直接依赖】——
#   比如它只会说"Qt5Gui.dll 缺"，**不会说 Qt5Gui.dll 自己还缺 libicu / libpng / libharfbuzz…**，
#   而那些是 GUI 的间接依赖，少一个照样起不来（2026-10-16 实测：先只拷了 21 个，程序起得来、
#   进程活着，但 MainWindowHandle=0 —— 没有主窗口，因为 Qt 的图形栈没凑齐）。
#   ⇒ 每拷进来一个 DLL，就再问它一遍"你还缺什么"，直到没有新东西为止。
echo "  （递归收敛中……）"
round=0
while :; do
    round=$((round+1))
    [ "$round" -gt 12 ] && { echo "  ⚠️ 递归超过 12 轮，停下（可能有环）"; break; }

    before=$(wc -l < /tmp/ft8miss.txt)
    for dll in "$OUT"/*.dll; do
        [ -f "$dll" ] || continue
        ldd "$dll" 2>/dev/null | awk '/not found/ {print $1}' | tr -d '\r' >> /tmp/ft8miss.txt || true
    done
    awk -F/ '{print $NF}' /tmp/ft8miss.txt \
        | grep -v '^$' | grep -viE '^api-ms-win-|^ext-ms-win-' \
        | sort -u > /tmp/ft8miss2.txt
    mv /tmp/ft8miss2.txt /tmp/ft8miss.txt
    after=$(wc -l < /tmp/ft8miss.txt)

    new=0
    while IFS= read -r b; do
        [ -z "$b" ] && continue
        [ -f "$OUT/$b" ] && continue
        for cand in "$MINGW/bin/$b" "$MINGW/lib/$b" "$MSYS/usr/bin/$b" \
                    "$MINGW/share/qt5/plugins/platforms/$b" \
                    "$MINGW/share/qt5/plugins/sqldrivers/$b"; do
            [ -f "$cand" ] && { cp -f "$cand" "$OUT/" && new=$((new+1)); break; }
        done
    done < /tmp/ft8miss.txt

    echo "    第 $round 轮：缺件清单 $before → $after，新拷 $new"
    [ "$new" -eq 0 ] && break
done

# 最后再收敛一次清单（供自检/报告用）
awk -F/ '{print $NF}' /tmp/ft8miss.txt | grep -v '^$' | grep -viE '^api-ms-win-|^ext-ms-win-' | sort -u > /tmp/ft8miss2.txt
mv /tmp/ft8miss2.txt /tmp/ft8miss.txt
echo "  最终仍缺 $(wc -l < /tmp/ft8miss.txt) 个"

echo ""
echo "=== 3) Qt 插件目录 ==="
for sub in platforms sqldrivers styles imageformats iconengines tls bearer; do
    src="$MINGW/share/qt5/plugins/$sub"
    if [ -d "$src" ]; then
        mkdir -p "$OUT/$sub"
        cp -n "$src"/*.dll "$OUT/$sub/" 2>/dev/null || true
        printf '  %-14s %s 个\n' "$sub" "$(ls "$OUT/$sub" 2>/dev/null | wc -l)"
    fi
done

echo ""
echo "=== 4) 结果 ==="
echo "  DLL 总数: $(ls "$OUT"/*.dll 2>/dev/null | wc -l)"
echo "  目录大小: $(du -sh "$OUT" 2>/dev/null | cut -f1)"

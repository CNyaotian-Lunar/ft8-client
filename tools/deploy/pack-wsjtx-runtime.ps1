# pack-wsjtx-runtime.ps1 —— 一键把自编的 WSJT-X 组装成"能直接跑"的目录
#
# 为什么要有它：Windows 上跑 MSYS2/mingw64 编出来的 Qt 程序，光有 exe 跑不起来 ——
#   要【39+ 个 DLL】+【6 个 Qt 插件目录】+【两个插件自己的依赖】。少一个的后果分三种：
#     · 缺 platforms\qwindows.dll      => 弹"缺少 DLL"，程序起不来（这个还算明显）
#     · 缺 sqldrivers\qsqlite.dll      => ⚠️ 它能起来、有窗口、responding=True，
#                                         但 main.cpp 的 db.open() 抛异常 -> catch ->
#                                         MessageBox("Fatal error") 卡住
#                                         => ⭐ MainWindow 构造函数【压根没开始】
#                                         表现成"我改的代码全都不生效"（作者为此花了 10 轮）
#     · 缺 libfftw3f_threads-3.dll     => jt9.exe 弹 Windows 系统错误框（同类）
#
# ⇒ 把"记住要拷哪些"换成【结构】：依赖从 ldd 反推，一次拷全，而且能重复跑。
#
# 用法：
#   .\pack-wsjtx-runtime.ps1 -BuildDir '<BUILD-DIR>'
#   .\pack-wsjtx-runtime.ps1 -BuildDir <构建目录> -OutDir 'D:\Tools\ft8-runtime' -Force
#
# ⚠️ 依赖收集的活交给同目录的 pack-deps.sh（纯 bash、位置参数）——
#    别在 PowerShell 里拼 bash 字符串：`$OUT` / `"$sub:"` 会被 PowerShell 先展开一次，
#    报"变量未设置" / "变量引用无效"（2026-10-16 踩过两轮）。

[CmdletBinding()]
param(
    [string]$BuildDir = '<BUILD-DIR>',
    [string]$Msys     = '<MSYS2>',
    [string]$OutDir   = '',
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $OutDir) { $OutDir = $BuildDir }
if (-not (Test-Path -LiteralPath $BuildDir)) { throw "构建目录不存在：$BuildDir" }
if (-not (Test-Path -LiteralPath $OutDir))   { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

$bash = Join-Path $Msys 'usr\bin\bash.exe'
if (-not (Test-Path -LiteralPath $bash)) { throw "找不到 MSYS2 bash：$bash" }

$sh = Join-Path $PSScriptRoot 'pack-deps.sh'
if (-not (Test-Path -LiteralPath $sh)) { throw "找不到 pack-deps.sh（应与本脚本同目录）：$sh" }

Write-Host "=== pack-wsjtx-runtime ==="
Write-Host "  构建目录: $BuildDir"
Write-Host "  产出目录: $OutDir"
Write-Host "  MSYS2   : $Msys"
Write-Host ""

$shArg = $sh -replace '\\', '/'
$forceArg = if ($Force) { 'force' } else { '' }
# ⭐ 第 4 个参数 = 构建目录（exe 的来源）。⚠️ 从 2026-10-17 起必传 ——
#   在此之前脚本只拷依赖、隐含前提是"目标目录里已有 exe"，往空目录组装时会拷不出一份可跑的东西。
if ($forceArg) {
    & $bash $shArg $OutDir $Msys $forceArg $BuildDir
} else {
    & $bash $shArg $OutDir $Msys '' $BuildDir
}
if ($LASTEXITCODE -ne 0) { throw "pack-deps.sh 退出码 $LASTEXITCODE" }

Write-Host ""
Write-Host "=== 自检（每一项都是作者踩过的）==="
$must = @(
    @{ n = 'wsjtx.exe';               why = '主程序' },
    @{ n = 'jt9.exe';                 why = '解码器，由 wsjtx 拉起' },
    @{ n = 'libsqlite3-0.dll';        why = '漏了它 qsqlite 加载失败 => 卡在 Fatal error（花过 10 轮）' },
    @{ n = 'libfftw3f_threads-3.dll'; why = '漏了它 jt9.exe 弹系统错误框' },
    @{ n = 'platforms\qwindows.dll';  why = 'Qt 平台插件，必需' },
    @{ n = 'sqldrivers\qsqlite.dll';  why = 'SQLite 驱动' }
)
$bad = 0
foreach ($m in $must) {
    $p = Join-Path $OutDir $m.n
    if (Test-Path -LiteralPath $p) {
        Write-Host ("  [OK]   " + $m.n.PadRight(30) + $m.why)
    } else {
        Write-Host ("  [MISS] " + $m.n.PadRight(30) + $m.why) -ForegroundColor Red
        $bad++
    }
}
Write-Host ""
if ($bad -eq 0) {
    Write-Host "✅ 六项必需件齐全 —— 直接跑 $OutDir\wsjtx.exe 应该能出【主窗口】。" -ForegroundColor Green
    Write-Host "   ⚠️ 判据：窗口标题要带版本号（如 'WSJT-X  v3.0.2-devel'）；"
    Write-Host "      只显示光秃秃的 'WSJT-X' 那是 SplashScreen —— 说明后面某步初始化失败了。"
} else {
    Write-Host "❌ 缺 $bad 项 —— 别急着跑，先补上（上面标 MISS 的）。" -ForegroundColor Red
    exit 3
}

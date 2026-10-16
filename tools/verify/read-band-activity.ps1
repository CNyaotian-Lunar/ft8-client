# read-band-activity.ps1 —— 把 WSJT-X「波段活动」栏的内容读出来（不开界面也能验）
#
# ⭐ 为什么要有它：agent 看不到屏幕 ⇒ 以前"界面到底显示了没有"只能让用户瞄一眼。
#    2026-10-17 发现 Qt 的 `DisplayText`（继承 `QTextEdit`）**支持 UI Automation 的 TextPattern**，
#    于是"波段活动栏里现在有哪些行"变成了一个**可以程序化断言**的东西。
#
# ⭐ 用法：
#   .\read-band-activity.ps1                      # 自动找正在跑的 wsjtx
#   .\read-band-activity.ps1 -Pid 12345
#   .\read-band-activity.ps1 -Expect 'CQ N0CALL OM89'   # 断言：期望包含某串（可多次传）
#
# 退出码：0 = 成功（且所有 -Expect 都命中）；3 = 没找到 wsjtx / 没读到内容；4 = 断言未命中
#
# 🩸 关键实现细节（都是踩出来的）：
#   · **`ValuePattern` 读不到 QTextBrowser 的内容** —— 必须用 `TextPattern.DocumentRange.GetText()`
#   · Qt 的主窗口 `ClassName` 就是 `MainWindow`（第二窗口是 `WideGraph`，频谱瀑布图）
#   · ⚠️ `-EncodedCommand` 传 PowerShell 时，**里面的 `$` 要双写**（外层先展开一次）

[CmdletBinding()]
param(
    # ⚠️ 名字不能用 `$Pid` —— 它是 PowerShell 的【只读自动变量】，
    #    参数绑定阶段就会报"无法覆盖变量 Pid，因为它是只读变量或常量"。
    #    （同族禁名：`$home` `$pid` `$host` `$profile` `$error` `$args` …）
    [int]$TargetPid = 0,
    [string[]]$Expect = @(),
    [int]$MaxChars = 8000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $TargetPid) {
    $proc = Get-Process wsjtx -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $proc) { Write-Error "没有正在跑的 wsjtx"; exit 3 }
    $TargetPid = $proc.Id
}
$Pid2 = $TargetPid     # 供下面 here-string 展开用（避免直接用 $TargetPid 里出现 $Pid 前缀歧义）

$script = @"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
`$root = [System.Windows.Automation.AutomationElement]::RootElement
`$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $Pid2)
`$wins = `$root.FindAll([System.Windows.Automation.TreeScope]::Children, `$cond)
`$win = `$null
foreach (`$w in `$wins) { if (`$w.Current.ClassName -eq 'MainWindow') { `$win = `$w; break } }
if (-not `$win) { Write-Output '__NO_MAINWINDOW__'; exit }
`$all = `$win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
`$best = ''
foreach (`$e in `$all) {
    `$tp = `$null
    try { `$tp = `$e.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern) } catch { continue }
    if (-not `$tp) { continue }
    `$txt = ''
    try { `$txt = `$tp.DocumentRange.GetText($MaxChars) } catch { continue }
    # 「波段活动」栏是含解码行最多的那个 Edit；取最长的一份（空文档会被排除）
    if (`$txt.Length -gt `$best.Length) { `$best = `$txt }
}
Write-Output '__BEGIN__'
Write-Output `$best
Write-Output '__END__'
"@
$b64 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
$out = & pwsh -NoProfile -EncodedCommand $b64 2>&1

if ($out -contains '__NO_MAINWINDOW__') { Write-Error "pid $TargetPid 没有 MainWindow（还在 splash？）"; exit 3 }

$joined = ($out -join "`n")
$m = [regex]::Match($joined, '(?s)__BEGIN__\r?\n(.*?)\r?\n__END__')
if (-not $m.Success) { Write-Error "没读到内容（TextPattern 不可用？）"; exit 3 }
$text = $m.Groups[1].Value

Write-Host "=== pid $Pid 的「波段活动」栏（$($text.Length) 字符）==="
foreach ($line in ($text -split "`n")) {
    if ($line.Trim()) { Write-Host ("  | " + $line) }
}

if ($Expect.Count) {
    $bad = 0
    Write-Host ""
    Write-Host "=== 断言 ==="
    foreach ($e in $Expect) {
        if ($text -like "*$e*") { Write-Host ("  [OK]   " + $e) -ForegroundColor Green }
        else { Write-Host ("  [MISS] " + $e) -ForegroundColor Red; $bad++ }
    }
    if ($bad) { exit 4 }
}
exit 0

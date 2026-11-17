# 用 PowerShell 在隔离 profile 中验证插件能被真实 DSH 宿主加载并执行（合成数据）。
# 用法：pwsh tests/dsh-probe/run.ps1   （需要已安装 dsh 0.2.2 与 pnpm）
$ErrorActionPreference = 'Stop'
$root = Resolve-Path "$PSScriptRoot\..\.."
$tmp = Join-Path $env:TEMP ("wechat-one-probe-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $tmp | Out-Null
python -X utf8 -B "$root\tests\python\fixtures.py" "$tmp\decrypted" | Out-Null
$overlay = Join-Path $tmp 'overlay.yml'
@"
- id: dsh-wechat-one-core
  config:
    confirmReads: false
    stateDirectory: '$($tmp -replace '\\','/')/state'
    sourcePath: '$($tmp -replace '\\','/')/decrypted'
"@ | Set-Content -Encoding UTF8 $overlay
$profile = 'wechat-one-dev'
$env:WS_CONFIG = Join-Path $tmp 'no-config.yaml'
python -X utf8 -B "$root\tests\dsh-probe\prebuild.py" "$tmp"
dsh plugin --profile $profile add "$root" | Out-Null
dsh plugin --profile $profile add "$root\tests\dsh-probe" | Out-Null
$log = Join-Path $tmp 'boot.log'
$p = Start-Process dsh.cmd -ArgumentList '--profile', $profile, '--patch', $overlay -NoNewWindow -PassThru `
  -RedirectStandardOutput $log -RedirectStandardError "$log.err"
if (-not $p.WaitForExit(90000)) {
  Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -match $profile } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  try { $p.Kill() } catch { }
}
$out = (Get-Content $log, "$log.err" -Raw -ErrorAction SilentlyContinue) -join "`n"
dsh plugin --profile $profile remove dsh-wechat-one-probe | Out-Null
$line = ($out -split "`n") | Where-Object { $_ -match 'DSH_WECHAT_ONE_PROBE_(OK|FAILED)' }
if ($line) { $line } else { $out.Substring([Math]::Max(0, $out.Length - 3000)) }
Remove-Item -Recurse -Force $tmp
if (-not ($line -match 'PROBE_OK')) { exit 1 }

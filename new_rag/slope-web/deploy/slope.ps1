#Requires -Version 5.1
<#
.SYNOPSIS
    边坡监测智能平台 · 一键启停控制台

.DESCRIPTION
    一条命令管理全部四个服务（Redis / Java / Agent / nginx）。

    .\slope.ps1            交互菜单（双击 启动.bat 也是走这里）
    .\slope.ps1 start      一键启动
    .\slope.ps1 stop       一键停止
    .\slope.ps1 restart    重启
    .\slope.ps1 status     只看状态

.NOTES
    * 本文件必须保存为 **UTF-8 with BOM**。
      这台机器上是 Windows PowerShell 5.1，它读无 BOM 的 UTF-8 会按 GBK 解释，
      中文变乱码后**连字符串的结尾引号都会被吃掉**，直接语法报错。
    * 全程兼容 PowerShell 5.1：不使用 ??、?.、三元运算符等 7.0+ 语法。
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('menu', 'start', 'stop', 'restart', 'status')]
    [string]$Action = 'menu'
)

$ErrorActionPreference = 'Continue'

# ══════════════════════════════════════════════════════════════════
#  配置（默认自动探测，探测不到时可在下面手工写死）
# ══════════════════════════════════════════════════════════════════

$DeployDir = $PSScriptRoot
$WebDir    = Split-Path -Parent $DeployDir          # slope-web
$Root      = Split-Path -Parent $WebDir             # new_rag
$LogDir    = Join-Path $Root 'logs'

$NginxConf = Join-Path $DeployDir 'nginx.conf'
$JarPath   = Join-Path $Root 'slope-platform\target\slope-platform-1.0.0.jar'

# 允许用环境变量覆盖，方便换机器/换版本
$NginxHome = $env:SLOPE_NGINX_HOME
$RedisHome = $env:SLOPE_REDIS_HOME

$Ports = @{ Redis = 6379; Java = 8080; Agent = 8000; Nginx = 80 }

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# ══════════════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════════════

function Write-Title($text) {
    Write-Host ''
    Write-Host ('═' * 58) -ForegroundColor DarkCyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('═' * 58) -ForegroundColor DarkCyan
}

function Write-Step($text) { Write-Host "  → $text" -ForegroundColor Gray }
function Write-Ok($text)   { Write-Host "  ✅ $text" -ForegroundColor Green }
function Write-Warn2($text){ Write-Host "  ⚠️  $text" -ForegroundColor Yellow }
function Write-Bad($text)  { Write-Host "  ❌ $text" -ForegroundColor Red }

<#
.SYNOPSIS 用 TcpClient 探端口。比 Get-NetTCPConnection 快，且不需要管理员权限。
#>
function Test-PortOpen {
    param([int]$Port, [string]$TargetHost = '127.0.0.1', [int]$TimeoutMs = 800)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($TargetHost, $Port, $null, $null)
        $done = $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if ($done -and $client.Connected) { $client.EndConnect($iar); return $true }
        return $false
    } catch {
        return $false
    } finally {
        try { $client.Close() } catch { }
    }
}

function Wait-HttpOk {
    param([string]$Url, [int]$TimeoutSec = 120, [string]$Name = '服务')
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 2
    }
    return $false
}

<#
.SYNOPSIS 在常见位置搜索工具目录（不写死盘符）。
#>
function Find-ToolHome {
    param([string]$ExeName, [string]$NamePattern, [string[]]$ExtraPaths)
    foreach ($p in $ExtraPaths) {
        if ($p -and (Test-Path (Join-Path $p $ExeName))) { return $p }
    }
    $cmd = Get-Command $ExeName -ErrorAction SilentlyContinue
    if ($cmd) { return (Split-Path -Parent $cmd.Source) }
    foreach ($drive in (Get-PSDrive -PSProvider FileSystem).Root) {
        $hit = Get-ChildItem -Path $drive -Directory -Filter $NamePattern `
                   -Recurse -Depth 3 -ErrorAction SilentlyContinue |
               Where-Object { Test-Path (Join-Path $_.FullName $ExeName) } |
               Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Get-ServiceState {
    <# 返回 4 个服务的当前状态（只看端口，不依赖进程名） #>
    $s = [ordered]@{}
    $s['Redis'] = Test-PortOpen -Port $Ports.Redis
    $s['Java']  = Test-PortOpen -Port $Ports.Java
    $s['Agent'] = Test-PortOpen -Port $Ports.Agent
    $s['Nginx'] = Test-PortOpen -Port $Ports.Nginx
    return $s
}

# ══════════════════════════════════════════════════════════════════
#  状态
# ══════════════════════════════════════════════════════════════════

function Show-Status {
    Write-Title '服务状态'
    $st = Get-ServiceState
    <#
      排版说明：不要用 "{0,-24}" 去对齐含中文的名字。
      PowerShell 的格式对齐是按**字符数**算的，而中文在终端占两个字符宽，
      于是列必然错位。这里把 ASCII 名字放前面（对齐交给它），
      中文说明放到最后（不参与对齐）。
    #>
    $rows = @(
        @{ N = 'Redis'; P = $Ports.Redis; K = 'Redis'; D = '会话持久化' },
        @{ N = 'Java '; P = $Ports.Java;  K = 'Java';  D = '业务后端'   },
        @{ N = 'Agent'; P = $Ports.Agent; K = 'Agent'; D = 'Python Agent' },
        @{ N = 'nginx'; P = $Ports.Nginx; K = 'Nginx'; D = '前端入口'   }
    )
    foreach ($r in $rows) {
        $up = $st[$r.K]
        $mark  = if ($up) { '运行中' } else { '已停止' }
        $color = if ($up) { 'Green' } else { 'DarkGray' }
        Write-Host ("    {0}  :{1,-6} {2}   " -f $r.N, $r.P, $mark) -NoNewline -ForegroundColor $color
        Write-Host $r.D -ForegroundColor DarkGray
    }

    # Agent 就绪细节：RAG 有没有真的加载到实例上
    if ($st['Agent']) {
        try {
            $h = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 5
            $c2 = if ($h.status -eq 'ok') { 'Green' } else { 'Yellow' }
            Write-Host '    Agent: ' -NoNewline -ForegroundColor DarkGray
            Write-Host ("status={0}  rag_ready={1}  rag_on_agent={2}" -f $h.status, $h.rag_ready, $h.rag_on_agent) -ForegroundColor $c2
        } catch { }
    }

    Write-Host ''
    if ($st['Nginx']) {
        Write-Host '    访问入口：' -NoNewline -ForegroundColor Gray
        Write-Host 'http://127.0.0.1/' -ForegroundColor White
        Write-Host '    登录账号：' -NoNewline -ForegroundColor Gray
        Write-Host 'admin / admin123' -ForegroundColor White
    }
}

# ══════════════════════════════════════════════════════════════════
#  启动
# ══════════════════════════════════════════════════════════════════

function Start-Redis {
    if (Test-PortOpen -Port $Ports.Redis) { Write-Ok 'Redis 已在运行'; return $true }
    if (-not $RedisHome) {
        $RedisHome = Find-ToolHome -ExeName 'redis-server.exe' `
            -NamePattern 'Redis*' `
            -ExtraPaths @('D:\develop_software\Redis-x64-3.2.100', 'C:\Redis', 'C:\Program Files\Redis')
    }
    if (-not $RedisHome) {
        Write-Warn2 '找不到 redis-server.exe，跳过'
        Write-Step '会话将回落进程内内存：重启会丢对话上下文（功能仍可用）'
        return $false
    }
    Start-Process -FilePath (Join-Path $RedisHome 'redis-server.exe') `
        -WorkingDirectory $RedisHome -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'redis.out.log') `
        -RedirectStandardError  (Join-Path $LogDir 'redis.err.log')
    for ($i = 0; $i -lt 15; $i++) {
        if (Test-PortOpen -Port $Ports.Redis) {
            $ver = ''
            $cli = Join-Path $RedisHome 'redis-cli.exe'
            if (Test-Path $cli) {
                $ver = (& $cli info server 2>$null | Select-String 'redis_version') -replace '\s', ''
            }
            Write-Ok "Redis 已启动 $ver"
            return $true
        }
        Start-Sleep -Milliseconds 700
    }
    Write-Warn2 'Redis 启动超时'
    return $false
}

function Start-Java {
    if (Test-PortOpen -Port $Ports.Java) { Write-Ok 'Java 已在运行'; return $true }
    if (-not (Test-Path $JarPath)) {
        Write-Bad "找不到 jar：$JarPath"
        Write-Step '请先在 slope-platform 目录执行：mvn -q -DskipTests package'
        return $false
    }
    Start-Process -FilePath 'java' -ArgumentList '-jar', "`"$JarPath`"" -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'java.out.log') `
        -RedirectStandardError  (Join-Path $LogDir 'java.err.log')
    if (Wait-HttpOk -Url 'http://127.0.0.1:8080/api/health' -TimeoutSec 120 -Name 'Java') {
        Write-Ok 'Java 已就绪 (8080)'
        return $true
    }
    Write-Bad 'Java 启动超时，看 logs\java.err.log'
    return $false
}

function Start-Agent {
    if (Test-PortOpen -Port $Ports.Agent) { Write-Ok 'Agent 已在运行'; return $true }
    $env:SLOPE_HEADLESS = '1'
    Start-Process -FilePath 'python' -ArgumentList 'end_new.py', '--api' `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'agent.out.log') `
        -RedirectStandardError  (Join-Path $LogDir 'agent.err.log')

    <#
      Agent 的就绪分两段：进程起来（starting）→ RAG 加载完（ok）。
      必须等到 ok，否则前端一打开问到知识类问题就会得到"知识库未加载"。
      加载嵌入模型 + FAISS 通常 15~25 秒。
    #>
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $last = ''
    while ($sw.Elapsed.TotalSeconds -lt 180) {
        try {
            $h = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 5
            if ($h.status -eq 'ok') {
                Write-Ok "Agent 已就绪，RAG 已加载（耗时 $([int]$sw.Elapsed.TotalSeconds)s）"
                return $true
            }
            if ($h.status -eq 'degraded') {
                Write-Bad 'Agent 的 RAG 加载失败，看 logs\agent.err.log'
                return $false
            }
            if ($h.status -ne $last) {
                $last = $h.status
                Write-Step "Agent 预热中…（$($h.status) / rag_state=$($h.rag_state)）"
            }
        } catch { }
        Start-Sleep -Seconds 3
    }
    Write-Warn2 'Agent 预热超时，但进程可能仍在跑（看 /health）'
    return $false
}

function Start-Nginx {
    if (-not $NginxHome) {
        $NginxHome = Find-ToolHome -ExeName 'nginx.exe' -NamePattern 'nginx*' `
            -ExtraPaths @('D:\develop_software\nginx-1.20.2', 'C:\nginx', 'C:\Program Files\nginx')
    }
    if (-not $NginxHome) {
        Write-Bad '找不到 nginx.exe。可设环境变量 SLOPE_NGINX_HOME 指定目录'
        return $false
    }
    $exe = Join-Path $NginxHome 'nginx.exe'

    # 先做语法检查，配置写错时给出明确报错而不是静默失败
    $test = & $exe -p "$NginxHome" -c "$NginxConf" -t 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Bad 'nginx 配置检查未通过：'
        $test | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkRed }
        return $false
    }

    if (Test-PortOpen -Port $Ports.Nginx) {
        & $exe -p "$NginxHome" -c "$NginxConf" -s reload 2>$null
        Write-Ok 'nginx 已重载配置 (80)'
        return $true
    }
    Start-Process -FilePath $exe -ArgumentList '-p', "$NginxHome", '-c', "$NginxConf" `
        -WorkingDirectory "$NginxHome" -WindowStyle Hidden
    Start-Sleep -Seconds 3
    if (Test-PortOpen -Port $Ports.Nginx) { Write-Ok 'nginx 已启动 (80)'; return $true }
    Write-Bad 'nginx 启动失败'
    return $false
}

function Invoke-Start {
    Write-Title '启动边坡监测智能平台'
    # 顺序有依赖：Agent 要连 Redis 与 MySQL；nginx 要能反代到两个后端。
    # 所以严格按 Redis → Java → Agent → nginx，且每步等就绪再继续。
    Start-Redis | Out-Null
    Start-Java  | Out-Null
    Start-Agent | Out-Null
    Start-Nginx | Out-Null
    Write-Host ''
    Show-Status
}

# ══════════════════════════════════════════════════════════════════
#  停止
# ══════════════════════════════════════════════════════════════════

function Stop-Nginx {
    if ($NginxHome -and (Test-Path (Join-Path $NginxHome 'nginx.exe'))) {
        & (Join-Path $NginxHome 'nginx.exe') -p "$NginxHome" -c "$NginxConf" -s stop 2>$null
        Start-Sleep -Seconds 2
    }
    Get-Process nginx -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    if (Test-PortOpen -Port $Ports.Nginx) { Write-Warn2 'nginx 仍在监听 80' } else { Write-Ok 'nginx 已停止' }
}

function Stop-Agent {
    # 只杀命令行里带 end_new.py 的 python，避免误伤用户其它 python 脚本
    $n = 0
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*end_new.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }
    Start-Sleep -Seconds 1
    Write-Ok "Agent 已停止（$n 个进程）"
}

function Stop-Java {
    $n = 0
    Get-CimInstance Win32_Process -Filter "Name='java.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*slope-platform*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }
    Start-Sleep -Seconds 1
    Write-Ok "Java 已停止（$n 个进程）"
}

function Stop-Redis {
    if (-not $RedisHome) {
        $RedisHome = Find-ToolHome -ExeName 'redis-server.exe' -NamePattern 'Redis*' `
            -ExtraPaths @('D:\develop_software\Redis-x64-3.2.100')
    }
    if (-not $RedisHome) { Write-Warn2 '找不到 Redis 目录，跳过'; return }
    $cli = Join-Path $RedisHome 'redis-cli.exe'
    if (Test-Path $cli) {
        # SHUTDOWN NOSAVE 让 Redis 自己干净退出；强杀可能留下半写的 dump.rdb
        & $cli shutdown nosave 2>$null
        Start-Sleep -Seconds 2
    }
    if (Test-PortOpen -Port $Ports.Redis) {
        Get-Process redis-server -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    if (Test-PortOpen -Port $Ports.Redis) { Write-Warn2 'Redis 仍在监听 6379' }
    else { Write-Ok 'Redis 已停止（会话数据保留在 dump.rdb）' }
}

function Invoke-Stop {
    Write-Title '停止边坡监测智能平台'
    # 顺序与启动相反：先断入口，再停后端，避免出现"能访问但后端已没"的空窗
    Stop-Nginx
    Stop-Agent
    Stop-Java
    Stop-Redis
    Write-Host ''
    Show-Status
}

# ══════════════════════════════════════════════════════════════════
#  交互菜单
# ══════════════════════════════════════════════════════════════════

function Show-Menu {
    while ($true) {
        Write-Host ''
        Write-Host ('═' * 58) -ForegroundColor DarkCyan
        Write-Host '   边坡监测智能平台 · 控制台' -ForegroundColor Cyan
        Write-Host ('═' * 58) -ForegroundColor DarkCyan
        $st = Get-ServiceState
        $running = ($st.Values | Where-Object { $_ }).Count
        Write-Host "   当前状态：$running / 4 个服务运行中" -ForegroundColor $(if ($running -eq 4) { 'Green' } elseif ($running -eq 0) { 'DarkGray' } else { 'Yellow' })
        Write-Host ''
        Write-Host '   [1] 启动全部服务' -ForegroundColor White
        Write-Host '   [2] 停止全部服务' -ForegroundColor White
        Write-Host '   [3] 重启' -ForegroundColor White
        Write-Host '   [4] 查看状态' -ForegroundColor White
        Write-Host '   [5] 打开前端页面' -ForegroundColor White
        Write-Host '   [0] 退出' -ForegroundColor DarkGray
        Write-Host ''
        $c = Read-Host '   请选择'
        switch ($c) {
            '1' { Invoke-Start }
            '2' { Invoke-Stop }
            '3' { Invoke-Stop; Start-Sleep -Seconds 2; Invoke-Start }
            '4' { Show-Status }
            '5' { Start-Process 'http://127.0.0.1/'; Write-Ok '已在浏览器打开 http://127.0.0.1/' }
            '0' { return }
            default { Write-Warn2 '请输入 0-5' }
        }
    }
}

# ══════════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════════

switch ($Action) {
    'start'   { Invoke-Start }
    'stop'    { Invoke-Stop }
    'restart' { Invoke-Stop; Start-Sleep -Seconds 2; Invoke-Start }
    'status'  { Show-Status }
    default   { Show-Menu }
}

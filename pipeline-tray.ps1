#requires -Version 7.0
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ObsidianDir = "D:\Main\OpenCode\Obsidian\AI-Chats"
$PipeScript  = Join-Path $ProjectRoot "pipeline.py"

$pipeProcess = $null

# ─── Иконки (зелёный / серый круг 16×16) ────────────────────────────────────
function Make-CircleIcon($color) {
    $bmp = New-Object System.Drawing.Bitmap(16, 16)
    $g   = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $brush = New-Object System.Drawing.SolidBrush($color)
    $g.FillEllipse($brush, 2, 2, 12, 12)
    $g.Dispose(); $brush.Dispose()
    $hIcon = $bmp.GetHicon()
    $bmp.Dispose()
    return [System.Drawing.Icon]::FromHandle($hIcon)
}

$iconGreen = Make-CircleIcon ([System.Drawing.Color]::FromArgb(0, 200, 83))
$iconGray  = Make-CircleIcon ([System.Drawing.Color]::FromArgb(150, 150, 150))

# ─── Управление pipeline ─────────────────────────────────────────────────────
function Start-Pipeline {
    if ($script:pipeProcess -and !$script:pipeProcess.HasExited) { return }
    try {
        $psi = [System.Diagnostics.ProcessStartInfo]@{
            FileName         = "pwsh.exe"
            Arguments        = "-WindowStyle Hidden -NoLogo -NoProfile -Command python `"$PipeScript`" --serve"
            WorkingDirectory = $ProjectRoot
            UseShellExecute  = $false
            CreateNoWindow   = $true
        }
        $script:pipeProcess = [System.Diagnostics.Process]::Start($psi)
        $script:notifyIcon.Icon = $iconGreen
        $script:notifyIcon.Text = "AI Pipeline — запущен"
    } catch {
        $script:notifyIcon.Icon = $iconGray
        $script:notifyIcon.Text = "AI Pipeline — ошибка запуска"
        $script:pipeProcess = $null
    }
}

function Stop-Pipeline {
    if ($script:pipeProcess -and !$script:pipeProcess.HasExited) {
        try { $script:pipeProcess.Kill($true) } catch {}
        $script:pipeProcess.WaitForExit(2000)
        $script:pipeProcess.Dispose()
        $script:pipeProcess = $null
    }
    # Дополнительно: убить осиротевшие python процессы, которые слушают наш порт
    $listener = Get-NetTCPConnection -LocalPort 18888 -ErrorAction SilentlyContinue
    if ($listener) {
        $listener.OwningProcess | ForEach-Object {
            $p = Get-Process -Id $_ -ErrorAction SilentlyContinue
            if ($p) { try { $p.Kill($true) } catch {} }
        }
    }
    $script:notifyIcon.Icon = $iconGray
    $script:notifyIcon.Text = "AI Pipeline — остановлен"
}

# ─── Health check (каждые 3 сек) ─────────────────────────────────────────────
$healthTimer = New-Object System.Windows.Forms.Timer
$healthTimer.Interval = 3000
$healthTimer.Add_Tick({
    if ($script:pipeProcess -and $script:pipeProcess.HasExited) {
        $script:notifyIcon.Icon = $iconGray
        $script:notifyIcon.Text = "AI Pipeline — остановлен"
        $script:pipeProcess = $null
    }
})
$healthTimer.Start()

# ─── Контекстное меню (перестраивается при каждом открытии) ──────────────────
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$menu.Add_Opening({
    while ($menu.Items.Count -gt 0) { $menu.Items.RemoveAt(0) }
    $running = $script:pipeProcess -and !$script:pipeProcess.HasExited

    $status = New-Object System.Windows.Forms.ToolStripMenuItem
    $status.Text = "Pipeline: $(if ($running) { '✔ Запущен' } else { '✖ Остановлен' })"
    $status.Enabled = $false
    $menu.Items.Add($status) | Out-Null
    $menu.Items.Add("-") | Out-Null

    $start = New-Object System.Windows.Forms.ToolStripMenuItem("▶ Запустить")
    $start.Add_Click({ Start-Pipeline })
    $menu.Items.Add($start) | Out-Null

    $stop = New-Object System.Windows.Forms.ToolStripMenuItem("⏹ Остановить")
    $stop.Add_Click({ Stop-Pipeline })
    $menu.Items.Add($stop) | Out-Null

    $menu.Items.Add("-") | Out-Null

    $openObs = New-Object System.Windows.Forms.ToolStripMenuItem("📂 Открыть папку Obsidian")
    $openObs.Add_Click({ Start-Process $ObsidianDir })
    $menu.Items.Add($openObs) | Out-Null

    $openProj = New-Object System.Windows.Forms.ToolStripMenuItem("📁 Папка проекта")
    $openProj.Add_Click({ Start-Process $ProjectRoot })
    $menu.Items.Add($openProj) | Out-Null

    $menu.Items.Add("-") | Out-Null

    $exit = New-Object System.Windows.Forms.ToolStripMenuItem("✕ Выход")
    $exit.Add_Click({
        $healthTimer.Stop()
        Stop-Pipeline
        $notifyIcon.Visible = $false
        $notifyIcon.Dispose()
        $iconGreen.Dispose()
        $iconGray.Dispose()
        [System.Windows.Forms.Application]::Exit()
    })
    $menu.Items.Add($exit) | Out-Null
})

# ─── Уведомление в трее ──────────────────────────────────────────────────────
$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Icon = $iconGray
$notifyIcon.Text = "AI Pipeline — остановлен"
$notifyIcon.ContextMenuStrip = $menu
$notifyIcon.Visible = $true
$notifyIcon.Add_Click({
    if ($_.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        Start-Process $ObsidianDir
    }
})

# ─── Старт ───────────────────────────────────────────────────────────────────
Start-Pipeline
[System.Windows.Forms.Application]::Run()

<#
  LAN Messenger Server - background service control.

  Runs LANMessengerServer.exe --headless at Windows startup as SYSTEM (no user needs to be logged on),
  restarts it automatically if it stops unexpectedly, never times out.

  Usage (as administrator):
    powershell -ExecutionPolicy Bypass -File service.ps1 install     # create + start
    powershell -ExecutionPolicy Bypass -File service.ps1 uninstall   # stop + remove
    powershell -ExecutionPolicy Bypass -File service.ps1 start | stop | restart | status
#>
param(
    [ValidateSet("install", "uninstall", "start", "stop", "restart", "status")]
    [string]$Action = "status"
)
$ErrorActionPreference = "Stop"
$TaskName = "LAN Messenger Server"
$Exe = Join-Path $PSScriptRoot "LANMessengerServer.exe"

function Stop-Server {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    # the headless server process (the console window, if open, is left alone)
    Get-CimInstance Win32_Process -Filter "Name='LANMessengerServer.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*--headless*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
}

switch ($Action) {
    "install" {
        $action = New-ScheduledTaskAction -Execute $Exe -Argument "--headless" -WorkingDirectory $PSScriptRoot
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
            -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
        Stop-Server
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
            -Settings $settings -Description "LAN Messenger Server running in the background (no login needed)." `
            -Force | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        # wait until the server holds its mutex: the console opened right after setup must find it running
        # (otherwise it would try to start a second server itself)
        $m = $null
        for ($i = 0; $i -lt 60; $i++) {
            try {
                if ([System.Threading.Mutex]::TryOpenExisting("Global\LANMessengerServerMutex", [ref]$m)) { $m.Dispose(); break }
            } catch { break }      # exists but belongs to SYSTEM: it is running
            Start-Sleep -Milliseconds 500
        }
        Write-Output "Service installed and started."
    }
    "uninstall" {
        Stop-Server
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Output "Service removed."
    }
    "start"   { Start-ScheduledTask -TaskName $TaskName; Write-Output "Started." }
    "stop"    { Stop-Server; Write-Output "Stopped." }
    "restart" { Stop-Server; Start-ScheduledTask -TaskName $TaskName; Write-Output "Restarted." }
    "status" {
        $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($t) { Write-Output "Service: $($t.State)" } else { Write-Output "Service: not installed" }
    }
}

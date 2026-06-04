$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
$script = Join-Path $projectRoot "scripts\run.bat"
$startup = [Environment]::GetFolderPath("Startup")
$target = Join-Path $startup "UpSteeper.lnk"

$ws = New-Object -ComObject WScript.Shell
$link = $ws.CreateShortcut($target)
$link.TargetPath = $script
$link.WorkingDirectory = $projectRoot
$link.Save()

Write-Host "Startup shortcut created: $target"

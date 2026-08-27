$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'GOFO站点工具包.lnk'
$target = Join-Path $dir '启动器.bat'
if (-not (Test-Path $target)) { Write-Host '未找到 启动器.bat'; exit 1 }
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $target
$s.WorkingDirectory = $dir
$s.IconLocation = (Join-Path $dir 'app.ico') + ',0'
$s.WindowStyle = 1
$s.Description = 'GOFO 站点工具包'
$s.Save()
Write-Host ('已在桌面创建快捷方式: ' + $lnk)

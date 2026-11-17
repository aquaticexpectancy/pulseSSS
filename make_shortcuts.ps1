# Creates "pulseSSS" shortcuts on the Desktop and in the Start menu.
# Run again after moving the pulseSSS folder:  powershell -ExecutionPolicy Bypass -File make_shortcuts.ps1
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (& python -c "import sys; print(sys.executable)").Trim()
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }
$icon = Join-Path $root 'assets\pulsesss.ico'
if (-not (Test-Path $icon)) { & python -c "import sys; sys.path.insert(0, r'$root'); import tray; tray.write_icon_file()" }

$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir 'pulseSSS.lnk'))
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = '"' + (Join-Path $root 'pulseSSS.pyw') + '"'
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = $icon
    $lnk.Description = 'pulseSSS - live heart rate for OBS'
    $lnk.Save()
    Write-Output "Created $($lnk.FullName)"
}

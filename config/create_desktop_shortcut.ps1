$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$MainScript = Join-Path $ProjectRoot 'main.py'
$Pythonw = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
$IconPath = Join-Path $ProjectRoot 'assets\Brahma_Lite_Logo.ico'

if (-not (Test-Path -LiteralPath $MainScript)) {
    throw "Brahma main.py was not found under project root: $ProjectRoot"
}

if (-not (Test-Path -LiteralPath $Pythonw)) {
    $PythonwCommand = Get-Command 'pythonw.exe' -ErrorAction SilentlyContinue
    if ($PythonwCommand) {
        $Pythonw = $PythonwCommand.Source
    } else {
        throw "pythonw.exe was not found. Create the project .venv or add pythonw.exe to PATH."
    }
}

$Desktop = [Environment]::GetFolderPath('DesktopDirectory')
if ([string]::IsNullOrWhiteSpace($Desktop)) {
    $Desktop = Join-Path $HOME 'Desktop'
}
New-Item -ItemType Directory -Path $Desktop -Force | Out-Null

$ShortcutPath = Join-Path $Desktop 'Brahma Evo.lnk'
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Pythonw
$Shortcut.Arguments = '"' + $MainScript + '"'
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.WindowStyle = 7
$Shortcut.Description = 'Launch Brahma Evo'
if (Test-Path -LiteralPath $IconPath) {
    $Shortcut.IconLocation = "$IconPath,0"
}
$Shortcut.Save()
Write-Output "Created Brahma Evo shortcut: $ShortcutPath"

# build.ps1
# Builds the standalone Windows executable into dist\ with PyInstaller.
# Needs `pip install . pyinstaller` first (a regular install, not an editable one).

$ErrorActionPreference = "Stop"
$versionStr = (Select-String -Path ".\src\__init__.py" -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$Env:CURRENT_VERSION = $versionStr
& python -m PyInstaller --onefile --windowed --clean --noupx --icon ".\src\icons\photosheet.ico" `
    --collect-data photosheet .github/launcher.py -n "photosheet-v$versionStr"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

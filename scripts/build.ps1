# Builds RockCrawler into dist\RockCrawler (PyInstaller, one folder) and runs its self-test.
# The release workflow uses this too, so a local build matches what members install.
#
#   .\scripts\build.ps1                     # build + self-test
#   .\scripts\build.ps1 -Pack -Version 1.0.1 # also make a local Setup.exe in .\Releases (needs vpk)
param(
    [string]$Version = "0.0.0-local",
    [switch]$Pack
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot)

python -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name RockCrawler --icon assets/asop.ico `
    --add-data "assets;assets" `
    --collect-all winrt --collect-all velopack `
    rockcrawler.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }

# Launch the built exe in self-test mode: it checks OCR, Velopack, fonts and Tk made it in.
$out = Join-Path (Get-Location) 'selftest.txt'
Remove-Item $out -ErrorAction SilentlyContinue
$p = Start-Process -FilePath 'dist\RockCrawler\RockCrawler.exe' -ArgumentList '--selftest', $out -Wait -PassThru
if (Test-Path $out) { Get-Content $out } else { Write-Host 'No self-test output' }
if ($p.ExitCode -ne 0 -or -not (Test-Path $out)) { throw "Self-test failed (exit $($p.ExitCode))" }

if ($Pack) {
    vpk pack --packId ASOP.RockCrawler --packVersion $Version --packDir dist\RockCrawler `
        --mainExe RockCrawler.exe --packTitle "ASOP RockCrawler" --packAuthors "ASOP" --icon assets\asop.ico
    if ($LASTEXITCODE) { throw "vpk pack failed" }
    Write-Host "Setup: $(Resolve-Path Releases)\ASOP.RockCrawler-win-Setup.exe"
}

param(
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$originalPath = $env:Path
$originalCache = $env:PYINSTALLER_CONFIG_DIR
Push-Location $root
try {
    $pythonExe = (Get-Command $Python).Source
    $buildRoot = Join-Path $root "build/media_probe"
    $fixtures = Join-Path $buildRoot "fixtures"
    $distRoot = Join-Path $root "dist"
    $bundle = Join-Path $distRoot "MediaProbe"
    & $pythonExe tools/media_probe/generate_fixtures.py $fixtures
    if ($LASTEXITCODE) { throw "Media fixture generation failed ($LASTEXITCODE)." }

    # Do not let DLLs from unrelated programs on PATH shadow Windows DLLs.
    $env:Path = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $buildRoot "pyinstaller-cache"
    & $pythonExe -m PyInstaller --clean --noconfirm --windowed --onedir --name MediaProbe `
        --specpath $buildRoot --workpath $buildRoot --distpath $distRoot tools/media_probe/probe.py
    if ($LASTEXITCODE) { throw "PyInstaller failed ($LASTEXITCODE)." }
    Copy-Item -LiteralPath $fixtures -Destination (Join-Path $bundle "media") -Recurse

    # The lock file follows offline acceptance. Keep the exact candidate versions
    # and packaged binary inventory next to the build for the verification record.
    @(& $pythonExe --version; & $pythonExe -m pip freeze --all) |
        Set-Content -Encoding utf8 (Join-Path $buildRoot "versions.txt")
    Get-ChildItem -LiteralPath $bundle -Recurse -File |
        Select-Object @{Name="Path";Expression={$_.FullName.Substring($bundle.Length + 1)}}, Length |
        Export-Csv -NoTypeInformation -Encoding utf8 (Join-Path $buildRoot "bundle.csv")
}
finally {
    $env:Path = $originalPath
    $env:PYINSTALLER_CONFIG_DIR = $originalCache
    Pop-Location
}

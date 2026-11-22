param(
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$originalPath = $env:Path
$originalCache = $env:PYINSTALLER_CONFIG_DIR
Push-Location $root
try {
    $pythonExe = (Get-Command $Python).Source
    $buildRoot = Join-Path $root "build/player"
    $env:Path = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $buildRoot "pyinstaller-cache"
    $arguments = @(
        "-m", "PyInstaller", "--clean", "--noconfirm", "--windowed", "--onedir",
        "--name", "GalGamePlayer", "--specpath", $buildRoot,
        "--workpath", $buildRoot, "--distpath", (Join-Path $root "dist"),
        "player_main.py"
    )
    & $pythonExe @arguments
    if ($LASTEXITCODE) { throw "Player build failed ($LASTEXITCODE)." }
}
finally {
    $env:Path = $originalPath
    $env:PYINSTALLER_CONFIG_DIR = $originalCache
    Pop-Location
}

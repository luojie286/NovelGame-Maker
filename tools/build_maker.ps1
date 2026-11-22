param(
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$originalPath = $env:Path
$originalCache = $env:PYINSTALLER_CONFIG_DIR
Push-Location $root
try {
    & (Join-Path $PSScriptRoot "build_player.ps1") -Python $Python
    $pythonExe = (Get-Command $Python).Source
    $buildRoot = Join-Path $root "build/maker"
    $playerBundle = (Resolve-Path "dist/GalGamePlayer").Path
    $env:Path = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $buildRoot "pyinstaller-cache"
    $arguments = @(
        "-m", "PyInstaller", "--clean", "--noconfirm", "--windowed", "--onedir",
        "--name", "GalGameMaker", "--specpath", $buildRoot,
        "--workpath", $buildRoot, "--distpath", (Join-Path $root "dist"),
        "--collect-binaries", "z3",
        "--add-data", "${playerBundle}:player_bundle",
        "maker_main.py"
    )
    & $pythonExe @arguments
    if ($LASTEXITCODE) { throw "Maker build failed ($LASTEXITCODE)." }
}
finally {
    $env:Path = $originalPath
    $env:PYINSTALLER_CONFIG_DIR = $originalCache
    Pop-Location
}

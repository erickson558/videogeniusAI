$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$entryPoint = Join-Path $projectRoot "videogeniusAI.pyw"
$iconPath = Join-Path $projectRoot "videogeniusai.ico"
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

if (-not (Test-Path $entryPoint)) {
    throw "Entry point not found: $entryPoint"
}

if (-not (Test-Path $iconPath)) {
    throw "Icon not found: $iconPath"
}

Push-Location $projectRoot
try {
    & $pythonExe -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name "videogeniusAI" `
        --icon $iconPath `
        --distpath $projectRoot `
        --workpath (Join-Path $projectRoot "build") `
        --specpath $projectRoot `
        $entryPoint
}
finally {
    Pop-Location
}


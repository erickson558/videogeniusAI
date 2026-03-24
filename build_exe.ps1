$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$entryPoint = Join-Path $projectRoot "videogeniusAI.pyw"
$iconPath = Join-Path $projectRoot "videogeniusai.ico"
$localesPath = Join-Path $projectRoot "videogenius_ai\locales"
$versionPath = Join-Path $projectRoot "videogenius_ai\version.py"
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

if (-not (Test-Path $localesPath)) {
    throw "Locales folder not found: $localesPath"
}

if (-not (Test-Path $versionPath)) {
    throw "Version file not found: $versionPath"
}

$versionSource = Get-Content -Path $versionPath -Raw
if ($versionSource -notmatch 'APP_VERSION = "(\d+\.\d+\.\d+)"') {
    throw "APP_VERSION not found in $versionPath"
}

$appVersion = $Matches[1]
$versionParts = $appVersion.Split(".")
$versionInfoPath = Join-Path $env:TEMP "videogeniusAI_version_info.txt"
$safeVersion = $appVersion -replace '[^0-9\.]', '_'
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$workPath = Join-Path $env:TEMP "videogeniusAI_build_${safeVersion}_$timestamp"
$versionInfo = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($($versionParts[0]), $($versionParts[1]), $($versionParts[2]), 0),
    prodvers=($($versionParts[0]), $($versionParts[1]), $($versionParts[2]), 0),
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', 'Synyster Rick'),
            StringStruct('FileDescription', 'VideoGeniusAI desktop application'),
            StringStruct('FileVersion', '$appVersion'),
            StringStruct('InternalName', 'videogeniusAI'),
            StringStruct('LegalCopyright', 'Apache License 2.0'),
            StringStruct('OriginalFilename', 'videogeniusAI.exe'),
            StringStruct('ProductName', 'VideoGeniusAI'),
            StringStruct('ProductVersion', '$appVersion')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
Set-Content -Path $versionInfoPath -Value $versionInfo -Encoding UTF8

Push-Location $projectRoot
try {
    & $pythonExe -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name "videogeniusAI" `
        --icon $iconPath `
        --add-data "$localesPath;videogenius_ai\locales" `
        --version-file $versionInfoPath `
        --distpath $projectRoot `
        --workpath $workPath `
        --specpath $projectRoot `
        $entryPoint
}
finally {
    Pop-Location
    Remove-Item -Path $versionInfoPath -ErrorAction SilentlyContinue
    Remove-Item -Path $workPath -Recurse -Force -ErrorAction SilentlyContinue
}

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    throw "No se encontro .venv\\Scripts\\python.exe. Crea la virtualenv e instala requirements antes de compilar."
}

# Validar que el ejecutable se compile con el mismo entorno que usa la app.
& $venvPython -c "import customtkinter, PIL, requests, PyInstaller"

# Limpiar builds previos
if (Test-Path .\build) { Remove-Item .\build -Recurse -Force }
if (Test-Path .\dist) { Remove-Item .\dist -Recurse -Force }

# Ejecutar PyInstaller usando la virtualenv del proyecto.
& $venvPython -m PyInstaller --noconfirm --clean .\videogeniusAI.spec

# Mover el ejecutable a la raíz del proyecto
if (-not (Test-Path .\dist\videogeniusAI.exe)) {
    throw "PyInstaller no genero dist\\videogeniusAI.exe."
}

Move-Item .\dist\videogeniusAI.exe .\videogeniusAI.exe -Force

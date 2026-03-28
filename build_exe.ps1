# build_exe.ps1
# Script de ejemplo para compilar VideoGeniusAI usando PyInstaller
# Asegúrate de personalizar los paths y opciones según tu proyecto

$ErrorActionPreference = 'Stop'

# Limpiar builds previos
if (Test-Path .\build) { Remove-Item .\build -Recurse -Force }
if (Test-Path .\dist) { Remove-Item .\dist -Recurse -Force }
if (Test-Path .\videogeniusAI.exe) { Remove-Item .\videogeniusAI.exe -Force }

# Ejecutar PyInstaller
pyinstaller --noconfirm --onefile --windowed videogeniusAI.pyw --name videogeniusAI --icon videogeniusai.ico

# Mover el ejecutable a la raíz del proyecto
if (Test-Path .\dist\videogeniusAI.exe) {
    Move-Item .\dist\videogeniusAI.exe .\videogeniusAI.exe -Force
}

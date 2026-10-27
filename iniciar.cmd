@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Primero instala las dependencias siguiendo README.md.
  pause
  exit /b 1
)
echo Multimundo estara disponible en http://127.0.0.1:8000
".venv\Scripts\python.exe" -m narrative.cli serve --db data/canon.db
pause

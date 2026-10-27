@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Primero instala las dependencias siguiendo README.md.
  pause
  exit /b 1
)
if not exist "data\demo.db" (
  ".venv\Scripts\python.exe" -m narrative.cli demo --db data/demo.db
  if errorlevel 1 exit /b 1
)
echo DEMOSTRACION FICTICIA en http://127.0.0.1:8001
".venv\Scripts\python.exe" -m narrative.cli serve --db data/demo.db --port 8001
pause

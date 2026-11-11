@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -m venv .venv
    if errorlevel 1 goto erro
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto erro
".venv\Scripts\python.exe" run_local.py
if errorlevel 1 goto erro
exit /b 0
:erro
echo.
echo O Dizipy nao conseguiu iniciar. Copie a mensagem acima para conferir o problema.
pause
exit /b 1

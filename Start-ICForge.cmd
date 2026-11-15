@echo off
setlocal
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE="
"%~dp0.venv\Scripts\python.exe" -m fpga_agent --workspace "%~dp0."
exit /b %errorlevel%

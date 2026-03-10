@echo off
cd /d "%~dp0.."
set PIPER_BIN=%~dp0..\piper\piper\piper.exe
set PIPER_MODEL=%~dp0..\piper\voices\en_US-lessac-medium.onnx
set PY=C:\Users\ryder\AppData\Local\Programs\Python\Python311\python.exe
echo Piper: %PIPER_BIN%
echo Model: %PIPER_MODEL%
"%PY%" webapp\start_web.py
pause

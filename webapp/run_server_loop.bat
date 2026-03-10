@echo off
cd /d "%~dp0\.."
setlocal EnableDelayedExpansion
set retry2=0

:loop
echo.
echo ========== Clipper server starting at %DATE% %TIME% ==========
echo Open http://127.0.0.1:8000 in your browser.
echo.
python webapp\start_server.py
set code=!ERRORLEVEL!
echo.
if !code!==0 (
  echo [run_server_loop] Server exited cleanly. Exiting.
  exit /b 0
)
if !code!==2 (
  set /a retry2+=1
  if !retry2! LSS 3 (
    echo [run_server_loop] Another server may be running. Retry !retry2!/3 in 10 seconds...
    timeout /t 10 /nobreak >nul
    goto loop
  )
  echo [run_server_loop] Another server is already running. Close that window and try again.
  exit /b 2
)
set retry2=0
echo [run_server_loop] Server exited with code !code!, restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop

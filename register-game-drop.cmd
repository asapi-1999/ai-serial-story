@echo off
setlocal
node "%~dp0tools\register-game.js" %*
set "register_game_exit=%ERRORLEVEL%"
echo.
pause
exit /b %register_game_exit%

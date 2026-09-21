@echo off
REM ============================================================
REM  Oshinei - set up Telegram alerts (run once, by the owner).
REM  Walks you through it: paste the bot token (hidden), add the
REM  bot to your group, it finds the chat id itself, sends a test
REM  message, saves to backend\.env, and offers to set the live app.
REM  Before you start: in Telegram open @BotFather, send /newbot,
REM  and keep the token it gives you ready to paste.
REM  ASCII ONLY in this file (see Fetch Feedback.bat for why).
REM  Other uses:
REM     "Setup Telegram.bat" --test        send one test message
REM     "Setup Telegram.bat" --chat-only   keep the bot, change the chat
REM     "Setup Telegram.bat" --fly-only    copy saved settings to the live app
REM  NOT logged to a file on purpose - this window handles a secret.
REM ============================================================
cd /d "%~dp0"
call "%~dp0_python.bat" sys
if errorlevel 1 ( pause & exit /b 1 )
"%PY%" -u backend\setup_telegram.py %*
echo.
pause

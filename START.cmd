@echo off
rem FILM_TV_REMOTE - start the local media server.
rem Set your movies folder in filmy_server.py (MEDIA_ROOT) or via FILMY_ROOT env var.
title FILM_TV_REMOTE
cd /d "%~dp0"
python filmy_server.py
if errorlevel 1 (
  echo.
  echo Could not start. Make sure Python 3.8+ is installed and in PATH.
  pause
)

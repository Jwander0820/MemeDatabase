@echo off
setlocal
set "ROOT=%~dp0..\.."
cd /d "%ROOT%"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=C:\Users\Jwander\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
"%PYTHON%" -m src.anime_memes --db data\anime_memes.sqlite3 import-review-corrections %*

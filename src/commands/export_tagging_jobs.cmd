@echo off
setlocal
cd /d "%~dp0\..\.."
set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
"%PYTHON%" -m src.anime_memes --db data\anime_memes.sqlite3 export-tagging-jobs %*

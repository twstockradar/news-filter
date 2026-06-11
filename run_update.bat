@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

echo [%date% %time%] fetch...
python fetch_news.py
if errorlevel 1 (
  echo fetch failed, abort push.
  exit /b 1
)

echo [%date% %time%] push...
git add docs/data.js
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "data: auto update"
  git push
) else (
  echo no changes, skip push.
)

echo [%date% %time%] done.

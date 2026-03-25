@echo off
REM Remove all *.tmp and tmp* files from repo root (editor temp files).
cd /d "%~dp0"
del /q tmp*.tmp 2>nul
del /q *.tmp 2>nul
for /f "delims=" %%f in ('dir /b /a-d tmp* 2^>nul') do del /q "%%f" 2>nul
echo Cleaned tmp files from root.
pause

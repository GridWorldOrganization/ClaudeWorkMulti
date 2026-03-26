@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ==========================================
echo   Zombie Process Killer
echo ==========================================
echo.
echo Poller that started processes only.
echo.

set COUNT=0

REM --- .claude_pids に記録されたプロセス（ポーラーが起動したもののみ）---
echo [1] Poller-started Claude processes (.claude_pids)
echo.
if exist ".claude_pids" (
    for /f "usebackq" %%P in (".claude_pids") do (
        tasklist /FI "PID eq %%P" /FO CSV /NH 2>nul | findstr "%%P" >nul
        if not errorlevel 1 (
            set /a COUNT+=1
            set "PID_!COUNT!=%%P"
            echo   !COUNT!. PID=%%P  [poller-started]
        )
    )
)
if !COUNT!==0 echo   None.

REM --- windows_poller.py を実行中の python.exe ---
set POLLER_COUNT=0
echo.
echo [2] Poller processes (python + windows_poller.py)
echo.
for /f "tokens=2 delims=," %%P in ('wmic process where "name='python.exe'" get ProcessId /FORMAT:CSV 2^>nul ^| findstr /r "[0-9]"') do (
    set "PID=%%P"
    wmic process where "ProcessId=!PID!" get CommandLine /FORMAT:LIST 2>nul | findstr /i "windows_poller" >nul
    if not errorlevel 1 (
        set /a COUNT+=1
        set /a POLLER_COUNT+=1
        set "PID_!COUNT!=!PID!"
        echo   !COUNT!. python.exe  PID=!PID!  [windows_poller.py]
    )
)
if !POLLER_COUNT!==0 echo   None.

echo.
echo ------------------------------------------

if !COUNT!==0 (
    echo   No zombie processes found.
    if exist ".claude_pids" (
        echo   Cleaning up .claude_pids...
        del ".claude_pids"
        echo   Done.
    )
    goto :END
)

echo   Zombie processes: !COUNT!
echo.
echo   [a] Kill all zombies
echo   [q] Quit
echo.
set /p CHOICE="Select: "

if /i "!CHOICE!"=="q" goto :END

if /i "!CHOICE!"=="a" (
    echo.
    for /L %%i in (1,1,!COUNT!) do (
        call set "KILL_PID=%%PID_%%i%%"
        taskkill /F /PID !KILL_PID! >nul 2>&1
        echo   Killed PID=!KILL_PID!
    )
    if exist ".claude_pids" del ".claude_pids"
    echo.
    echo   Done.
)

:END
echo.
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ==========================================
echo   Claude Process Checker
echo ==========================================
echo.
echo [Running Claude Processes]
echo.

set COUNT=0

for /f "tokens=1,2 delims=," %%A in ('tasklist /FO CSV /NH 2^>nul ^| findstr /i "claude"') do (
    set /a COUNT+=1
    set "PROC_NAME=%%~A"
    set "PROC_PID=%%~B"
    set "PID_!COUNT!=!PROC_PID!"
    set "NAME_!COUNT!=!PROC_NAME!"

    set "TAG="
    if exist ".claude_pids" (
        for /f "usebackq" %%P in (".claude_pids") do (
            if "%%P"=="!PROC_PID!" set "TAG= *POLLER*"
        )
    )
    echo   !COUNT!. !PROC_NAME!  PID=!PROC_PID!!TAG!
)

if !COUNT!==0 (
    echo   No claude process found.
)

echo.
echo ------------------------------------------
echo [.claude_pids]
if exist ".claude_pids" (
    set PIDCOUNT=0
    for /f "usebackq" %%P in (".claude_pids") do (
        set /a PIDCOUNT+=1
        echo   PID=%%P
    )
    if !PIDCOUNT!==0 echo   empty
) else (
    echo   not found
)
echo ------------------------------------------
echo.

if !COUNT!==0 goto :END

echo   [number] Kill selected process
echo   [a]      Kill ALL processes
echo   [c]      Clear .claude_pids only
echo   [q]      Quit
echo.
set /p CHOICE="Select: "

if /i "!CHOICE!"=="q" goto :END

if /i "!CHOICE!"=="c" (
    if exist ".claude_pids" del ".claude_pids"
    echo Cleared .claude_pids
    goto :END
)

if /i "!CHOICE!"=="a" (
    echo Killing all...
    for /L %%i in (1,1,!COUNT!) do (
        call set "KILL_PID=%%PID_%%i%%"
        taskkill /F /PID !KILL_PID! >nul 2>&1
        echo   Killed PID=!KILL_PID!
    )
    if exist ".claude_pids" del ".claude_pids"
    echo Done.
    goto :END
)

set /a NUM=!CHOICE! 2>nul
if !NUM! GEQ 1 if !NUM! LEQ !COUNT! (
    call set "KILL_PID=%%PID_!NUM!%%"
    taskkill /F /PID !KILL_PID! >nul 2>&1
    echo Killed PID=!KILL_PID!
    goto :END
)

echo Invalid input.

:END
echo.
endlocal
pause

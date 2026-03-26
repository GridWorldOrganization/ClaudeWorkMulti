@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ==========================================
echo   Process Checker
echo ==========================================
echo.

set COUNT=0

REM --- Poller (python + windows_poller.py) ---
echo [1] Poller processes
echo.
for /f "tokens=2 delims=," %%P in ('wmic process where "name='python.exe'" get ProcessId /FORMAT:CSV 2^>nul ^| findstr /r "[0-9]"') do (
    set "PID=%%P"
    wmic process where "ProcessId=!PID!" get CommandLine /FORMAT:LIST 2>nul | findstr /i "windows_poller" >nul
    if not errorlevel 1 (
        set /a COUNT+=1
        set "PID_!COUNT!=!PID!"
        set "NAME_!COUNT!=python.exe"
        REM Check which folder
        set "PTAG=[poller]"
        wmic process where "ProcessId=!PID!" get CommandLine /FORMAT:LIST 2>nul | findstr /i "ClaudeWorkMulti" >nul
        if not errorlevel 1 (
            set "PTAG=[poller: ClaudeWorkMulti]"
        ) else (
            set "PTAG=[poller: OTHER]"
        )
        echo   !COUNT!. python.exe  PID=!PID!  !PTAG!
    )
)

REM --- Claude Native (claude.exe) ---
echo.
echo [2] Claude processes - Native
echo.
set CLAUDE_NATIVE=0
for /f "tokens=1,2 delims=," %%A in ('tasklist /FO CSV /NH 2^>nul ^| findstr /i "claude.exe"') do (
    set /a COUNT+=1
    set /a CLAUDE_NATIVE+=1
    set "PID_!COUNT!=%%~B"
    set "NAME_!COUNT!=%%~A"

    set "TAG=[Native]"
    if exist ".claude_pids" (
        for /f "usebackq" %%P in (".claude_pids") do (
            if "%%P"=="%%~B" set "TAG=[Native] *POLLER*"
        )
    )
    echo   !COUNT!. %%~A  PID=%%~B  !TAG!
)
if !CLAUDE_NATIVE!==0 echo   None.

REM --- Claude npm (node.exe) ---
echo.
echo [3] Claude processes - npm
echo.
set CLAUDE_NPM=0
for /f "tokens=2 delims=," %%P in ('wmic process where "name='node.exe'" get ProcessId /FORMAT:CSV 2^>nul ^| findstr /r "[0-9]"') do (
    set "NODE_PID=%%P"
    wmic process where "ProcessId=!NODE_PID!" get CommandLine /FORMAT:LIST 2>nul | findstr /i "claude" >nul
    if not errorlevel 1 (
        set /a COUNT+=1
        set /a CLAUDE_NPM+=1
        set "PID_!COUNT!=!NODE_PID!"
        set "NAME_!COUNT!=node.exe"

        set "TAG=[npm]"
        if exist ".claude_pids" (
            for /f "usebackq" %%Q in (".claude_pids") do (
                if "%%Q"=="!NODE_PID!" set "TAG=[npm] *POLLER*"
            )
        )
        echo   !COUNT!. node.exe  PID=!NODE_PID!  !TAG!
    )
)
if !CLAUDE_NPM!==0 echo   None.

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

if !COUNT!==0 (
    echo   No processes found.
    goto :END
)

echo   Total: !COUNT! process(es)
echo.
echo   [number] Kill selected process
echo   [a]      Kill ALL
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

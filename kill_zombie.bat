@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ==========================================
echo   Zombie Process Killer
echo ==========================================
echo.

set SHOW_ALL=0
if /i "%~1"=="--all" set SHOW_ALL=1
if /i "%~1"=="-a" set SHOW_ALL=1

if !SHOW_ALL!==1 (
    echo   Mode: ALL (poller + all claude processes)
) else (
    echo   Mode: POLLER ONLY (use --all to include all claude processes)
)
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

REM --- --all モード: 全 Claude プロセスも表示 ---
if !SHOW_ALL!==1 (
    set CLAUDE_COUNT=0

    echo.
    echo [3] All Claude processes - Native (claude.exe)
    echo.
    for /f "tokens=1,2 delims=," %%A in ('tasklist /FO CSV /NH 2^>nul ^| findstr /i "claude.exe"') do (
        set /a COUNT+=1
        set /a CLAUDE_COUNT+=1
        set "PID_!COUNT!=%%~B"
        echo   !COUNT!. %%~A  PID=%%~B  [Native]
    )
    if !CLAUDE_COUNT!==0 echo   None.

    set NODE_COUNT=0
    echo.
    echo [4] All Claude processes - npm (node.exe)
    echo.
    for /f "tokens=2 delims=," %%P in ('wmic process where "name='node.exe'" get ProcessId /FORMAT:CSV 2^>nul ^| findstr /r "[0-9]"') do (
        set "PID=%%P"
        wmic process where "ProcessId=!PID!" get CommandLine /FORMAT:LIST 2>nul | findstr /i "claude" >nul
        if not errorlevel 1 (
            set /a COUNT+=1
            set /a NODE_COUNT+=1
            set "PID_!COUNT!=!PID!"
            echo   !COUNT!. node.exe  PID=!PID!  [npm]
        )
    )
    if !NODE_COUNT!==0 echo   None.
)

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

echo   Total: !COUNT! process(es)
echo.
echo   [number] Kill selected process
echo   [a]      Kill ALL
echo   [c]      Clear .claude_pids only
echo   [q]      Quit
echo.
set /p CHOICE="Select: "

if /i "!CHOICE!"=="q" goto :EOF

if /i "!CHOICE!"=="c" (
    if exist ".claude_pids" (
        del ".claude_pids"
        echo   .claude_pids cleared.
    ) else (
        echo   .claude_pids not found.
    )
    goto :END
)

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
    goto :END
)

REM --- number selection ---
set /a SEL=!CHOICE! 2>nul
if !SEL! GEQ 1 if !SEL! LEQ !COUNT! (
    call set "KILL_PID=%%PID_!SEL!%%"
    taskkill /F /PID !KILL_PID! >nul 2>&1
    echo   Killed PID=!KILL_PID!
    echo   Done.
    goto :END
)

echo   Invalid selection.

:END
echo.
pause
goto :EOF

:QUIT

@echo off
REM ============================================================================
REM Merc Storia — one-shot offline setup
REM
REM Run this ONCE after copying the game folder to a new machine. It creates an
REM NTFS junction so that Unity's Addressables runtime (which always reads/
REM writes its cache under %LOCALAPPDATA%\..\LocalLow\jp_co_happyelements\
REM メルストM\AssetBundle) transparently lands on the bundled cache that ships
REM inside the game folder.
REM
REM After this runs, the game launches fully offline (no Steam, no network).
REM The cache PHYSICALLY lives in the game install — copying the install
REM folder to another machine and running Setup.cmd is all that's needed.
REM
REM Re-running this script is a safe no-op (re-creates the junction in-place).
REM ============================================================================

setlocal enableextensions
set "GAME_AB=%~dp0AssetBundle"
set "PERSIST_PARENT=%USERPROFILE%\AppData\LocalLow\jp_co_happyelements\メルストM"
set "PERSIST_AB=%PERSIST_PARENT%\AssetBundle"

if not exist "%GAME_AB%\" (
    echo [ERROR] Bundled cache not found at: %GAME_AB%
    echo         This script must be placed next to the game's AssetBundle folder.
    pause
    exit /b 1
)

if not exist "%PERSIST_PARENT%\" (
    echo Creating persistent-data parent: %PERSIST_PARENT%
    mkdir "%PERSIST_PARENT%"
)

REM If something already exists at the junction location, move it aside
if exist "%PERSIST_AB%\" (
    REM Detect junction (reparse point) — fsutil reparsepoint query exits 0 if reparse
    fsutil reparsepoint query "%PERSIST_AB%" >nul 2>&1
    if not errorlevel 1 (
        echo Removing existing junction at: %PERSIST_AB%
        rmdir "%PERSIST_AB%"
    ) else (
        echo Found existing directory at junction location; renaming aside.
        set "BAK=%PERSIST_AB%.pre_setup"
        set "I=0"
        :findbak
        if exist "%BAK%\" (
            set /a I=I+1
            set "BAK=%PERSIST_AB%.pre_setup_%I%"
            goto findbak
        )
        ren "%PERSIST_AB%" "%~nx0%BAK%"
        REM ^ fallback if ren fails: try move
        if errorlevel 1 move "%PERSIST_AB%" "%BAK%" >nul
    )
)

echo Creating junction:
echo     %PERSIST_AB%
echo  ^-^>  %GAME_AB%
mklink /J "%PERSIST_AB%" "%GAME_AB%"
if errorlevel 1 (
    echo [ERROR] mklink failed. The junction was not created.
    pause
    exit /b 1
)

echo.
echo Setup complete. You can now run メルストM.exe.
echo.
pause

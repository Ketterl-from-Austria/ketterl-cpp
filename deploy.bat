@echo off
:: ── CPP Villa – Änderungen auf GitHub pushen ──────────────────────────────────
:: Pi pulled automatisch alle 5 Minuten und baut die HTML neu.
:: Ausführen aus: K:\KNX-Visu-Ketterl\

cd /d "%~dp0"

echo.
set /p MSG=Commit-Nachricht (Enter = "Update KNX config"):
if "%MSG%"=="" set MSG=Update KNX config

echo.
echo [1/3] Status prüfen...
git status --short

echo.
echo [2/3] Alle Änderungen committen...
git add knx_config_cpp.json gen_villa_real.py rebuild.sh overrides.json 2>nul
git commit -m "%MSG%"
if errorlevel 1 (
    echo Nichts zu committen oder Fehler.
    pause
    exit /b 0
)

echo.
echo [3/3] Push zu GitHub...
git push origin main
if errorlevel 1 (
    echo FEHLER beim Push!
    pause
    exit /b 1
)

echo.
echo ✓ Fertig – Pi updated in ~5 Minuten automatisch.
echo   Log: ssh kettleradm@ketterl-cpp "tail -20 /tmp/ketterl-rebuild.log"
pause

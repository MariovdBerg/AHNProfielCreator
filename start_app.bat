@echo off
rem Start de AHN profielen webapp in de browser.
rem
rem Gebruik: dubbelklik, of sleep een .ini-bestand op dit bestand, of:
rem     start_app.bat pad\naar\config.ini
rem Zonder argument wordt config.ini naast dit bestand gebruikt (als die bestaat).
setlocal

rem ---- Instellingen ----------------------------------------------------------
rem Conda-omgeving met fiona, rasterio, shapely, numpy en tqdm (zie environment.yml).
if not defined AHN_ENV set "AHN_ENV=ahnprofielcreator"
rem ----------------------------------------------------------------------------

set "CONFIG="
if not "%~1"=="" set "CONFIG=%~f1"

cd /d "%~dp0"
if not defined CONFIG if exist "config.ini" set "CONFIG=%~dp0config.ini"

rem Zoek conda (miniforge/anaconda) en activeer de omgeving.
set "ACTIVATE="
for %%D in ("%CONDA_ROOT%" "C:\ProgramData\miniforge3" "%USERPROFILE%\miniforge3" "C:\ProgramData\anaconda3" "%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3") do (
    if not defined ACTIVATE if exist "%%~D\Scripts\activate.bat" set "ACTIVATE=%%~D\Scripts\activate.bat"
)

if defined ACTIVATE (
    call "%ACTIVATE%" "%AHN_ENV%"
    if errorlevel 1 goto :no_env
) else (
    echo Geen conda gevonden, de python uit PATH wordt gebruikt.
)

python -c "import fiona, rasterio, shapely, numpy, tqdm" >nul 2>&1
if errorlevel 1 goto :no_env

if defined CONFIG (
    echo Instellingen: %CONFIG%
    python app.py --config "%CONFIG%"
) else (
    echo Geen config.ini gevonden; de app start met het demotraject.
    python app.py
)
if errorlevel 1 pause
exit /b

:no_env
echo.
echo De conda-omgeving "%AHN_ENV%" bestaat niet of mist pakketten.
echo Maak de omgeving aan met:
echo     conda env create -f environment.yml
echo of gebruik een bestaande omgeving: zet AHN_ENV bovenin dit bestand.
pause
exit /b 1

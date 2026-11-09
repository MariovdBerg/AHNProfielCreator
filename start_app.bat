@echo off
rem Start de AHN profielen webapp in de browser.
rem
rem Gebruik: dubbelklik, of sleep een werkmap of de config.ini van een project op dit bestand, of:
rem     start_app.bat pad\naar\werkmap
rem     start_app.bat pad\naar\werkmap\project\config.ini
rem Zonder argument is de map van dit bestand de werkmap en opent het project demo.
setlocal

rem ---- Instellingen ----------------------------------------------------------
rem Conda-omgeving met fiona, rasterio, shapely, numpy en tqdm (zie environment.yml).
if not defined AHN_ENV set "AHN_ENV=ahnprofielcreator"
rem ----------------------------------------------------------------------------

rem Een map is de werkmap, een bestand de config.ini van een project.
set "ARGS="
if not "%~1"=="" (
    if exist "%~f1\*" (set ARGS=--werkmap "%~f1\.") else (set ARGS=--config "%~f1")
)

cd /d "%~dp0"

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

python app.py %ARGS%
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

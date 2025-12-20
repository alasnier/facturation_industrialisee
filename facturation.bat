
@echo off
setlocal EnableExtensions

REM --- Chemin du projet ---
set "PROJECT_DIR=C:\Users\a050320\OneDrive - Alliance\Documents PERSO\side_projects\facturation_industrialisee"

REM --- Chemin du venv ---
set "VENV_ACTIVATE=%PROJECT_DIR%\facturation_uvenv\Scripts\activate.bat"

cd /d "%PROJECT_DIR%"

echo [INFO] Mise a jour du code (branche release)...
git checkout release
git pull origin release
if errorlevel 1 (
  echo [ERREUR] git pull a echoue.
  pause
  exit /b 1
)

if exist "%VENV_ACTIVATE%" (
  call "%VENV_ACTIVATE%"
) else (
  echo [ERREUR] Venv introuvable: %VENV_ACTIVATE%
  pause
  exit /b 1
)

if exist "requirements.txt" (
  echo [INFO] Mise a jour dependances...
  pip install -r requirements.txt
)

echo [INFO] Lancement Streamlit...
python -m streamlit run app.py

pause
endlocal

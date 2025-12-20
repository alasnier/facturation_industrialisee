
@echo off
setlocal EnableExtensions

REM --- Chemin du projet ---
set "PROJECT_DIR=C:\Users\a050320\OneDrive - Alliance\Documents PERSO\side_projects\facturation_industrialisee"

REM --- Chemin du venv ---
set "VENV_ACTIVATE=C:\Users\a050320\main_uvenv\Scripts\activate.bat"

echo [INFO] Dossier projet : %PROJECT_DIR%
if not exist "%PROJECT_DIR%" (
  echo [ERREUR] Le dossier projet n'existe pas.
  pause
  exit /b 1
)

cd /d "%PROJECT_DIR%"

REM --- Active l'environnement virtuel ---
if exist "%VENV_ACTIVATE%" (
  echo [INFO] Activation UV virtual environment...
  call "%VENV_ACTIVATE%"
) else (
  echo [ERREUR] Venv introuvable: %VENV_ACTIVATE%
  pause
  exit /b 1
)

REM --- Vérifs minimales ---
if not exist "credentials.json" (
  echo [ERREUR] credentials.json manquant dans %PROJECT_DIR%
  pause
  exit /b 1
)
if not exist ".env" (
  echo [ERREUR] .env manquant dans %PROJECT_DIR%
  pause
  exit /b 1
)

echo [INFO] Lancement application de facturation...
python -m streamlit run app.py

echo [INFO] Streamlit s'est arrêté (ou a crash). Voir les logs au-dessus.
pause
endlocal

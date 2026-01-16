@echo off
setlocal EnableExtensions

echo.
echo ========================================
echo  Facturation - Lancement v1.1
echo ========================================
echo .

REM --- Chemin du projet (installation remote proposee) ---
set "PROJECT_DIR=C:\Facturation\facturation_industrialisee"

echo [1/4]  [INFO] Activation de l'environnement virtuel...
echo .
echo .
echo .

REM --- Chemin du venv ---
set "VENV_ACTIVATE=%PROJECT_DIR%\facturation_uvenv\Scripts\activate.bat"

cd /d "%PROJECT_DIR%"

echo [2/4]  [INFO] Forcage de la mise a jour (ecrasement local)...
echo .

git checkout release
git fetch origin release
if errorlevel 1 (
  echo [ERREUR] Impossible de contacter le depot distant.
  pause
  exit /b 1
)

git reset --hard origin/release
if errorlevel 1 (
  echo [ERREUR] Le reset a echoue.
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

if exist "requirements.in" (
  echo .
  echo .
  echo .
  echo [3/4]  [INFO] Mise a jour dependances...
  del /f /q requirements.txt
  uv pip compile requirements.in -o requirements.txt
  echo .
)

if exist "requirements.txt" (
  echo.
  uv pip sync requirements.txt
)


echo [4/4]  [INFO] Lancement de l'interface web...
echo .
start "" /B pythonw -m streamlit run app.py >nul 2>&1
echo .
echo ========================================
echo  Interface prete dans votre navigateur
echo ========================================
echo .
echo .
echo .

timeout /t 10 /nobreak >nul
exit

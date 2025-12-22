
@echo off
setlocal EnableExtensions

REM =========================
REM PARAMETRES A ADAPTER
REM =========================
set "INSTALL_DIR=C:\Facturation"
set "REPO_URL=https://github.com/alasnier/facturation_industrialisee.git"
set "REPO_DIR=%INSTALL_DIR%\facturation_industrialisee"
set "VENV_DIR=%REPO_DIR%\facturation_uvenv"

echo [INFO] Dossier d'installation : %INSTALL_DIR%
echo [INFO] Repo : %REPO_URL%
echo.

REM =========================
REM 0) VERIF winget
REM =========================
where winget >nul 2>&1
if errorlevel 1 (
  echo [ERREUR] winget n'est pas disponible sur ce PC.
  echo [AIDE] winget est fourni par "App Installer" (Microsoft Store).
  echo        Installe/Met a jour "App Installer" puis relance ce script.
  pause
  exit /b 1
)

REM =========================
REM 1) INSTALL GIT
REM =========================
where git >nul 2>&1
if errorlevel 1 (
  echo [INFO] Installation de Git...
  winget install --id Git.Git -e --source winget
) else (
  echo [INFO] Git deja installe.
)

REM =========================
REM 2) INSTALL UV
REM =========================
where uv >nul 2>&1
if errorlevel 1 (
  echo [INFO] Installation de uv...
  winget install --id astral-sh.uv -e
) else (
  echo [INFO] uv deja installe.
)

REM =========================
REM 3) CREER DOSSIER INSTALL
REM =========================
if not exist "%INSTALL_DIR%" (
  mkdir "%INSTALL_DIR%"
)

REM =========================
REM 4) CLONE REPO
REM =========================
if not exist "%REPO_DIR%\.git" (
  echo [INFO] Clonage du repo...
  cd /d "%INSTALL_DIR%"
  git clone "%REPO_URL%"
) else (
  echo [INFO] Repo deja clone.
)

cd /d "%REPO_DIR%"

REM =========================
REM 5) CHECKOUT release
REM =========================
echo [INFO] Passage sur la branche release...
git fetch --all
git checkout release

REM =========================
REM 6) CREER VENV
REM =========================
if not exist "%VENV_DIR%\Scripts\activate.bat" (
  echo [INFO] Creation du venv via uv...
  uv venv "%VENV_DIR%"
) else (
  echo [INFO] Venv deja existant.
)

REM =========================
REM 7) ACTIVER VENV + INSTALL DEP
REM =========================
call "%VENV_DIR%\Scripts\activate.bat"
echo [INFO] Installation des dependances...
uv pip install -r requirements.txt

echo.
echo [NEXT] Il reste 2 actions manuelles :
echo   1) Copier credentials.json dans %REPO_DIR%
echo   2) Creer/configurer le .env dans %REPO_DIR%
echo.
echo [NEXT] Ensuite : lancer l'app une premiere fois pour OAuth :
echo   python -m streamlit run app.py
echo.
pause
endlocal

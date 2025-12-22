
@echo off
setlocal EnableExtensions

REM =========================
REM PARAMETRES
REM =========================
set "INSTALL_DIR=C:\Facturation"
set "REPO_URL=https://github.com/alasnier/facturation_industrialisee.git"
set "REPO_DIR=%INSTALL_DIR%\facturation_industrialisee"
set "VENV_DIR=%REPO_DIR%\facturation_uvenv"

echo ==========================================================
echo  FACTURATION - INSTALLATION AUTOMATIQUE (REMOTE)
echo ==========================================================
echo [INFO] Dossier install : %INSTALL_DIR%
echo [INFO] Repo          : %REPO_URL%
echo.

REM =========================
REM 0) WINGET
REM =========================
where winget >nul 2>&1
if errorlevel 1 (
  echo [ERREUR] winget n'est pas disponible sur ce PC.
  echo [AIDE] winget est fourni par "App Installer" (Microsoft).
  echo        Installe/Met a jour "App Installer" (Microsoft Store),
  echo        puis relance ce script.
  echo.
  echo [PLAN B] Si Microsoft Store indisponible :
  echo        installer winget via msixbundle (DesktopAppInstaller).
  echo        Voir: https://learn.microsoft.com/en-us/windows/package-manager/winget/
  pause
  exit /b 1
)

REM =========================
REM 1) GIT
REM =========================
where git >nul 2>&1
if errorlevel 1 (
  echo [INFO] Installation de Git...
  winget install --id Git.Git -e --source winget
) else (
  echo [INFO] Git deja installe.
)

REM =========================
REM 2) UV
REM =========================
where uv >nul 2>&1
if errorlevel 1 (
  echo [INFO] Installation de uv...
  winget install --id astral-sh.uv -e
) else (
  echo [INFO] uv deja installe.
)

REM =========================
REM 3) DOSSIER D INSTALL
REM =========================
if not exist "%INSTALL_DIR%" (
  echo [INFO] Creation du dossier %INSTALL_DIR% ...
  mkdir "%INSTALL_DIR%"
)

REM =========================
REM 4) CLONE
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
REM 5) BRANCHE release
REM =========================
echo [INFO] Synchronisation et checkout release...
git fetch --all
git checkout release

REM =========================
REM 6) VENV UV
REM =========================
if not exist "%VENV_DIR%\Scripts\activate.bat" (
  echo [INFO] Creation du venv uv: %VENV_DIR%
  uv venv "%VENV_DIR%"
) else (
  echo [INFO] Venv deja existant.
)

REM =========================
REM 7) INSTALL DEPENDANCES
REM =========================
call "%VENV_DIR%\Scripts\activate.bat"

if exist "requirements.txt" (
  echo [INFO] Installation des dependances (requirements.txt)...
  uv pip install -r requirements.txt
) else (
  echo [ERREUR] requirements.txt introuvable dans %REPO_DIR%
  pause
  exit /b 1
)

echo.
echo ==========================================================
echo  INSTALLATION TERMINEE
echo ==========================================================
echo [NEXT] 2 actions MANUELLES a faire dans: %REPO_DIR%
echo   1) Copier le fichier credentials.json a la racine du projet
echo   2) Creer / configurer le fichier .env a la racine du projet
echo.
echo [NEXT] Ensuite lancer l'application pour OAuth (1ere fois) :
echo   - Double cliquer sur Facturation.bat (ou)
echo   - python -m streamlit run app.py
echo.
echo [INFO] Apres OAuth, token.json sera cree automatiquement.
echo.
pause
endlocal

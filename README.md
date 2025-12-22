
# 🧾 Facturation industrialisée (MVP) — Streamlit + Google Sheets + Gmail + Drive

Ce projet permet à un praticien (ex : psychiatre) de **générer une facture PDF** en quelques clics et de l’**envoyer automatiquement par email** (client + comptable en copie), tout en archivant le PDF dans Google Drive et en historisant l’opération dans un onglet `factures`.

---

## 🎯 Objectif

- ✅ Sélectionner un **client** (BDD clients)
- ✅ Sélectionner une **prestation/produit** (BDD produits)
- ✅ Cliquer sur **“Générer & envoyer”**
- ✅ Automatiser :
  - génération PDF
  - upload Drive
  - envoi email via Gmail API
  - log dans Google Sheet (onglet `factures`)

---

## ✨ Fonctionnalités

- 🧾 **Facture PDF** numérotée : `FACT-YYYYMM-####`
- 📂 **Archivage Drive** (dossier cible configurable)
- 📧 **Envoi Gmail API** (destinataire + CC comptable)
- 📊 **Historique** dans l’onglet `factures`
- 🧠 Gestion des formats de prix (espaces milliers, €) et suppression des caractères non supportés en PDF
- ⚙️ Déploiement simple via scripts `.bat` (update + launch)

---

## 🧱 Architecture

- **Google Sheet** (1 seul fichier) avec 3 onglets :
  - `BDD client` : `id | nom | prenom | rue | code postal | ville | mail`
  - `produits` : `id | libelle | prix_ht | TVA | prix_ttc`
  - `factures` : historisation (créé/initialisé automatiquement si nécessaire)
- **Google Drive** : stockage des PDF
- **Gmail API** : envoi des factures (OAuth Desktop + token local)

---

## 🧰 Stack technique

- Python
- Streamlit (UI)
- ReportLab (PDF)
- Google API Client (Sheets/Drive/Gmail)
- UV (gestion Python/venv/dépendances)

---

# 🚀 Installation (Windows) — Mode Admin

> Objectif : permettre au praticien de n’avoir **qu’un double-clic** à faire ensuite.

## 1) Prérequis machine
- Windows 10/11
- Droits administrateur

### WinGet (si disponible)
WinGet fait partie de **App Installer** sur Windows et peut nécessiter une mise à jour / installation. [3](https://learn.microsoft.com/en-us/windows/package-manager/winget/)

## 2) Installer Git
```powershell
winget install --id Git.Git -e --source winget
```

(commande recommandée sur la page officielle Git for Windows)


## 4) Cloner le projet
```PowerShell
cd "C:\Facturation"
git clone https://github.com/alasnier/facturation_industrialisee.git
cd facturation_industrialisee
git checkout release
```

## 5) Créer un environnement virtuel UV
```PowerShell
uv venv facturation_uvenv
.\facturation_uvenv\Scripts\Activate.ps1
```
UV gère les environnements Python et peut télécharger Python automatiquement si nécessaire. 

## 6) Installer les dépendances
```PowerShell
uv pip install -r requirements.txt
```


## 7) Copier les fichiers de configuration (local machine)

Copier credentials.json à la racine <br>
Créer .env à la racine

Exemple .env :
```Plain Text

GOOGLE_FOLDER_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxx
ACCOUNTING_SPREADSHEET_ID=yyyyyyyyyyyyyyyyyyy
PRACTICE_NAME=Cabinet Dr. Nom Prénom
PRACTICE_ADDRESS=18 rue de Noailles\n28130 MAINTENON
PRACTICE_SIRET=123 456 789 00012
PRACTICE_TVA_NUMBER=
PRACTITIONER_EMAIL=exemple@gmail.com
COMPTABLE_EMAIL=compta@exemple.comShow more lines
```

## 8) Premier lancement (OAuth)
```PowerShell
python -m streamlit run app.py
```
Un navigateur s’ouvre <br>
Autoriser l’accès Google (Sheets/Drive/Gmail) <br>
token.json est créé localement

## 9) Test de recette
Créer une facture test envoyée vers une adresse de validation (ex : intégrateur), vérifier :
* email reçu
* PDF attaché
* PDF dans Drive
* ligne créée dans factures


## 🖱️ Utilisation quotidienne (client)
Un script Facturation.bat est placé sur le bureau :
* met à jour le code (branche release)
* met à jour les dépendances (requirements)
* lance Streamlit
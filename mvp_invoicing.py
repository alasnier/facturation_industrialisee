#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MVP facturation (Google Sheets + Drive + Gmail) - version 'comptabilite' (un seul fichier)

- Lit le Google Sheet 'comptabilite' avec 3 onglets: clients, produits, factures
- Génère un PDF de facture (ReportLab)
- Upload le PDF dans un dossier Google Drive
- Envoie la facture par email via Gmail API
- Journalise la facture dans l'onglet 'factures'
- Numérotation: FACT-YYYYMM-#### (ex: FACT-202512-0001)

Dépendances:
  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib reportlab python-dotenv

Fichiers requis:
  credentials.json (OAuth 2.0 Desktop app)
  .env (voir exemple dans la réponse)

Usage:
  python mvp_invoicing.py --client-id <ID_CLIENT> --product-id <ID_PRODUIT> --qty 1 --notes "Séance du 5/12"
"""

import os
import sys
import re
import base64
import argparse
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional

from dotenv import load_dotenv

# ReportLab (PDF)
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.styles import ParagraphStyle

# Gmail MIME
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# Google APIs
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# --- OAuth scopes ---
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/gmail.send",
]

# --------------------------
# Helpers & utilitaires
# --------------------------


def load_google_credentials() -> Credentials:
    """
    Charge ou crée les credentials OAuth (token.json) pour Desktop app.
    """
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"⚠️ Refresh token error: {e}")
                creds = None
        if not creds:
            if not os.path.exists("credentials.json"):
                raise FileNotFoundError(
                    "credentials.json manquant. Crée des identifiants OAuth 'Desktop app' dans Google Cloud."
                )
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        # Sauvegarde
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return creds


def build_services(creds: Credentials):
    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    gmail = build("gmail", "v1", credentials=creds)
    return sheets, drive, gmail


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def fmt_eur(x: float) -> str:
    return f"{x:,.2f} €".replace(",", " ").replace(".", ",")


def parse_currency(value: str) -> float:
    """
    Convertit une chaîne monétaire (ex: '1 025,48 €') en float.
    Gère les espaces comme séparateur de milliers et la virgule comme séparateur décimal.
    """
    if not isinstance(value, str):
        return 0.0
    try:
        # Supprime le symbole €, les espaces (séparateur de milliers),
        # et remplace la virgule décimale par un point.
        cleaned_value = (
            value.replace("€", "").replace(" ", "").replace(",", ".").strip()
        )
        return float(cleaned_value)
    except (ValueError, TypeError):
        # Retourne 0.0 si la conversion échoue (ex: chaîne vide ou non numérique)
        return 0.0


# --------------------------
# Google Sheets utils
# --------------------------


def list_sheet_titles(sheets, spreadsheet_id: str) -> List[str]:
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def pick_sheet_title(
    sheets, spreadsheet_id: str, preferred_names=("clients",), fallback=True
) -> str:
    titles = list_sheet_titles(sheets, spreadsheet_id)
    norm = {t.lower().strip(): t for t in titles}
    for pref in preferred_names:
        key = pref.lower().strip()
        if key in norm:
            return norm[key]
    if fallback and titles:
        return titles[0]
    raise RuntimeError(f"Aucun onglet correspondant. Onglets disponibles: {titles}")


def read_table_by_title(
    sheets, spreadsheet_id: str, sheet_title: str, range_columns: str
) -> List[Dict[str, str]]:
    """
    Lit un onglet (sheet_title) et une plage de colonnes (ex 'A1:G'),
    en gérant correctement les titres avec espaces/accents via quotes.
    """
    a1 = f"'{sheet_title}'!{range_columns}"
    res = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=a1)
        .execute()
    )
    values = res.get("values", [])
    if not values or len(values) < 2:
        return []
    headers = [h.strip().lower() for h in values[0]]
    rows = []
    for row in values[1:]:
        item = {}
        for i, h in enumerate(headers):
            item[h] = row[i].strip() if i < len(row) else ""
        rows.append(item)
    return rows


def init_factures_header_if_missing(
    sheets, spreadsheet_id: str, sheet_title: str = "factures"
):
    """
    Garantit l'existence de l'onglet 'factures' et de ses en-têtes.
    """
    try:
        res = (
            sheets.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{sheet_title}'!A1:M1")
            .execute()
        )
        values = res.get("values", [])
        if values:
            return
    except HttpError:
        pass  # peut être absent

    # Crée l'onglet s'il n'existe pas déjà
    titles = list_sheet_titles(sheets, spreadsheet_id)
    if sheet_title not in titles:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet_title}}}]},
        ).execute()

    headers = [
        [
            "numero",
            "date",
            "client_id",
            "client_nom",
            "client_prenom",
            "produit_id",
            "libelle",
            "quantite",
            "montant_ht",
            "montant_tva",
            "montant_ttc",
            "lien_drive",
            "email_envoye_a",
        ]
    ]
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_title}'!A1:M1",
        valueInputOption="RAW",
        body={"values": headers},
    ).execute()


def append_facture_row(sheets, spreadsheet_id: str, sheet_title: str, row: List[str]):
    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_title}'!A2",
        valueInputOption="RAW",
        body={"values": [row]},
    ).execute()


def get_next_invoice_number_monthly(
    sheets, spreadsheet_id: str, sheet_title: str = "factures"
) -> str:
    """
    Numérotation mensuelle: FACT-YYYYMM-#### (#### séquentiel dans le mois courant).
    """
    now = datetime.now()
    year = now.year
    month = now.month
    yyyymm = f"{year}{month:02d}"
    prefix = f"FACT-{yyyymm}-"

    try:
        res = (
            sheets.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{sheet_title}'!A2:A")
            .execute()
        )
        numbers = [r[0] for r in res.get("values", []) if r]
        seq = [
            int(n.replace(prefix, ""))
            for n in numbers
            if isinstance(n, str)
            and n.startswith(prefix)
            and re.match(rf"^{prefix}\d{{4}}$", n)
        ]
        next_n = (max(seq) + 1) if seq else 1
    except Exception:
        next_n = 1

    return f"{prefix}{next_n:04d}"


# --------------------------
# Domain models
# --------------------------


@dataclass
class Client:
    id: str
    nom: str
    prenom: str
    rue: str
    code_postal: str
    ville: str
    mail: str


@dataclass
class Product:
    id: str
    libelle: str
    prix_ht: float
    prix_ttc: float
    tva_rate_for_display: (
        float  # Nouveau: le taux de TVA en décimal (ex: 0.20 pour 20%)
    )
    is_tva_exempt: bool  # Nouveau: True si TVA 0%


def find_client(clients: List[Dict[str, str]], id_: str) -> Client:
    for c in clients:
        if c.get("id") == id_:
            return Client(
                id=c.get("id", ""),
                nom=c.get("nom", ""),
                prenom=c.get("prenom", ""),
                rue=c.get("rue", ""),
                code_postal=c.get("code postal", ""),
                ville=c.get("ville", ""),
                mail=c.get("mail", ""),
            )
    raise ValueError(f"Client id={id_} introuvable.")


def find_product(
    products: List[Dict[str, str]], id_: str
) -> Product:  # Supprimez le paramètre tva_exempt
    for p in products:
        if p.get("id") == id_:
            ht = parse_currency(p.get("prix_ht", "0"))
            ttc_from_sheet = parse_currency(p.get("prix_ttc", "0"))
            tva_str = p.get("tva", "0%").strip()  # Lire la colonne 'TVA' du sheet

            # Déterminer si le produit est exempt de TVA
            is_tva_exempt = tva_str == "0%" or tva_str == "0"

            tva_rate_for_display = 0.0
            if not is_tva_exempt:
                try:
                    # Tenter de parser le pourcentage de TVA directement de la colonne 'TVA'
                    # Ex: "20%" -> 20.0 -> 0.20
                    tva_rate_for_display = (
                        parse_currency(tva_str.replace("%", "")) / 100.0
                    )
                except (ValueError, TypeError):
                    # Fallback: si le parsing échoue, calculer à partir de HT/TTC
                    if ht > 0:
                        tva_rate_for_display = max(0.0, (ttc_from_sheet / ht) - 1.0)

            # Assurer la cohérence du TTC :
            # Si exempt, TTC doit être égal à HT.
            # Sinon, on fait confiance au TTC fourni dans la feuille.
            final_ttc = ttc_from_sheet
            if is_tva_exempt:
                final_ttc = ht

            return Product(
                id=p.get("id", ""),
                libelle=p.get("libelle", ""),
                prix_ht=ht,
                prix_ttc=final_ttc,
                tva_rate_for_display=tva_rate_for_display,
                is_tva_exempt=is_tva_exempt,
            )
    raise ValueError(f"Produit id={id_} introuvable.")


# --------------------------
# PDF generation
# --------------------------


def generate_invoice_pdf(
    output_path: str,
    invoice_number: str,
    date_str: str,
    practice_name: str,
    practice_address: str,
    practice_siret: str,
    practice_tva_number: Optional[str],
    # Supprimez la ligne suivante : tva_exempt: bool,
    client: Client,
    product: Product,  # L'objet product contient maintenant les infos TVA
    qty: int,
    notes: Optional[str],
):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    style_title = styles["Heading1"]
    style_normal = styles["Normal"]
    style_right = ParagraphStyle("right", parent=styles["Normal"], alignment=TA_RIGHT)
    style_small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9)
    elems = []

    # En-tête cabinet
    elems.append(Paragraph(practice_name, style_title))
    if practice_address:
        elems.append(Paragraph(practice_address, style_normal))
    if practice_siret:
        elems.append(Paragraph(f"SIRET : {practice_siret}", style_normal))
    if practice_tva_number:
        elems.append(
            Paragraph(f"N° TVA intracom : {practice_tva_number}", style_normal)
        )
    # Remplacer : if tva_exempt:
    if product.is_tva_exempt:  # Utiliser le drapeau spécifique au produit
        elems.append(
            Paragraph(
                "Exonération de TVA (art. 261 du CGI – actes médicaux).", style_small
            )
        )
    elems.append(Spacer(1, 10))

    # Facture + date
    elems.append(Paragraph(f"<b>Facture n° {invoice_number}</b>", style_normal))
    elems.append(Paragraph(f"Date : {date_str}", style_normal))
    elems.append(Spacer(1, 6))

    # Client
    client_block = f"""
    <b>Client</b><br/>
    {client.prenom} {client.nom}<br/>
    {client.rue}<br/>
    {client.code_postal} {client.ville}<br/>
    {client.mail}
    """
    elems.append(Paragraph(client_block, style_normal))
    elems.append(Spacer(1, 12))

    # Détails ligne
    montant_ht = product.prix_ht * qty
    montant_ttc = product.prix_ttc * qty
    montant_tva = montant_ttc - montant_ht  # Calculer la TVA directement
    tva_rate_pct_display = int(
        round(product.tva_rate_for_display * 100)
    )  # Utiliser le taux pour l'affichage

    data = [
        ["Libellé", "Qté", "PU HT", "TVA", "Total HT", "Total TTC"],
        [
            product.libelle,
            str(qty),
            fmt_eur(product.prix_ht),
            f"{tva_rate_pct_display}%",  # Afficher le taux de TVA du produit
            fmt_eur(montant_ht),
            fmt_eur(montant_ttc),
        ],
    ]
    table = Table(
        data, colWidths=[80 * mm, 15 * mm, 25 * mm, 15 * mm, 25 * mm, 25 * mm]
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    elems.append(table)
    elems.append(Spacer(1, 10))

    # Totaux
    totals = [
        ["Total HT", fmt_eur(montant_ht)],
        ["TVA", fmt_eur(montant_tva)],
        ["Total TTC", fmt_eur(montant_ttc)],
    ]
    totals_table = Table(totals, colWidths=[40 * mm, 40 * mm])
    totals_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ]
        )
    )
    elems.append(totals_table)
    elems.append(Spacer(1, 8))

    if notes:
        elems.append(Paragraph(f"<b>Notes :</b> {notes}", style_small))
        elems.append(Spacer(1, 6))

    # Mentions
    elems.append(
        Paragraph(
            "Paiement comptant à réception. Facture émise électroniquement.",
            style_small,
        )
    )
    elems.append(Spacer(1, 4))
    elems.append(
        Paragraph(
            "Cette facture a été archivée dans votre espace sécurisé.", style_small
        )
    )

    doc.build(elems)


# --------------------------
# Drive + Gmail
# --------------------------


def upload_to_drive(drive, file_path: str, folder_id: str) -> Dict[str, str]:
    file_metadata = {
        "name": os.path.basename(file_path),
        "parents": [folder_id],
        "mimeType": "application/pdf",
    }
    media = MediaFileUpload(file_path, mimetype="application/pdf", resumable=True)
    file = (
        drive.files()
        .create(body=file_metadata, media_body=media, fields="id, webViewLink")
        .execute()
    )
    return {"id": file["id"], "link": file.get("webViewLink", "")}


def send_email_gmail(
    gmail,
    sender: str,
    to: str,
    cc: Optional[str],
    subject: str,
    html_body: str,
    attachment_path: str,
):
    msg = MIMEMultipart()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["From"] = sender or ""
    msg["Subject"] = subject

    msg.attach(MIMEText(html_body, "html"))

    # Pièce jointe
    with open(attachment_path, "rb") as f:
        part = MIMEBase("application", "pdf")
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(attachment_path)}"',
        )
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    message = {"raw": raw}
    gmail.users().messages().send(userId="me", body=message).execute()


# --------------------------
# CLI main
# --------------------------


def main():
    load_dotenv()

    # ENV
    folder_id = os.getenv("GOOGLE_FOLDER_ID")
    acc_ss_id = os.getenv("ACCOUNTING_SPREADSHEET_ID")

    practice_name = os.getenv("PRACTICE_NAME", "Cabinet")
    practice_address = os.getenv("PRACTICE_ADDRESS", "")
    practice_siret = os.getenv("PRACTICE_SIRET", "")
    practice_tva_number = os.getenv("PRACTICE_TVA_NUMBER", "")
    # Supprimez la ligne suivante :
    # tva_exempt = os.getenv("TVA_EXEMPT", "false").lower().strip() == "true"

    sender_email = os.getenv("PRACTITIONER_EMAIL", "")
    accountant_email = os.getenv("COMPTABLE_EMAIL", "")

    if not folder_id or not acc_ss_id:
        print(
            "❌ .env incomplet. Renseigne GOOGLE_FOLDER_ID et ACCOUNTING_SPREADSHEET_ID."
        )
        sys.exit(1)

    # Args
    parser = argparse.ArgumentParser(description="Génération & envoi de facture")
    parser.add_argument("--client-id", required=True, help="ID client (colonne 'id')")
    parser.add_argument("--product-id", required=True, help="ID produit (colonne 'id')")
    parser.add_argument("--qty", type=int, default=1, help="Quantité")
    parser.add_argument("--notes", type=str, default="", help="Notes sur la facture")
    args = parser.parse_args()

    # OAuth + services
    creds = load_google_credentials()
    sheets, drive, gmail = build_services(creds)

    # Onglets (tolérants)
    clients_title = pick_sheet_title(
        sheets, acc_ss_id, preferred_names=("clients", "Clients")
    )
    products_title = pick_sheet_title(
        sheets, acc_ss_id, preferred_names=("produits", "Produits")
    )
    # Pour 'factures', on exige qu'il existe ou on le crée (fallback False pour forcer ce nom)
    try:
        factures_title = pick_sheet_title(
            sheets, acc_ss_id, preferred_names=("factures", "Factures"), fallback=False
        )
    except RuntimeError:
        factures_title = "factures"
    init_factures_header_if_missing(sheets, acc_ss_id, factures_title)

    # Lecture des tables
    clients_rows = read_table_by_title(sheets, acc_ss_id, clients_title, "A1:G")
    products_rows = read_table_by_title(sheets, acc_ss_id, products_title, "A1:D")

    # Recherche des entités
    client = find_client(clients_rows, args.client_id)
    # Appel à find_product sans le paramètre tva_exempt
    product = find_product(products_rows, args.product_id)

    # Numéro de facture (mensuel)
    invoice_number = get_next_invoice_number_monthly(sheets, acc_ss_id, factures_title)
    today = datetime.now().strftime("%d/%m/%Y")

    # Génération PDF
    filename = f"{invoice_number}_{slugify(client.nom)}_{slugify(client.prenom)}.pdf"
    output_path = os.path.join(os.getcwd(), filename)

    # Appel à generate_invoice_pdf sans le paramètre tva_exempt
    generate_invoice_pdf(
        output_path=output_path,
        invoice_number=invoice_number,
        date_str=today,
        practice_name=practice_name,
        practice_address=practice_address,
        practice_siret=practice_siret,
        practice_tva_number=practice_tva_number,
        # Supprimez la ligne suivante : tva_exempt=tva_exempt,
        client=client,
        product=product,
        qty=args.qty,
        notes=args.notes if args.notes else None,
    )

    # Upload Drive
    uploaded = upload_to_drive(drive, output_path, folder_id)
    drive_link = uploaded["link"]

    # Envoi email
    recipient = client.mail or ""
    if recipient:
        subject = f"Votre facture {invoice_number} - {practice_name}"
        html_body = f"""
        <p>Bonjour {client.prenom} {client.nom},</p>
        <p>Veuillez trouver ci-joint votre facture <b>{invoice_number}</b> pour la prestation :
        <br/><i>{product.libelle}</i>.</p>
        <p>Vous pouvez également consulter la facture en ligne : {drive_link}ouvrir dans Drive</a>.</p>
        <p>Bien cordialement,<br/>{practice_name}</p>
        """
        try:
            send_email_gmail(
                gmail,
                sender_email or "me",
                recipient,
                accountant_email or None,
                subject,
                html_body,
                output_path,
            )
        except Exception as e:
            print(f"❌ Erreur envoi email: {e}")
    else:
        print("⚠️ Le client n'a pas d'email. Facture non envoyée.")

    # Log factures
    montant_ht = product.prix_ht * args.qty
    montant_ttc = product.prix_ttc * args.qty
    montant_tva = montant_ttc - montant_ht  # Calculer la TVA directement

    row = [
        invoice_number,
        today,
        client.id,
        client.nom,
        client.prenom,
        product.id,
        product.libelle,
        str(args.qty),
        f"{montant_ht:.2f}",
        f"{montant_tva:.2f}",
        f"{montant_ttc:.2f}",
        drive_link,
        recipient,
    ]
    append_facture_row(sheets, acc_ss_id, factures_title, row)

    # Logs console
    print(f"✅ Facture générée: {output_path}")
    print(f"✅ Upload Drive: {drive_link}")
    if recipient:
        print(f"✅ Email envoyé à: {recipient} (CC: {accountant_email or 'aucun'})")
    print(
        f"✅ Log ajouté dans l'onglet '{factures_title}' du spreadsheet ({acc_ss_id})"
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MVP facturation (Google Sheets + Drive + Gmail) - version 'comptabilite' (un seul fichier)

- Lit le Google Sheet avec 3 onglets: clients, produits, factures
- Génère un PDF de facture (ReportLab)
- Upload le PDF dans un dossier Google Drive
- Envoie la facture par email via Gmail API
- Journalise la facture dans l'onglet 'factures'
- Numérotation: FACT-YYYYMM-#### (ex: FACT-202512-0001)

Dépendances:
  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib reportlab python-dotenv

Fichiers requis:
  credentials.json (OAuth 2.0 Desktop app)
  .env

Usage:
  python mvp_invoicing.py --client-id <ID_CLIENT> --product-id <ID_PRODUIT> --qty 1 --notes "Séance du 5/12"
"""

import os
import sys
import re
import base64
import argparse
import unicodedata
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
from reportlab.lib.styles import ParagraphStyle

# Gmail MIME
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

# Google APIs
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/gmail.send",
]


# --------------------------
# Auth / Services
# --------------------------


def load_google_credentials() -> Credentials:
    """Charge ou crée les credentials OAuth (token.json) pour Desktop app."""
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

        with open("token.json", "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return creds


def build_services(creds: Credentials):
    # cache_discovery=False => plus robuste sur certains environnements
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return sheets, drive, gmail


# --------------------------
# Utils (format / parse)
# --------------------------


def slugify(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def fmt_eur(x: float) -> str:
    # format FR: séparateur milliers espace + virgule
    return f"{x:,.2f} €".replace(",", " ").replace(".", ",")


def sanitize_pdf_text(s: str) -> str:
    """
    Évite les carrés noirs dans ReportLab en remplaçant:
    - espace insécable U+00A0
    - espace fine insécable U+202F
    par un espace normal.
    """
    if s is None:
        return ""
    return str(s).replace("\u202f", " ").replace("\u00a0", " ")


def normalize_money_display(val) -> str:
    """Affichage proche du Google Sheet (on ne normalise pas trop côté UI)."""
    if val is None:
        return ""
    return str(val).strip()


def parse_currency(val) -> float:
    """
    Parse robuste en float.
    Supporte:
      - espaces: ' ', U+00A0, U+202F
      - apostrophes (1'234.56)
      - € et autres caractères
      - formats FR/US/CH
    Règle: le séparateur décimal est celui (',' ou '.') le plus à droite.
    """
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip()
    if s == "":
        return 0.0

    s = unicodedata.normalize("NFKC", s)
    s = (
        s.replace("€", "")
        .replace(" ", "")
        .replace("\u00a0", "")  # NBSP
        .replace("\u202f", "")  # NNBSP
        .replace("’", "")
        .replace("'", "")
        .strip()
    )

    s = re.sub(r"[^\d\.,\-\+]", "", s)

    if re.fullmatch(r"[+-]?\d+", s):
        return float(s)

    if s.count(",") == 1 and s.count(".") == 0:
        return float(s.replace(",", "."))
    if s.count(".") == 1 and s.count(",") == 0:
        return float(s)

    last_comma = s.rfind(",")
    last_dot = s.rfind(".")
    if last_dot > last_comma:
        s = s.replace(",", "")
        return float(s)
    else:
        s = s.replace(".", "").replace(",", ".")
        return float(s)


# --------------------------
# Sheets helpers
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
        pass

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
    now = datetime.now()
    yyyymm = f"{now.year}{now.month:02d}"
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

    # Raw (affichage identique au sheet)
    prix_ht_raw: str
    prix_ttc_raw: str
    tva_raw: str

    # Numeric (calcul)
    prix_ht: float
    prix_ttc: float

    # TVA info
    tva_rate_for_display: float
    is_tva_exempt: bool


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


def find_product(products: List[Dict[str, str]], id_: str) -> Product:
    for p in products:
        if p.get("id") == id_:
            ht_raw = normalize_money_display(p.get("prix_ht", "0"))
            ttc_raw = normalize_money_display(p.get("prix_ttc", "0"))
            tva_raw = normalize_money_display(p.get("tva", "0%")).strip()

            ht = parse_currency(ht_raw)
            ttc_from_sheet = parse_currency(ttc_raw)

            is_tva_exempt = tva_raw in ("0", "0%", "0.0", "0.00", "0,0", "0,00")

            # taux TVA (si besoin fallback)
            tva_rate_for_display = 0.0
            if not is_tva_exempt:
                try:
                    tva_rate_for_display = (
                        parse_currency(tva_raw.replace("%", "")) / 100.0
                    )
                except Exception:
                    if ht > 0:
                        tva_rate_for_display = max(0.0, (ttc_from_sheet / ht) - 1.0)

            final_ttc = ht if is_tva_exempt else ttc_from_sheet

            return Product(
                id=p.get("id", ""),
                libelle=p.get("libelle", ""),
                prix_ht_raw=ht_raw,
                prix_ttc_raw=ttc_raw,
                tva_raw=tva_raw,
                prix_ht=ht,
                prix_ttc=final_ttc,
                tva_rate_for_display=tva_rate_for_display,
                is_tva_exempt=is_tva_exempt,
            )

    raise ValueError(f"Produit id={id_} introuvable.")


# --------------------------
# PDF
# --------------------------


def generate_invoice_pdf(
    output_path: str,
    invoice_number: str,
    date_str: str,
    practice_name: str,
    practice_address: str,
    practice_siret: str,
    practice_tva_number: Optional[str],
    client: Client,
    product: Product,
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
    style_small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9)
    elems = []

    # En-tête cabinet
    elems.append(Paragraph(sanitize_pdf_text(practice_name), style_title))

    if practice_address:
        # ✅ gère "\n" littéral dans .env
        addr = practice_address.replace("\\n", "\n")
        addr = sanitize_pdf_text(addr).replace("\n", "<br/>")
        elems.append(Paragraph(addr, style_normal))

    if practice_siret:
        elems.append(
            Paragraph(sanitize_pdf_text(f"SIRET : {practice_siret}"), style_normal)
        )
    if practice_tva_number:
        elems.append(
            Paragraph(
                sanitize_pdf_text(f"N° TVA intracom : {practice_tva_number}"),
                style_normal,
            )
        )

    if product.is_tva_exempt:
        elems.append(
            Paragraph(
                "Exonération de TVA (art. 261 du CGI – actes médicaux).", style_small
            )
        )

    elems.append(Spacer(1, 10))
    elems.append(
        Paragraph(
            f"<b>Facture n° {sanitize_pdf_text(invoice_number)}</b>", style_normal
        )
    )
    elems.append(Paragraph(f"Date : {sanitize_pdf_text(date_str)}", style_normal))
    elems.append(Spacer(1, 6))

    # Client
    client_block = f"""
    <b>Client</b><br/>
    {sanitize_pdf_text(client.prenom)} {sanitize_pdf_text(client.nom)}<br/>
    {sanitize_pdf_text(client.rue)}<br/>
    {sanitize_pdf_text(client.code_postal)} {sanitize_pdf_text(client.ville)}<br/>
    {sanitize_pdf_text(client.mail)}
    """
    elems.append(Paragraph(client_block, style_normal))
    elems.append(Spacer(1, 12))

    # Calculs
    montant_ht = product.prix_ht * qty
    montant_ttc = product.prix_ttc * qty
    montant_tva = montant_ttc - montant_ht

    # ✅ Affichage PU/TVA "raw" mais safe PDF (pas de carré noir)
    pu_ht_display = sanitize_pdf_text(product.prix_ht_raw)
    tva_display = sanitize_pdf_text(product.tva_raw)

    data = [
        ["Libellé", "Qté", "PU HT", "TVA", "Total HT", "Total TTC"],
        [
            sanitize_pdf_text(product.libelle),
            str(qty),
            pu_ht_display,
            tva_display,
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
        elems.append(
            Paragraph(f"<b>Notes :</b> {sanitize_pdf_text(notes)}", style_small)
        )
        elems.append(Spacer(1, 6))

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
    """
    Envoi mail "standard" (évite pièces jointes dupliquées selon certains clients):
    - multipart/mixed
      - multipart/alternative (plain + html)
      - attachment PDF (1 seule fois)
    """
    msg = MIMEMultipart("mixed")
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["From"] = sender or ""
    msg["Subject"] = subject

    alt = MIMEMultipart("alternative")
    alt.attach(
        MIMEText("Veuillez trouver votre facture en pièce jointe.", "plain", "utf-8")
    )
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    with open(attachment_path, "rb") as f:
        part = MIMEApplication(f.read(), _subtype="pdf")
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=os.path.basename(attachment_path),
        )
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gmail.users().messages().send(userId="me", body={"raw": raw}).execute()


# --------------------------
# CLI
# --------------------------


def main():
    load_dotenv()

    folder_id = os.getenv("GOOGLE_FOLDER_ID")
    acc_ss_id = os.getenv("ACCOUNTING_SPREADSHEET_ID")

    practice_name = os.getenv("PRACTICE_NAME", "Cabinet")
    practice_address = os.getenv("PRACTICE_ADDRESS", "")
    practice_siret = os.getenv("PRACTICE_SIRET", "")
    practice_tva_number = os.getenv("PRACTICE_TVA_NUMBER", "")

    sender_email = os.getenv("PRACTITIONER_EMAIL", "")
    accountant_email = os.getenv("COMPTABLE_EMAIL", "")

    if not folder_id or not acc_ss_id:
        print(
            "❌ .env incomplet. Renseigne GOOGLE_FOLDER_ID et ACCOUNTING_SPREADSHEET_ID."
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Génération & envoi de facture")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--notes", type=str, default="")
    args = parser.parse_args()

    creds = load_google_credentials()
    sheets, drive, gmail = build_services(creds)

    clients_title = pick_sheet_title(
        sheets, acc_ss_id, preferred_names=("clients", "Clients", "BDD client")
    )
    products_title = pick_sheet_title(
        sheets, acc_ss_id, preferred_names=("produits", "Produits")
    )
    try:
        factures_title = pick_sheet_title(
            sheets, acc_ss_id, preferred_names=("factures", "Factures"), fallback=False
        )
    except RuntimeError:
        factures_title = "factures"
    init_factures_header_if_missing(sheets, acc_ss_id, factures_title)

    clients_rows = read_table_by_title(sheets, acc_ss_id, clients_title, "A1:G")
    products_rows = read_table_by_title(
        sheets, acc_ss_id, products_title, "A1:E"
    )  # id, libellé, prix_ht, TVA, prix_ttc

    client = find_client(clients_rows, args.client_id)
    product = find_product(products_rows, args.product_id)

    invoice_number = get_next_invoice_number_monthly(sheets, acc_ss_id, factures_title)
    today = datetime.now().strftime("%d/%m/%Y")

    filename = f"{invoice_number}_{slugify(client.nom)}_{slugify(client.prenom)}.pdf"
    output_path = os.path.join(os.getcwd(), filename)

    generate_invoice_pdf(
        output_path=output_path,
        invoice_number=invoice_number,
        date_str=today,
        practice_name=practice_name,
        practice_address=practice_address,
        practice_siret=practice_siret,
        practice_tva_number=practice_tva_number,
        client=client,
        product=product,
        qty=args.qty,
        notes=args.notes if args.notes else None,
    )

    uploaded = upload_to_drive(drive, output_path, folder_id)
    drive_link = uploaded["link"]

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
        send_email_gmail(
            gmail,
            sender_email or "me",
            recipient,
            accountant_email or None,
            subject,
            html_body,
            output_path,
        )

    montant_ht = product.prix_ht * args.qty
    montant_ttc = product.prix_ttc * args.qty
    montant_tva = montant_ttc - montant_ht

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

    print(f"✅ Facture générée: {output_path}")
    print(f"✅ Upload Drive: {drive_link}")
    print(f"✅ Log ajouté dans '{factures_title}'")


if __name__ == "__main__":
    main()

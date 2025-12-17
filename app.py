#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

# Import des fonctions existantes du backend (ton mvp_invoicing.py)
from mvp_invoicing import (
    load_google_credentials,
    build_services,
    pick_sheet_title,
    read_table_by_title,
    init_factures_header_if_missing,
    find_client,
    find_product,
    get_next_invoice_number_monthly,
    generate_invoice_pdf,
    upload_to_drive,
    send_email_gmail,
    append_facture_row,
    slugify,
    fmt_eur,
)

# -----------------------
# Setup page
# -----------------------
st.set_page_config(
    page_title="Facturation psychiatre", page_icon="🧾", layout="centered"
)
st.title("🧾 Facturation")

# -----------------------
# ENV & chargement
# -----------------------
load_dotenv()
GOOGLE_FOLDER_ID = os.getenv("GOOGLE_FOLDER_ID")
ACCOUNTING_SPREADSHEET_ID = os.getenv("ACCOUNTING_SPREADSHEET_ID")

PRACTICE_NAME = os.getenv("PRACTICE_NAME", "Cabinet")
PRACTICE_ADDRESS = os.getenv("PRACTICE_ADDRESS", "")
PRACTICE_SIRET = os.getenv("PRACTICE_SIRET", "")
PRACTICE_TVA_NUMBER = os.getenv("PRACTICE_TVA_NUMBER", "")
TVA_EXEMPT = os.getenv("TVA_EXEMPT", "false").lower().strip() == "true"

SENDER_EMAIL = os.getenv("PRACTITIONER_EMAIL", "")
ACCOUNTANT_EMAIL = os.getenv("COMPTABLE_EMAIL", "")

if not GOOGLE_FOLDER_ID or not ACCOUNTING_SPREADSHEET_ID:
    st.error(
        "❌ .env incomplet. Renseigne GOOGLE_FOLDER_ID et ACCOUNTING_SPREADSHEET_ID."
    )
    st.stop()

# -----------------------
# Services & cache
# -----------------------


@st.cache_resource
def get_services():
    creds = load_google_credentials()
    return build_services(creds)


sheets, drive, gmail = get_services()


@st.cache_data(ttl=60)
def load_data():
    # Détecte les titres des onglets (tolérant)
    clients_title = pick_sheet_title(
        sheets, ACCOUNTING_SPREADSHEET_ID, preferred_names=("clients", "Clients")
    )
    products_title = pick_sheet_title(
        sheets, ACCOUNTING_SPREADSHEET_ID, preferred_names=("produits", "Produits")
    )
    try:
        factures_title = pick_sheet_title(
            sheets,
            ACCOUNTING_SPREADSHEET_ID,
            preferred_names=("factures", "Factures"),
            fallback=False,
        )
    except Exception:
        factures_title = "factures"

    # Initialise en-têtes de 'factures' si besoin
    init_factures_header_if_missing(sheets, ACCOUNTING_SPREADSHEET_ID, factures_title)

    # Lit les données
    clients_rows = read_table_by_title(
        sheets, ACCOUNTING_SPREADSHEET_ID, clients_title, "A1:G"
    )
    products_rows = read_table_by_title(
        sheets, ACCOUNTING_SPREADSHEET_ID, products_title, "A1:D"
    )

    return {
        "clients_title": clients_title,
        "products_title": products_title,
        "factures_title": factures_title,
        "clients": clients_rows,
        "products": products_rows,
    }


data = load_data()
clients = data["clients"]
products = data["products"]
factures_title = data["factures_title"]

# -----------------------
# UI – Sélection
# -----------------------

# Zones de filtre rapide (optionnelles)
col_f1, col_f2 = st.columns(2)
with col_f1:
    filter_client = st.text_input("🔎 Filtrer clients (nom/prénom/email)", "")
with col_f2:
    filter_product = st.text_input("🔎 Filtrer produits (libellé)", "")


# Prépare les options clients
def client_label(c):
    return f"{c.get('prenom', '')} {c.get('nom', '')} • {c.get('mail', '')} • [{c.get('id', '')}]"


clients_filtered = [
    c for c in clients if (filter_client.strip().lower() in client_label(c).lower())
]

if not clients_filtered:
    st.warning("Aucun client ne correspond au filtre. Affichage de tous les clients.")
    clients_filtered = clients

client_options = {client_label(c): c.get("id") for c in clients_filtered}
selected_client_label = st.selectbox("👤 Client", list(client_options.keys()))
selected_client_id = client_options[selected_client_label]


# Prépare les options produits
def product_label(p):
    prix_ht = float(p.get("prix_ht", "0").replace(",", "."))
    prix_ttc = float(p.get("prix_ttc", "0").replace(",", "."))
    return f"{p.get('libelle', '')} • HT {fmt_eur(prix_ht)} • TTC {fmt_eur(prix_ttc)} • [{p.get('id', '')}]"


products_filtered = [
    p
    for p in products
    if (filter_product.strip().lower() in p.get("libelle", "").lower())
]

if not products_filtered:
    st.warning("Aucun produit ne correspond au filtre. Affichage de tous les produits.")
    products_filtered = products

product_options = {product_label(p): p.get("id") for p in products_filtered}
selected_product_label = st.selectbox("💼 Produit", list(product_options.keys()))
selected_product_id = product_options[selected_product_label]

# Quantité + notes
col_q, col_n = st.columns([1, 2])
with col_q:
    qty = st.number_input("🔢 Quantité", min_value=1, max_value=100, value=1, step=1)
with col_n:
    notes = st.text_area("📝 Notes (optionnel)", placeholder="Séance du ...")

# -----------------------
# Aperçu des totaux
# -----------------------
client_obj = find_client(clients, selected_client_id)
product_obj = find_product(products, selected_product_id, TVA_EXEMPT)

montant_ht = product_obj.prix_ht * qty
montant_ttc = product_obj.prix_ttc * qty
montant_tva = 0.0 if TVA_EXEMPT else (montant_ttc - montant_ht)

with st.expander("📄 Aperçu de la facture (totaux)", expanded=True):
    st.write(f"**Client** : {client_obj.prenom} {client_obj.nom} — {client_obj.mail}")
    st.write(f"**Prestation** : {product_obj.libelle}")
    col_tot1, col_tot2, col_tot3 = st.columns(3)
    col_tot1.metric("Total HT", fmt_eur(montant_ht))
    col_tot2.metric("TVA", fmt_eur(montant_tva))
    col_tot3.metric("Total TTC", fmt_eur(montant_ttc))

# -----------------------
# Action – Générer & envoyer
# -----------------------
btn = st.button("🚀 Générer & envoyer la facture", type="primary")

if btn:
    with st.spinner("Génération de la facture..."):
        # Numéro de facture mensuel
        invoice_number = get_next_invoice_number_monthly(
            sheets, ACCOUNTING_SPREADSHEET_ID, factures_title
        )
        today = datetime.now().strftime("%d/%m/%Y")

        # Fichier PDF
        filename = f"{invoice_number}_{slugify(client_obj.nom)}_{slugify(client_obj.prenom)}.pdf"
        output_path = os.path.join(os.getcwd(), filename)

        # Génère PDF
        generate_invoice_pdf(
            output_path=output_path,
            invoice_number=invoice_number,
            date_str=today,
            practice_name=PRACTICE_NAME,
            practice_address=PRACTICE_ADDRESS,
            practice_siret=PRACTICE_SIRET,
            practice_tva_number=PRACTICE_TVA_NUMBER,
            tva_exempt=TVA_EXEMPT,
            client=client_obj,
            product=product_obj,
            qty=qty,
            notes=notes if notes.strip() else None,
        )

        # Upload Drive
        uploaded = upload_to_drive(drive, output_path, GOOGLE_FOLDER_ID)
        drive_link = uploaded["link"]

        # Envoi email
        recipient = client_obj.mail or ""
        send_ok = False
        if recipient:
            subject = f"Votre facture {invoice_number} - {PRACTICE_NAME}"
            html_body = f"""
            <p>Bonjour {client_obj.prenom} {client_obj.nom},</p>
            <p>Veuillez trouver ci-joint votre facture <b>{invoice_number}</b> pour la prestation :
            <br/><i>{product_obj.libelle}</i>.</p>
            <p>Vous pouvez également consulter la facture en ligne : {drive_link}ouvrir dans Drive</a>.</p>
            <p>Bien cordialement,<br/>{PRACTICE_NAME}</p>
            """
            try:
                send_email_gmail(
                    gmail,
                    SENDER_EMAIL or "me",
                    recipient,
                    ACCOUNTANT_EMAIL or None,
                    subject,
                    html_body,
                    output_path,
                )
                send_ok = True
            except Exception as e:
                st.error(f"❌ Erreur envoi email: {e}")

        # Log dans 'factures'
        row = [
            invoice_number,
            today,
            client_obj.id,
            client_obj.nom,
            client_obj.prenom,
            product_obj.id,
            product_obj.libelle,
            str(qty),
            f"{montant_ht:.2f}",
            f"{montant_tva:.2f}",
            f"{montant_ttc:.2f}",
            drive_link,
            recipient,
        ]
        try:
            append_facture_row(sheets, ACCOUNTING_SPREADSHEET_ID, factures_title, row)
        except Exception as e:
            st.error(f"❌ Erreur lors de l'enregistrement dans 'factures' : {e}")

        # Feedback UI
        st.success("✅ Facture générée et enregistrée.")
        st.write(f"- Numéro : **{invoice_number}**")
        st.write(f"- Lien Drive : {drive_link}")
        if send_ok:
            st.write(
                f"- Email envoyé à : **{recipient}** (CC: {ACCOUNTANT_EMAIL or 'aucun'})"
            )
        else:
            st.write("- Email non envoyé (pas d'adresse ou erreur).")

        # Bouton pour ouvrir le PDF local (info)
        st.download_button(
            label="⬇️ Télécharger le PDF généré",
            data=open(output_path, "rb").read(),
            file_name=os.path.basename(output_path),
            mime="application/pdf",
        )

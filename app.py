#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from invoicing import (
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
    normalize_money_display,
)

st.set_page_config(page_title="Facturation", page_icon="🧾", layout="centered")
st.title("🧾 Facturation")

# États Streamlit (anti double envoi / rerun)
if "processing" not in st.session_state:
    st.session_state.processing = False
if "last_sent_key" not in st.session_state:
    st.session_state.last_sent_key = None

# Bouton reload (utile)
colA, colB = st.columns([1, 3])
with colA:
    if st.button("🔄 Recharger"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()

# ENV
load_dotenv()
GOOGLE_FOLDER_ID = os.getenv("GOOGLE_FOLDER_ID")
ACCOUNTING_SPREADSHEET_ID = os.getenv("ACCOUNTING_SPREADSHEET_ID")

PRACTICE_NAME = os.getenv("PRACTICE_NAME", "Cabinet")
PRACTICE_ADDRESS = os.getenv("PRACTICE_ADDRESS", "")
PRACTICE_SIRET = os.getenv("PRACTICE_SIRET", "")
PRACTICE_TVA_NUMBER = os.getenv("PRACTICE_TVA_NUMBER", "")

SENDER_EMAIL = os.getenv("PRACTITIONER_EMAIL", "")
ACCOUNTANT_EMAIL = os.getenv("COMPTABLE_EMAIL", "")

if not GOOGLE_FOLDER_ID or not ACCOUNTING_SPREADSHEET_ID:
    st.error(
        "❌ .env incomplet. Renseigne GOOGLE_FOLDER_ID et ACCOUNTING_SPREADSHEET_ID."
    )
    st.stop()


@st.cache_resource
def get_services():
    creds = load_google_credentials()
    return build_services(creds)


sheets, drive, gmail = get_services()


@st.cache_data(ttl=60)
def load_data():
    clients_title = pick_sheet_title(
        sheets,
        ACCOUNTING_SPREADSHEET_ID,
        preferred_names=("BDD client", "clients", "Clients"),
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

    init_factures_header_if_missing(sheets, ACCOUNTING_SPREADSHEET_ID, factures_title)

    clients_rows = read_table_by_title(
        sheets, ACCOUNTING_SPREADSHEET_ID, clients_title, "A1:G"
    )
    products_rows = read_table_by_title(
        sheets, ACCOUNTING_SPREADSHEET_ID, products_title, "A1:E"
    )  # id, libelle, prix_ht, tva, prix_ttc

    return {
        "clients": clients_rows,
        "products": products_rows,
        "factures_title": factures_title,
    }


data = load_data()
clients = data["clients"]
products = data["products"]
factures_title = data["factures_title"]

# Filtres
col_f1, col_f2 = st.columns(2)
with col_f1:
    filter_client = st.text_input("🔎 Filtrer clients (nom/prénom/email)", "")
with col_f2:
    filter_product = st.text_input("🔎 Filtrer produits (libellé)", "")


def client_label(c):
    return f"{c.get('prenom', '')} {c.get('nom', '')} • {c.get('mail', '')}"


clients_filtered = [
    c for c in clients if filter_client.strip().lower() in client_label(c).lower()
]
if not clients_filtered:
    clients_filtered = clients

client_options = {client_label(c): c.get("id") for c in clients_filtered}
selected_client_label = st.selectbox("👤 Client", list(client_options.keys()))
selected_client_id = client_options[selected_client_label]


def product_label(p):
    ht_raw = normalize_money_display(p.get("prix_ht", ""))
    ttc_raw = normalize_money_display(p.get("prix_ttc", ""))
    tva_raw = normalize_money_display(p.get("tva", "")).strip()
    return f"{p.get('libelle', '')} • HT {ht_raw} • TVA {tva_raw} • TTC {ttc_raw}"


products_filtered = [
    p
    for p in products
    if filter_product.strip().lower() in p.get("libelle", "").lower()
]
if not products_filtered:
    products_filtered = products

product_options = {product_label(p): p.get("id") for p in products_filtered}
selected_product_label = st.selectbox("💼 Produit", list(product_options.keys()))
selected_product_id = product_options[selected_product_label]

# Formulaire (évite double envoi)
with st.form("invoice_form", clear_on_submit=False):
    col_q, col_n = st.columns([1, 2])
    with col_q:
        qty = st.number_input(
            "🔢 Quantité", min_value=1, max_value=100, value=1, step=1
        )
    with col_n:
        notes = st.text_area("📝 Notes (optionnel)", placeholder="Séance du ...")

    submitted = st.form_submit_button(
        "🚀 Générer & envoyer la facture",
        type="primary",
        disabled=st.session_state.processing,
    )

# Objet client/produit
client_obj = find_client(clients, selected_client_id)
product_obj = find_product(products, selected_product_id)

# Totaux calculés
montant_ht = product_obj.prix_ht * qty
montant_ttc = product_obj.prix_ttc * qty
montant_tva = montant_ttc - montant_ht

with st.expander("📄 Aperçu (affichage identique au Sheet)", expanded=True):
    st.write(f"**Client** : {client_obj.prenom} {client_obj.nom} — {client_obj.mail}")
    st.write(f"**Prestation** : {product_obj.libelle}")
    st.write(f"**PU HT (sheet)** : {product_obj.prix_ht_raw}")
    st.write(f"**TVA (sheet)** : {product_obj.tva_raw}")
    st.write(f"**PU TTC (sheet)** : {product_obj.prix_ttc_raw}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Total HT", fmt_eur(montant_ht))
    c2.metric("TVA (montant)", fmt_eur(montant_tva))
    c3.metric("Total TTC", fmt_eur(montant_ttc))

if submitted and not st.session_state.processing:
    st.session_state.processing = True
    try:
        with st.spinner("Génération de la facture..."):
            invoice_number = get_next_invoice_number_monthly(
                sheets, ACCOUNTING_SPREADSHEET_ID, factures_title
            )
            today = datetime.now().strftime("%d/%m/%Y")

            # clé anti-double envoi (même client+produit+qty+date+notes)
            send_key = f"{invoice_number}|{client_obj.id}|{product_obj.id}|{qty}|{notes.strip()}"

            if st.session_state.last_sent_key == send_key:
                st.warning(
                    "⚠️ Cette facture semble déjà avoir été envoyée (anti double-envoi)."
                )
                st.stop()

            filename = f"{invoice_number}_{slugify(client_obj.nom)}_{slugify(client_obj.prenom)}.pdf"
            output_path = os.path.join(os.getcwd(), filename)

            generate_invoice_pdf(
                output_path=output_path,
                invoice_number=invoice_number,
                date_str=today,
                practice_name=PRACTICE_NAME,
                practice_address=PRACTICE_ADDRESS,
                practice_siret=PRACTICE_SIRET,
                practice_tva_number=PRACTICE_TVA_NUMBER,
                client=client_obj,
                product=product_obj,
                qty=qty,
                notes=notes if notes.strip() else None,
            )

            uploaded = upload_to_drive(drive, output_path, GOOGLE_FOLDER_ID)
            drive_link = uploaded["link"]

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

            # log
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
            append_facture_row(sheets, ACCOUNTING_SPREADSHEET_ID, factures_title, row)

            # marqueur anti double envoi
            st.session_state.last_sent_key = send_key

            st.success("✅ Facture générée et enregistrée.")
            st.write(f"- Numéro : **{invoice_number}**")
            st.write(f"- Lien Drive : {drive_link}")
            if send_ok:
                st.write(
                    f"- Email envoyé à : **{recipient}** (CC: {ACCOUNTANT_EMAIL or 'aucun'})"
                )
            else:
                st.write("- Email non envoyé (pas d'adresse).")

            with open(output_path, "rb") as f:
                st.download_button(
                    label="⬇️ Télécharger le PDF généré",
                    data=f.read(),
                    file_name=os.path.basename(output_path),
                    mime="application/pdf",
                )

    except Exception as e:
        st.error(f"❌ Erreur: {e}")
    finally:
        st.session_state.processing = False

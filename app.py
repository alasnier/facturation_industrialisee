#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from invoicing import (
    append_facture_row,
    build_services,
    find_client,
    find_product,
    fmt_eur,
    generate_invoice_pdf,
    get_next_invoice_number_monthly,
    init_factures_header_if_missing,
    load_google_credentials,
    normalize_money_display,
    pick_sheet_title,
    read_table_by_title,
    send_email_gmail,
    slugify,
    upload_to_drive,
)

st.set_page_config(page_title="Facturation", page_icon="🧾", layout="centered")
st.title("🧾 Facturation")

# États Streamlit (anti double envoi / rerun)
if "processing" not in st.session_state:
    st.session_state.processing = False
if "last_sent_key" not in st.session_state:
    st.session_state.last_sent_key = None

# ENV
load_dotenv()
GOOGLE_FOLDER_ID = os.getenv("GOOGLE_FOLDER_ID")
ACCOUNTING_SPREADSHEET_ID = os.getenv("ACCOUNTING_SPREADSHEET_ID")

PRACTICE_NAME = os.getenv("PRACTICE_NAME", "Cabinet")
PRACTICE_ADDRESS = os.getenv("PRACTICE_ADDRESS", "")
PRACTICE_SIRET = os.getenv("PRACTICE_SIRET", "")
PRACTICE_TVA_NUMBER = os.getenv("PRACTICE_TVA_NUMBER", "")
PRACTICE_ADELI_NUMBER = os.getenv("PRACTICE_ADELI_NUMBER", "")

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

# Lien vers l'historique des factures (Google Sheet)
sheet_url = f"https://docs.google.com/spreadsheets/d/{ACCOUNTING_SPREADSHEET_ID}"
st.markdown(
    f'<a href="{sheet_url}" target="_blank" style="text-decoration: none;">'
    f'<button style="background-color: #4CAF50; color: white; padding: 10px 20px; '
    f'border: none; border-radius: 5px; cursor: pointer; font-size: 16px;">'
    f'📋 Voir l\'historique des factures'
    f'</button></a>',
    unsafe_allow_html=True
)

st.markdown("---")

# ============================================
# FORMULAIRE : NOUVELLE FACTURE
# ============================================
st.subheader("✨ Créer une nouvelle facture")

# Filtre client uniquement
filter_client = st.text_input("🔎 Filtrer clients (nom/prénom/email)", "")


def client_label(c):
    return f"{c.get('prenom', '')} {c.get('nom', '')} • {c.get('mail', '')}"


clients_filtered = [
    c for c in clients if filter_client.strip().lower() in client_label(c).lower()
]
if not clients_filtered:
    clients_filtered = clients

# NOUVEAU v1.2 : Auto-sélection si un seul client filtré
if len(clients_filtered) == 1 and filter_client.strip():
    # Auto-sélection
    selected_client_id = clients_filtered[0]["id"]
    st.success(f"✅ Client sélectionné : {client_label(clients_filtered[0])}")
else:
    # Menu déroulant classique
    client_options = {client_label(c): c.get("id") for c in clients_filtered}
    selected_client_label = st.selectbox("👤 Client", list(client_options.keys()))
    selected_client_id = client_options[selected_client_label]


def product_label(p):
    ht_raw = normalize_money_display(p.get("prix_ht", ""))
    ttc_raw = normalize_money_display(p.get("prix_ttc", ""))
    tva_raw = normalize_money_display(p.get("tva", "")).strip()
    return f"{p.get('libelle', '')} • HT {ht_raw} • TVA {tva_raw} • TTC {ttc_raw}"


product_options = {product_label(p): p.get("id") for p in products}
selected_product_label = st.selectbox("💼 Produit", list(product_options.keys()))
selected_product_id = product_options[selected_product_label]

# Objet client/produit (récupération anticipée pour détecter produit variable)
client_obj = find_client(clients, selected_client_id)
product_obj = find_product(products, selected_product_id)

# NOUVEAU v1.2 : Détecter si produit à montant variable
is_variable_product = (
    product_obj.id == "PAP variable" or 
    "STAGE PAP" in product_obj.libelle.upper()
)

# Formulaire (évite double envoi)
with st.form("invoice_form", clear_on_submit=False):
    col_q, col_var = st.columns([1, 2])
    
    with col_q:
        qty = st.number_input(
            "🔢 Quantité", min_value=1, max_value=100, value=1, step=1
        )
    
    # NOUVEAU v1.2 : Saisie manuelle montant HT pour produit variable
    with col_var:
        if is_variable_product:
            montant_ht_manuel = st.number_input(
                "💶 Montant HT (saisie manuelle)",
                min_value=0.0,
                value=0.0,
                step=10.0,
                help="Pour le produit STAGE PAP, saisissez le montant HT manuellement. La TVA (20%) sera calculée automatiquement."
            )
        else:
            st.empty()  # Placeholder vide si pas produit variable
    
    notes = st.text_area("📝 Notes (dates séances/formations)", placeholder="Séance du ...")

    # Checkboxes envoi
    st.markdown("---")
    st.markdown("**📧 Options d'envoi**")
    col_send1, col_send2 = st.columns(2)
    with col_send1:
        send_to_client = st.checkbox("Envoyer au client", value=True)
    with col_send2:
        send_to_accountant = st.checkbox("Envoyer au comptable", value=True)

    submitted = st.form_submit_button(
        "🚀 Générer & envoyer la facture",
        type="primary",
        disabled=st.session_state.processing,
    )

# Calcul des montants (selon produit variable ou non)
if is_variable_product:
    # Produit variable : utiliser montant HT manuel
    montant_ht = montant_ht_manuel * qty
    montant_tva = montant_ht * 0.20  # TVA 20%
    montant_ttc = montant_ht + montant_tva
else:
    # Produit normal : utiliser prix du produit
    montant_ht = product_obj.prix_ht * qty
    montant_ttc = product_obj.prix_ttc * qty
    montant_tva = montant_ttc - montant_ht

with st.expander("📄 Aperçu (affichage identique au Sheet)", expanded=True):
    st.write(f"**Client** : {client_obj.prenom} {client_obj.nom} — {client_obj.mail}")
    st.write(f"**Prestation** : {product_obj.libelle}")
    
    if is_variable_product:
        st.info("⚠️ Produit à montant variable - Calcul avec montant HT saisi manuellement + TVA 20%")
    else:
        st.write(f"**PU HT (sheet)** : {product_obj.prix_ht_raw}")
        st.write(f"**TVA (sheet)** : {product_obj.tva_raw}")
        st.write(f"**PU TTC (sheet)** : {product_obj.prix_ttc_raw}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Total HT", fmt_eur(montant_ht))
    c2.metric("TVA (montant)", fmt_eur(montant_tva))
    c3.metric("Total TTC", fmt_eur(montant_ttc))

if submitted and not st.session_state.processing:
    # NOUVEAU v1.2 : Garde-fou notes obligatoires
    if not notes or not notes.strip():
        st.error("❌ **Impossible d'envoyer la facture sans notes.**")
        st.warning("⚠️ Veuillez ajouter une note avec les dates des séances/formations avant de générer la facture.")
        st.stop()
    
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

            # NOUVEAU v1.2 : Passer le numéro ADELI et les montants pour produit variable
            generate_invoice_pdf(
                output_path=output_path,
                invoice_number=invoice_number,
                date_str=today,
                practice_name=PRACTICE_NAME,
                practice_address=PRACTICE_ADDRESS,
                practice_siret=PRACTICE_SIRET,
                practice_tva_number=PRACTICE_TVA_NUMBER,
                practice_adeli_number=PRACTICE_ADELI_NUMBER,
                client=client_obj,
                product=product_obj,
                qty=qty,
                notes=notes if notes.strip() else None,
                # Nouveaux paramètres pour produit variable
                montant_ht_override=montant_ht if is_variable_product else None,
                montant_tva_override=montant_tva if is_variable_product else None,
                montant_ttc_override=montant_ttc if is_variable_product else None,
            )

            uploaded = upload_to_drive(drive, output_path, GOOGLE_FOLDER_ID)
            drive_link = uploaded["link"]

            # Envoi conditionnel selon checkboxes
            recipient = client_obj.mail or ""
            send_ok_client = False
            send_ok_accountant = False

            if send_to_client and recipient:
                subject = f"Votre facture {invoice_number} - {PRACTICE_NAME}"
                html_body = f"""
                <p>Bonjour {client_obj.prenom} {client_obj.nom},</p>
                <p>Veuillez trouver ci-joint votre facture <b>{invoice_number}</b> pour la prestation :
                <br/><i>{product_obj.libelle}</i>.</p>
                <p>Vous pouvez également consulter la facture en ligne : <a href="{drive_link}">ouvrir dans Drive</a>.</p>
                <p>Bien cordialement,<br/>{PRACTICE_NAME}</p>
                """

                # Envoi au client avec ou sans CC comptable selon checkbox
                cc_email = ACCOUNTANT_EMAIL if send_to_accountant else None

                send_email_gmail(
                    gmail,
                    SENDER_EMAIL or "me",
                    recipient,
                    cc_email,
                    subject,
                    html_body,
                    output_path,
                )
                send_ok_client = True
                if send_to_accountant:
                    send_ok_accountant = True

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

            # Messages conditionnels selon checkboxes
            if send_ok_client:
                if send_ok_accountant:
                    st.write(
                        f"- Email envoyé à : **{recipient}** (CC: {ACCOUNTANT_EMAIL})"
                    )
                else:
                    st.write(f"- Email envoyé à : **{recipient}**")
            else:
                if send_to_client and not recipient:
                    st.warning("⚠️ Email non envoyé au client (pas d'adresse email).")
                else:
                    st.info("ℹ️ Email non envoyé au client (option désactivée).")

            if not send_to_accountant:
                st.info("ℹ️ Email non envoyé au comptable (option désactivée).")

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
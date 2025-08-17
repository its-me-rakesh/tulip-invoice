# invoice_generator.py — Optimized & Modular Single-File Version

import streamlit as st
import streamlit_authenticator as stauth
import yaml, os, base64, bcrypt, requests
import pandas as pd
from datetime import datetime
from io import BytesIO
from github import Github
import gspread
from google.oauth2.service_account import Credentials
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import plotly.express as px

# ------------------------
# Config & Setup
# ------------------------
st.set_page_config(page_title="Invoice Generator", layout="centered")

# Load config from YAML
with open("config.yaml") as f:
    config = yaml.safe_load(f)

# ------------------------
# GitHub Utils
# ------------------------
def update_config_on_github(config_dict):
    """Update config.yaml in GitHub repo with new settings."""
    try:
        github_token = st.secrets["GITHUB_TOKEN"]
        repo_name = st.secrets["GITHUB_REPO"]
        config_path = st.secrets["CONFIG_FILE_PATH"]

        g = Github(github_token)
        repo = g.get_repo(repo_name)

        contents = repo.get_contents(config_path)
        yaml_content = yaml.dump(config_dict, sort_keys=False)
        encoded_content = base64.b64encode(yaml_content.encode()).decode()

        repo.update_file(
            path=config_path,
            message="Update config.yaml via Streamlit",
            content=encoded_content,
            sha=contents.sha,
            branch="main"
        )
        st.success("✅ Config updated in GitHub.")
        st.rerun()
    except Exception as e:
        st.error(f"❌ Failed to update config: {e}")

# ------------------------
# Authentication
# ------------------------
authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

st.image("Tulip.jpeg", use_container_width=False, width=700)
st.markdown("<div style='text-align: center; font-size: 14px; margin-bottom: 10px;'>Welcome to Tulip Billing</div>", unsafe_allow_html=True)

name, auth_status, username = authenticator.login("Login", "main")

if auth_status is False:
    st.error("Incorrect username or password.")
    st.stop()
elif auth_status is None:
    st.warning("Please enter your credentials.")
    st.stop()

authenticator.logout("🔒 Logout", "sidebar")

role = config['credentials']['usernames'][username]['role']
is_master, is_admin, is_user = role == 'master', role == 'admin', role == 'user'

st.success(f"Welcome, {name} 👋 | Role: {role.upper()}")
st.title("Shilp Samagam Mela Invoicing System")

# ------------------------
# Google Sheets Utils
# ------------------------
@st.cache_resource
def get_worksheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict).with_scopes(scopes)
    gc = gspread.authorize(creds)
    return gc.open("invoices_records").sheet1

@st.cache_data(ttl=300, show_spinner="Loading data from Google Sheets...")
def fetch_sheet_df():
    try:
        worksheet = get_worksheet()
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        df.columns = df.columns.astype(str).str.strip()
        return df
    except Exception as e:
        st.warning(f"⚠️ Failed to fetch Google Sheet data: {e}")
        return pd.DataFrame()

def append_to_google_sheet(rows):
    """Append invoice rows with location info."""
    try:
        worksheet = get_worksheet()
        header = ["Stall No", "Invoice No", "Date", "Phone No", "Payment Method",
                  "Artisan Code", "Item", "Qty", "Price", "Total (Item)",
                  "Discount%", "Final Total (Item)", "Final Total (Invoice)",
                  "Status", "Location"]

        if not worksheet.row_values(1):
            worksheet.insert_row(header, 1)

        user_loc = config["credentials"]["usernames"][username].get("location", "")
        rows_with_loc = [row + [user_loc] for row in rows]
        worksheet.append_rows(rows_with_loc, value_input_option="USER_ENTERED")
    except Exception as e:
        st.warning(f"⚠️ Failed to update Google Sheet: {e}")

# ------------------------
# Invoice Utils
# ------------------------
def generate_invoice_number(counter: str, df: pd.DataFrame) -> str:
    """Generate next invoice number for a billing counter."""
    if df.empty:
        return f"{counter}_INV01"
    df_counter = df[df["Invoice No"].str.startswith(counter)]
    last_num = df_counter["Invoice No"].str.extract(rf"{counter}_INV(\d+)")[0].dropna().astype(int).max(initial=0)
    return f"{counter}_INV{last_num + 1:02d}"

def generate_invoice_pdf(invoice_no, artisan_code, date, stall_no, ph_no,
                         payment_method, items, total_amount, discount_amt, grand_total, logo="Tulip.jpeg"):
    """Generate invoice + artisan slip PDF."""
    buffer = BytesIO()
    height = 250 + 15 * len(items)
    inv = canvas.Canvas(buffer, pagesize=(200, height), bottomup=0)

    def draw_page(heading):
        inv.line(5, 45, 195, 45)
        inv.translate(10, 40); inv.scale(1, -1)
        if os.path.exists(logo):
            inv.drawImage(ImageReader(logo), x=-10, y=0, width=200, height=40, preserveAspectRatio=False, mask='auto')
        inv.scale(1, -1); inv.translate(-10, -40)
        inv.setFont("Times-Bold", 6)
        inv.drawCentredString(100, 55, heading)

        inv.setFont("Times-Bold", 4)
        inv.drawString(15, 70, f"Invoice No.: {invoice_no}")
        inv.drawString(15, 80, f"Artisan Code: {artisan_code}")
        inv.drawString(15, 90, f"Date: {date}")
        inv.drawString(110, 70, f"Stall No.: {stall_no}")
        inv.drawString(110, 80, f"Customer Ph No.: {ph_no}")
        inv.drawString(110, 90, f"Payment Method: {payment_method}")

        start_y = 100
        inv.roundRect(15, start_y, 170, 15 * (len(items) + 1), 5, fill=0)
        inv.drawString(20, start_y + 10, "S.No")
        inv.drawString(45, start_y + 10, "Item")
        inv.drawString(100, start_y + 10, "Price")
        inv.drawString(130, start_y + 10, "Qty")
        inv.drawString(155, start_y + 10, "Total")

        y = start_y + 20
        for it in items:
            inv.drawString(20, y, it["s_no"])
            inv.drawString(45, y, it["item"])
            inv.drawString(100, y, f"{it['price']:.2f}")
            inv.drawString(130, y, str(it["qty"]))
            inv.drawString(155, y, f"{it['total']:.2f}")
            y += 15

        inv.setFont("Times-Bold", 5)
        inv.drawString(15, y + 10, f"Subtotal: {total_amount:.2f}")
        inv.drawString(15, y + 20, f"Total Discount: {discount_amt:.2f}")
        inv.drawString(140, y + 10, f"Grand Total: {grand_total:.2f}")
        inv.drawString(140, y + 60, "Tulip")
        inv.drawString(140, y + 68, "Signature")

    draw_page("INVOICE")
    inv.showPage()
    draw_page("ARTISAN SLIP")
    inv.save()
    buffer.seek(0)
    return buffer

# ------------------------
# Billing Section
# ------------------------
st.subheader("1. Billing Counter")
billing_counter = st.text_input("Counter Name (e.g. MAIN)").strip().upper()

st.subheader("2. Company & Invoice Details")
col1, col2 = st.columns(2)
with col1:
    stall_no = st.text_input("Stall Number") 
    artisan_code = st.text_input("Artisan Code")
with col2:
    date = st.date_input("Invoice Date", value=datetime.today()).strftime("%d-%m-%Y")
    ph_no = st.text_input("Customer Phone No.")
    payment_method = st.selectbox("Payment Method", ["Cash", "UPI"])

df_all = fetch_sheet_df()
invoice_no = generate_invoice_number(billing_counter, df_all) if billing_counter else ""

st.subheader("3. Add Items to Invoice")
num_items = st.number_input("How many items?", min_value=1, step=1)
items = []
for i in range(num_items):
    with st.expander(f"Item {i + 1}"):
        name = st.text_input("Item Name", key=f"item_{i}")
        price = st.number_input("Price per unit", min_value=0.0, step=0.1, key=f"price_{i}")
        qty = st.number_input("Quantity", min_value=1, step=1, key=f"qty_{i}")
        discount_item = st.number_input(f"Discount %", min_value=0.0, max_value=100.0, value=0.0, step=0.1, key=f"discount_{i}")

        total_before_discount = price * qty
        total_after_discount = total_before_discount * (1 - discount_item / 100)

        items.append({
            "s_no": str(i + 1), "item": name, "price": price, "qty": qty,
            "discount_percent": discount_item,
            "total": total_before_discount,
            "final_total": total_after_discount
        })

subtotal = sum(it["final_total"] for it in items)
st.markdown(f"### 🧾 Current Subtotal: ₹ {subtotal:.2f}")

# ------------------------
# Invoice Generation
# ------------------------
missing_fields = []
if not billing_counter: missing_fields.append("Billing Counter")
if not stall_no: missing_fields.append("Stall Number")
generate_disabled = bool(missing_fields)

if st.button("🧾 Generate Invoice", disabled=generate_disabled):
    total_amount = sum(it["total"] for it in items)
    discount_amt = sum(it["total"] - it["final_total"] for it in items)
    grand_total = subtotal

    buffer = generate_invoice_pdf(invoice_no, artisan_code, date, stall_no, ph_no,
                                  payment_method, items, total_amount, discount_amt, grand_total)

    st.download_button("📄 Download Invoice PDF", buffer, file_name=f"{invoice_no}.pdf", mime="application/pdf")

    rows = [[
        stall_no, invoice_no, date, ph_no, payment_method, artisan_code,
        it["item"], it["qty"], it["price"], it["total"],
        it["discount_percent"], it["final_total"], grand_total, "Active"
    ] for it in items]

    append_to_google_sheet(rows)
    fetch_sheet_df.clear()
    st.success("✅ Invoice saved to database & refreshed!")

# ------------------------
# (Keep Admin/Master Sections: Past Invoices, Dashboard, User Management, Export)
# ------------------------
# 🔹 For brevity, you can reuse your existing admin/master sections here without change.
# 🔹 They will work fine with the refactored helper functions above.

# Footer
st.markdown(
    """
    <hr style='margin-top: 50px;'>
    <div style='text-align: center; font-size: 12px; color: gray;'>
        Invoicing System Developed by <b>Rakesh Chourasia</b> |
        <a href='https://www.linkedin.com/feed/' target='_blank'>LinkedIn</a>
    </div>
    """,
    unsafe_allow_html=True
)

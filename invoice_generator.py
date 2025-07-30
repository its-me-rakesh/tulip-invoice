# invoice_generator.py — Modular Role-Based Invoice System

import streamlit as st
import streamlit_authenticator as stauth
import yaml
import pandas as pd
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from datetime import datetime
from io import BytesIO
import gspread
from google.auth.exceptions import GoogleAuthError
import plotly.express as px
import os
import bcrypt

# ------------------------
# Authentication
# ------------------------
with open("config.yaml") as file:
    config = yaml.safe_load(file)

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


role = config['credentials']['usernames'][username]['role']
is_master = role == 'master'
is_admin = role == 'admin'
is_user = role == 'user'

st.set_page_config(page_title="Invoice Generator", layout="centered")
st.success(f"Welcome, {name} 👋 | Role: {role.upper()}")
st.title("Shilp Samagam Mela Invoicing System")

# ------------------------
# Google Sheet Utils
# ------------------------
from google.oauth2.service_account import Credentials
import gspread

def get_google_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    
    creds_dict = dict(st.secrets["gcp_service_account"])
    base_creds = Credentials.from_service_account_info(creds_dict)
    scoped_creds = base_creds.with_scopes(scopes)

    gc = gspread.authorize(scoped_creds)
    sh = gc.open("invoices_records")  # must match sheet name
    return sh.sheet1


@st.cache_data(ttl=300, show_spinner="Loading data from Google Sheets...")
def fetch_sheet_df():
    try:
        worksheet = get_google_sheet()
        data = worksheet.get_all_records()
        return pd.DataFrame(data)
    except Exception as e:
        st.warning(f"⚠️ Failed to fetch Google Sheet data: {e}")
        return pd.DataFrame()

def append_to_google_sheet(rows):
    try:
        worksheet = get_google_sheet()
        header = ["Stall No", "Invoice No", "Date", "Phone No", "Item", "Qty", "Price", "Total (Item)", "Final Total (Item)", "Discount%", "Final Total (Invoice)"]
        if not worksheet.row_values(1):
            worksheet.insert_row(header, 1)
        worksheet.append_rows(rows, value_input_option="USER_ENTERED")
    except Exception as e:
        st.warning(f"⚠️ Failed to update Google Sheet: {e}")

# ------------------------
# Invoice Creation
# ------------------------
st.subheader("1. Billing Counter")
billing_counter = st.text_input("Counter Name (e.g. MAIN)").strip().upper()

st.subheader("2. Company & Invoice Details")
col1, col2 = st.columns(2)
with col1:
    stall_no = st.text_input("Stall Number")
with col2:
    date = st.date_input("Invoice Date", value=datetime.today()).strftime("%d-%m-%Y")
    ph_no = st.text_input("Customer Phone No.")

invoice_no = ""
inv_numeric = 1
all_df = fetch_sheet_df()
if billing_counter and not all_df.empty:
    df_counter = all_df[all_df["Invoice No"].str.startswith(billing_counter)]
    if not df_counter.empty:
        last = df_counter["Invoice No"].str.extract(rf"{billing_counter}_INV(\d+)")[0].dropna().astype(int).max()
        inv_numeric = last + 1
invoice_no = f"{billing_counter}_INV{inv_numeric:02d}"

st.subheader("3. Add Items to Invoice")
num_items = st.number_input("How many items?", min_value=1, step=1)
items = []
for i in range(num_items):
    with st.expander(f"Item {i + 1}"):
        name = st.text_input(f"Item Name {i + 1}", key=f"item_{i}")
        price = st.number_input(f"Price per unit {i + 1}", key=f"price_{i}")
        qty = st.number_input(f"Quantity {i + 1}", min_value=1, step=1, key=f"qty_{i}")
        items.append({"s_no": str(i + 1), "item": name, "price": price, "qty": qty, "total": price * qty})

discount_percent = st.number_input("Discount Percentage", min_value=0.0, max_value=100.0, value=0.0)

def draw_page(heading):
    inv.line(5, 45, 195, 45)
    inv.translate(10, 40)
    inv.scale(1, -1)

    logo_path = os.path.join(os.path.dirname(__file__), "Tulip.jpeg")
    if not os.path.exists(logo_path):
        raise FileNotFoundError("Logo 'Tulip.jpeg' not found.")
    inv.drawImage(ImageReader(logo_path), 110, 0, width=70, height=25)
    inv.scale(1, -1)
    inv.translate(-10, -40)

    inv.setFont("Times-Bold", 7)
    inv.drawString(10, 20, "Tulip")
    inv.setFont("Times-Bold", 4)
    inv.drawString(10, 30, "5th Floor, NCUI Building 3, August Kranti Marg,")
    inv.drawString(10, 35, "Siri Institutional Area, New Delhi, 110016")
    inv.setFont("Times-Bold", 6)
    inv.drawCentredString(100, 55, heading)

    inv.setFont("Times-Bold", 4)
    inv.drawString(15, 70, f"Stall No.: {stall_no}")
    inv.drawString(15, 80, f"Invoice No.: {invoice_no}")
    inv.drawString(15, 90, f"Date: {date}")
    inv.drawString(15, 100, f"Customer Ph No.: {ph_no}")

    start_y = 108
    inv.roundRect(15, start_y, 170, 15 * (len(items) + 1), 5, fill=0)
    inv.setFont("Times-Bold", 4)
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
    inv.drawString(120, y + 10, f"Subtotal: {total_amount:.2f}")
    inv.drawString(120, y + 20, f"Discount ({discount_percent}%): {discount_amt:.2f}")
    inv.drawString(120, y + 35, f"Grand Total: {grand_total:.2f}")
    inv.drawString(120, y + 60, "Tulip")
    inv.drawString(120, y + 68, "Signature")

if st.button("🧾 Generate Invoice") and billing_counter and invoice_no:
    total_amount = sum(it["total"] for it in items)
    discount_amt = total_amount * discount_percent / 100
    grand_total = total_amount - discount_amt

    buffer = BytesIO()
    height = 250 + 15 * len(items)
    inv = canvas.Canvas(buffer, pagesize=(200, height), bottomup=0)
    draw_page("INVOICE")
    inv.showPage()
    draw_page("ARTISIAN SLIP")
    inv.save()
    buffer.seek(0)

    st.download_button("📄 Download Invoice PDF", buffer, file_name=f"{invoice_no}.pdf", mime="application/pdf")

    rows = [[
        stall_no, invoice_no, date, ph_no,
        it["item"], it["qty"], it["price"], it["total"],
        it["total"] * (1 - discount_percent / 100),
        discount_percent, grand_total
    ] for it in items]

    append_to_google_sheet(rows)

    # Clear cached sheet data so the new invoice is included immediately
    fetch_sheet_df.clear()

    st.success("✅ Invoice saved to Google Sheet and data refreshed!")



# ------------------------
# Past Invoices Viewer (admin/master only)
# ------------------------
if is_admin or is_master:
    st.subheader("📚 Previous Invoice Records")
    with st.expander("Show all past invoice entries"):
        df = fetch_sheet_df()
        if not df.empty:
            st.dataframe(df)
            invoice_ids = df["Invoice No"].unique()
            selected_invoice = st.selectbox("🧾 Reprint Invoice", invoice_ids)
            selected_df = df[df["Invoice No"] == selected_invoice]

            if st.button("🖨️ Generate PDF for Selected"):
                invoice_items = selected_df.to_dict(orient="records")
                items = [{
                    "s_no": str(i + 1),
                    "item": r["Item"],
                    "price": r["Price"],
                    "qty": r["Qty"],
                    "total": r["Total (Item)"]
                } for i, r in enumerate(invoice_items)]

                stall_no = invoice_items[0]["Stall No"]
                invoice_no = invoice_items[0]["Invoice No"]
                date = invoice_items[0]["Date"]
                ph_no = invoice_items[0]["Phone No"]
                discount_percent = invoice_items[0]["Discount%"]
                total_amount = sum(it["total"] for it in items)
                discount_amt = total_amount * discount_percent / 100
                grand_total = invoice_items[0]["Final Total (Invoice)"]

                buffer = BytesIO()
                inv = canvas.Canvas(buffer, pagesize=(200, 250 + 15 * len(items)), bottomup=0)
                draw_page("INVOICE")
                inv.showPage()
                draw_page("ARTISIAN SLIP")
                inv.save()
                buffer.seek(0)
                st.download_button("📥 Download Re-Generated PDF", buffer, file_name=f"{invoice_no}_copy.pdf")
        else:
            st.info("No invoice records found.")

# ------------------------
# Sales Dashboard (admin/master only)
# ------------------------
if is_admin or is_master:
    st.subheader("📊 Sales Dashboard")
    with st.expander("View Sales Analytics"):
        df = fetch_sheet_df()
        if not df.empty:
            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")

            st.markdown("### Summary Stats")
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Revenue", f"₹{df['Final Total (Item)'].sum():,.2f}")
            col2.metric("Total Items Sold", int(df["Qty"].sum()))
            col3.metric("Total Invoices", df["Invoice No"].nunique())

            st.markdown("### 📈 Revenue Over Time")
            time_view = st.selectbox("Select View", ["Date", "Month"])

            if time_view == "Date":
                daily_df = df.groupby("Date")["Final Total (Item)"].sum().reset_index()
                fig = px.bar(daily_df, x="Date", y="Final Total (Item)", title="Revenue by Date")
            else:
                df["Month"] = df["Date"].dt.to_period("M").astype(str)
                monthly_df = df.groupby("Month")["Final Total (Item)"].sum().reset_index()
                fig = px.bar(monthly_df, x="Month", y="Final Total (Item)", title="Revenue by Month")

            st.plotly_chart(fig, use_container_width=True)


            st.plotly_chart(px.bar(df.groupby("Item")["Qty"].sum().sort_values(ascending=False).head(10).reset_index(), x="Item", y="Qty", title="Top-Selling Items"), use_container_width=True)
            st.plotly_chart(px.bar(df.groupby("Stall No")["Final Total (Item)"].sum().sort_values(ascending=False).reset_index(), x="Stall No", y="Final Total (Item)", title="Stall-wise Revenue"), use_container_width=True)
            
            disc_df = df[df["Discount%"] > 0]
            if not disc_df.empty:
                st.plotly_chart(px.histogram(disc_df, x="Discount%", nbins=20, title="Distribution of Discounts"), use_container_width=True)

            rev_items = df.groupby("Item")["Final Total (Item)"].sum().sort_values(ascending=False).reset_index()
            st.plotly_chart(px.pie(rev_items.head(10), values="Final Total (Item)", names="Item", title="Revenue Share by Item"), use_container_width=True)
        else:
            st.info("No sales data found.")

# ------------------------
# User Management (master only)
# ------------------------
import streamlit_authenticator as stauth

if is_master:
    st.subheader("👤 User Management")

    st.markdown("### 👥 Existing Users")
    user_data = [
        {
            "Username": uname,
            "Full Name": details.get("name", ""),
            "Role": details.get("role", ""),
        }
        for uname, details in config['credentials']['usernames'].items()
    ]
    st.dataframe(pd.DataFrame(user_data))

    st.markdown("---")
    st.markdown("### 🔐 Reset or Change User Password")

    with st.form("reset_password_form"):
        existing_users = list(config['credentials']['usernames'].keys())
        selected_user = st.selectbox("Select User", existing_users)

        selected_role = config['credentials']['usernames'][selected_user]['role']
        current_pass_input = ""
        if selected_role == "master":
            current_pass_input = st.text_input("Enter Current Password (required for master user)", type="password")

        new_pass = st.text_input("Enter New Password", type="password")
        confirm_pass = st.text_input("Confirm New Password", type="password")
        reset_btn = st.form_submit_button("Update Password")

        if reset_btn:
            if not new_pass or not confirm_pass:
                st.error("❗ Both password fields are required.")
            elif new_pass != confirm_pass:
                st.error("❗ Passwords do not match.")
            elif len(new_pass) < 6:
                st.error("❗ Password must be at least 6 characters.")
            elif selected_role == "master":
                # Verify current password using bcrypt
                stored_hash = config['credentials']['usernames'][selected_user]['password']
                if not bcrypt.checkpw(current_pass_input.encode(), stored_hash.encode()):
                    st.error("🚫 Incorrect current password.")
                else:
                    hashed_pass = stauth.Hasher([new_pass]).generate()[0]
                    config['credentials']['usernames'][selected_user]['password'] = hashed_pass
                    with open("config.yaml", "w") as f:
                        yaml.dump(config, f)
                    st.success(f"✅ Password for master user '{selected_user}' has been updated.")
            else:
                # Non-master users — no current password required
                hashed_pass = stauth.Hasher([new_pass]).generate()[0]
                config['credentials']['usernames'][selected_user]['password'] = hashed_pass
                with open("config.yaml", "w") as f:
                    yaml.dump(config, f)
                st.success(f"✅ Password for user '{selected_user}' has been updated.")

    st.markdown("---")
    st.markdown("### ➕ Create New User")

    with st.form("add_user_form"):
        new_username = st.text_input("Username").strip()
        new_name = st.text_input("Full Name").strip()
        new_password = st.text_input("Password", type="password")
        new_role = st.selectbox("Assign Role", ["admin", "user"])
        create_btn = st.form_submit_button("Create User")

        if create_btn:
            if not new_username or not new_password:
                st.error("❗ Username and Password cannot be empty.")
            elif new_username in config['credentials']['usernames']:
                st.error("🚫 Username already exists.")
            elif len(new_password) < 6:
                st.error("🔐 Password must be at least 6 characters.")
            else:
                hashed_password = stauth.Hasher([new_password]).generate()[0]
                config['credentials']['usernames'][new_username] = {
                    "name": new_name,
                    "password": hashed_password,
                    "role": new_role
                }
                with open("config.yaml", "w") as f:
                    yaml.dump(config, f)
                st.success(f"✅ User '{new_username}' with role '{new_role}' created successfully.")

#Footer
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


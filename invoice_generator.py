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
    extracted = df_counter["Invoice No"].str.extract(rf"{counter}_INV(\d+)")[0].dropna()

    if extracted.empty:
        last_num = 0
    else:
        last_num = extracted.astype(int).max()

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
# --- mandatory validation ---
missing_fields = []
if not billing_counter:
    missing_fields.append("Billing Counter")
if not stall_no:
    missing_fields.append("Stall Number")

if missing_fields:
    st.error(f"Please fill required field(s): {', '.join(missing_fields)} to enable invoice generation.")
    generate_disabled = True
else:
    generate_disabled = False

# --- invoice generation trigger ---
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
# Past Invoices Viewer (admin/master only)
# ------------------------
if is_admin or is_master:
    st.subheader("📚 Previous Invoice Records")
    with st.expander("Show all past invoice entries"):
        fetch_sheet_df.clear()
        df = fetch_sheet_df()

        if not df.empty:
            st.dataframe(df)
            invoice_ids = df["Invoice No"].unique()
            selected_invoice = st.selectbox("🧾 Reprint Invoice", invoice_ids)
            selected_df = df[df["Invoice No"] == selected_invoice]

            invoice_status = selected_df["Status"].iloc[0] if not selected_df.empty else "Active"

            if invoice_status == "Active":
                if st.button("❌ Cancel This Invoice"):
                    worksheet = get_worksheet()
                    all_data = worksheet.get_all_records()
                    df_all = pd.DataFrame(all_data)
                    for idx, _ in df_all[df_all["Invoice No"] == selected_invoice].iterrows():
                        worksheet.update_cell(idx + 2, df_all.columns.get_loc("Status") + 1, "Cancelled")
                    fetch_sheet_df.clear()
                    st.success(f"🛑 Invoice {selected_invoice} marked as Cancelled.")
            else:
                if st.button("↩️ Restore This Invoice"):
                    worksheet = get_worksheet()
                    all_data = worksheet.get_all_records()
                    df_all = pd.DataFrame(all_data)
                    for idx, _ in df_all[df_all["Invoice No"] == selected_invoice].iterrows():
                        worksheet.update_cell(idx + 2, df_all.columns.get_loc("Status") + 1, "Active")
                    fetch_sheet_df.clear()
                    st.success(f"✅ Invoice {selected_invoice} restored.")

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
                artisan_code = invoice_items[0].get("Artisan Code", "")
                payment_method = invoice_items[0].get("Payment Method", "Cash")
                discount_percent = invoice_items[0]["Discount%"]
                total_amount = sum(it["total"] for it in items)
                discount_amt = total_amount * discount_percent / 100
                grand_total = invoice_items[0]["Final Total (Invoice)"]

                buffer = generate_invoice_pdf(
                    invoice_no, artisan_code, date, stall_no, ph_no,
                    payment_method, items, total_amount, discount_amt, grand_total
                )
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

            st.plotly_chart(px.bar(df.groupby("Date")["Final Total (Item)"].sum().reset_index(),
                                   x="Date", y="Final Total (Item)",
                                   title="Revenue Over Time"), use_container_width=True)

            st.plotly_chart(px.bar(df.groupby("Item")["Qty"].sum().sort_values(ascending=False).head(10).reset_index(),
                                   x="Item", y="Qty", title="Top-Selling Items"), use_container_width=True)

            st.plotly_chart(px.bar(df.groupby("Stall No")["Final Total (Item)"].sum().reset_index(),
                                   x="Stall No", y="Final Total (Item)", title="Stall-wise Revenue"),
                                   use_container_width=True)

            st.plotly_chart(px.bar(df.groupby("Stall No")["Discount%"].mean().reset_index(),
                                   x="Stall No", y="Discount%", title="Average Discount per Stall"),
                                   use_container_width=True)

            df["Discount Amt"] = df["Price"] * df["Qty"] * (df["Discount%"] / 100)
            st.plotly_chart(px.bar(df.groupby("Stall No")["Discount Amt"].sum().reset_index(),
                                   x="Stall No", y="Discount Amt", title="Total Discount ₹ Given per Stall"),
                                   use_container_width=True)

            rev_items = df.groupby("Item")["Final Total (Item)"].sum().sort_values(ascending=False).reset_index()
            st.plotly_chart(px.pie(rev_items.head(10), values="Final Total (Item)", names="Item",
                                   title="Revenue Share by Item"), use_container_width=True)
        else:
            st.info("No sales data found.")

# ------------------------
# User Management (master only)
# ------------------------
if is_master:
    st.subheader("👤 User Management")

    # Assign location
    st.subheader("Assign Location to User")
    usernames_list = list(config["credentials"]["usernames"].keys())
    selected_user = st.selectbox("Select User", usernames_list)

    current_location = config["credentials"]["usernames"][selected_user].get("location", "")
    location_input = st.text_input(f"Enter Location for {selected_user}", value=current_location)

    if st.button("Save Location"):
        config["credentials"]["usernames"][selected_user]["location"] = location_input
        update_config_on_github(config)
        with open("config.yaml", "w") as f:
            yaml.safe_dump(config, f, sort_keys=False)
        st.success(f"Location '{location_input}' assigned to '{selected_user}'")
        st.rerun()

    # Existing users
    st.subheader("Existing Users")
    users_data = []
    for uname, details in config["credentials"]["usernames"].items():
        loc = details.get("location", "—")
        users_data.append([uname, details["name"], details["role"], loc])
    st.table(pd.DataFrame(users_data, columns=["Username", "Name", "Role", "Location"]))

    # Reset Password
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
                stored_hash = config['credentials']['usernames'][selected_user]['password']
                if not bcrypt.checkpw(current_pass_input.encode(), stored_hash.encode()):
                    st.error("🚫 Incorrect current password.")
                else:
                    hashed_pass = stauth.Hasher([new_pass]).generate()[0]
                    config['credentials']['usernames'][selected_user]['password'] = hashed_pass
                    with open("config.yaml", "w") as f:
                        yaml.dump(config, f)
                    update_config_on_github(config)
                    st.success(f"✅ Password for master user '{selected_user}' updated.")
                    st.rerun()
            else:
                hashed_pass = stauth.Hasher([new_pass]).generate()[0]
                config['credentials']['usernames'][selected_user]['password'] = hashed_pass
                with open("config.yaml", "w") as f:
                    yaml.dump(config, f)
                update_config_on_github(config)
                st.success(f"✅ Password for user '{selected_user}' updated.")

    # Create new user
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
                update_config_on_github(config)
                st.success(f"✅ User '{new_username}' created successfully.")
                st.rerun()

# ------------------------
# Invoice Search & Export (admin/master only)
# ------------------------
if is_admin or is_master:
    st.sidebar.markdown("### 📂 Invoice Search & Export")
    df = fetch_sheet_df()

    if not df.empty:
        stall_filter = st.sidebar.multiselect("🔎 Filter by Stall No", sorted(df["Stall No"].unique()))
        payment_filter = st.sidebar.multiselect("💰 Payment Method", sorted(df["Payment Method"].unique()))
        status_filter = st.sidebar.multiselect("📌 Status", sorted(df["Status"].unique()))
        start_date = st.sidebar.date_input("📅 Start Date", value=None)
        end_date = st.sidebar.date_input("📅 End Date", value=None)

        filtered_df = df.copy()
        if stall_filter:
            filtered_df = filtered_df[filtered_df["Stall No"].isin(stall_filter)]
        if payment_filter:
            filtered_df = filtered_df[filtered_df["Payment Method"].isin(payment_filter)]
        if status_filter:
            filtered_df = filtered_df[filtered_df["Status"].isin(status_filter)]
        if start_date:
            filtered_df = filtered_df[pd.to_datetime(filtered_df["Date"], dayfirst=True) >= pd.to_datetime(start_date)]
        if end_date:
            filtered_df = filtered_df[pd.to_datetime(filtered_df["Date"], dayfirst=True) <= pd.to_datetime(end_date)]

        st.sidebar.markdown(f"Showing **{len(filtered_df)}** filtered entries.")

        export_format = st.sidebar.radio("📁 Export Format", ["Excel", "CSV"], horizontal=True)
        export_filename = f"invoices_export.{ 'xlsx' if export_format == 'Excel' else 'csv' }"

        if export_format == "Excel":
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                filtered_df.to_excel(writer, index=False, sheet_name='Invoices')
            output.seek(0)
            st.sidebar.download_button("📤 Export Filtered", data=output,
                                       file_name=export_filename,
                                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            csv = filtered_df.to_csv(index=False).encode('utf-8')
            st.sidebar.download_button("📤 Export Filtered", data=csv,
                                       file_name=export_filename,
                                       mime="text/csv")
    else:
        st.sidebar.info("No data available for filtering/export.")


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

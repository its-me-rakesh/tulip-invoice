# ============================================
# Part 1: Constants & Config Management
# ============================================

import streamlit as st
import requests
import base64
import yaml
from github import Github

# ---------- Constants ----------
SHEET_NAME = "invoices_records"  # Google Sheet name
HEADERS = [
    "Stall No", "Invoice No", "Date", "Phone No", "Payment Method", 
    "Artisan Code", "Item", "Qty", "Price", "Total (Item)", 
    "Discount%", "Final Total (Item)", "Final Total (Invoice)", 
    "Status", "Location"
]
LOGO_PATH = "Tulip.jpeg"  # Path for invoice logo

# ---------- GitHub Config Management ----------
def load_config_from_github():
    """Fetch latest config.yaml directly from GitHub repo."""
    try:
        github_token = st.secrets["GITHUB_TOKEN"]
        repo = st.secrets["GITHUB_REPO"]
        config_path = st.secrets["CONFIG_FILE_PATH"]

        # Build API URL for raw file
        get_url = f"https://api.github.com/repos/{repo}/contents/{config_path}"
        headers = {"Authorization": f"token {github_token}"}

        # Fetch config file metadata + content
        r = requests.get(get_url, headers=headers)
        r.raise_for_status()
        file_info = r.json()

        # Decode Base64 YAML content
        decoded_content = base64.b64decode(file_info["content"]).decode()
        config = yaml.safe_load(decoded_content)
        return config, file_info["sha"]

    except Exception as e:
        st.error(f"❌ Failed to load config.yaml from GitHub: {e}")
        st.stop()  # Cannot continue without config

def update_config_on_github(updated_config):
    """Update config.yaml in GitHub with new content."""
    try:
        github_token = st.secrets["GITHUB_TOKEN"]
        repo = st.secrets["GITHUB_REPO"]
        config_path = st.secrets["CONFIG_FILE_PATH"]

        # Get file SHA (needed for update)
        get_url = f"https://api.github.com/repos/{repo}/contents/{config_path}"
        headers = {"Authorization": f"token {github_token}"}
        r = requests.get(get_url, headers=headers)
        r.raise_for_status()
        sha = r.json()["sha"]

        # Convert dict → YAML → Base64
        yaml_content = yaml.dump(updated_config, sort_keys=False)
        encoded_content = base64.b64encode(yaml_content.encode()).decode()

        # Commit payload
        payload = {
            "message": "Update config.yaml from Streamlit app",
            "content": encoded_content,
            "sha": sha,
            "branch": "main"
        }

        # Push update to GitHub
        put_url = f"https://api.github.com/repos/{repo}/contents/{config_path}"
        put_response = requests.put(put_url, headers=headers, json=payload)
        put_response.raise_for_status()

        st.success("✅ Config updated on GitHub successfully!")
        st.rerun()

    except Exception as e:
        st.error(f"❌ Failed to update config.yaml on GitHub: {e}")

# ============================================
# Part 2: Authentication & Role Setup
# ============================================

import streamlit_authenticator as stauth

# 🔹 Load config from GitHub (always latest)
config, config_sha = load_config_from_github()

# 🔹 Initialize authenticator
authenticator = stauth.Authenticate(
    config['credentials'],       # User credentials dict
    config['cookie']['name'],    # Cookie name
    config['cookie']['key'],     # Cookie key
    config['cookie']['expiry_days']  # Cookie expiry
)

# 🔹 App branding (logo + title)
st.set_page_config(page_title="Invoice Generator", layout="centered")
st.image(LOGO_PATH, use_container_width=False, width=700)
st.markdown(
    "<div style='text-align: center; font-size: 14px; margin-bottom: 10px;'>"
    "Welcome to Tulip Billing</div>",
    unsafe_allow_html=True
)

# 🔹 User login
name, auth_status, username = authenticator.login("Login", "main")

# 🔹 Handle login states
if auth_status is False:
    st.error("Incorrect username or password.")
    st.stop()
elif auth_status is None:
    st.warning("Please enter your credentials.")
    st.stop()

# 🔹 Persist login until manual logout
authenticator.logout("🔒 Logout", "sidebar")

# 🔹 Extract role for current user
role = config['credentials']['usernames'][username]['role']
is_master = role == 'master'
is_admin = role == 'admin'
is_user = role == 'user'

# 🔹 Welcome message
st.success(f"Welcome, {name} 👋 | Role: {role.upper()}")
st.title("Shilp Samagam Mela Invoicing System")


# ============================================
# Part 3: Google Sheets Utilities
# ============================================

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# 🔹 Google Sheets Authentication
def get_google_sheet():
    """Authorize and return the worksheet object."""
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]

        creds_dict = dict(st.secrets["gcp_service_account"])
        base_creds = Credentials.from_service_account_info(creds_dict)
        scoped_creds = base_creds.with_scopes(scopes)

        gc = gspread.authorize(scoped_creds)
        sh = gc.open(SHEET_NAME)   # sheet name from constants
        return sh.sheet1
    except Exception as e:
        st.error(f"❌ Could not connect to Google Sheets: {e}")
        st.stop()


@st.cache_data(ttl=300, show_spinner="Loading data from Google Sheets...")
def _fetch_sheet_df_internal():
    """Internal cached function for fetching sheet data."""
    worksheet = get_google_sheet()
    data = worksheet.get_all_records()
    df = pd.DataFrame(data)
    df.columns = df.columns.astype(str).str.strip()

    # ✅ Ensure critical columns are strings
    for col in ["Invoice No", "Stall No", "Phone No", "Payment Method", "Item", "Status"]:
        if col in df.columns:
            df[col] = df[col].astype(str)

    return df


def fetch_sheet_df(refresh=False):
    """Fetch invoice records as DataFrame. Pass refresh=True to force re-fetch."""
    if refresh:
        _fetch_sheet_df_internal.clear()  # Clear only this cache
    return _fetch_sheet_df_internal()


def append_to_google_sheet(rows, username, config):
    """
    Append new invoice rows to Google Sheet.
    - Adds location based on logged-in user.
    """
    try:
        worksheet = get_google_sheet()

        # Insert header if sheet is empty
        if not worksheet.row_values(1):
            worksheet.insert_row(HEADERS, 1)

        # Get current user location from config
        current_user_location = config["credentials"]["usernames"][username].get("location", "")

        # Append location to each row
        rows_with_location = [row + [current_user_location] for row in rows]

        worksheet.append_rows(rows_with_location, value_input_option="USER_ENTERED")

    except Exception as e:
        st.error(f"⚠️ Failed to update Google Sheet: {e}")

# ============================================
# Part 4: Invoice Creation
# ============================================

from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from datetime import datetime
from io import BytesIO
import os

# ---------- Helper: Generate Next Invoice No ----------
def get_next_invoice_no(counter: str, df: pd.DataFrame) -> str:
    """Generate the next invoice number for the given counter."""
    inv_numeric = 1
    if counter and not df.empty:
        df_counter = df[df["Invoice No"].str.startswith(counter)]
        if not df_counter.empty:
            last = (
                df_counter["Invoice No"]
                .str.extract(rf"{counter}_INV(\d+)")
                .dropna()[0]
                .astype(int)
                .max()
            )
            inv_numeric = last + 1
    return f"{counter}_INV{inv_numeric:02d}"


# ---------- Helper: Draw Invoice Page ----------
def draw_page(inv, invoice_no, artisan_code, date, stall_no, ph_no, payment_method, items, totals, heading):
    """
    Draw a single invoice page (Invoice or Artisan Slip).
    - inv: canvas object
    - items: list of dicts [{s_no, item, price, qty, total}]
    - totals: dict with subtotal, discount, grand_total
    """
    inv.line(5, 45, 195, 45)
    inv.translate(10, 40)
    inv.scale(1, -1)

    # --- Logo ---
    if not os.path.exists(LOGO_PATH):
        raise FileNotFoundError(f"Logo '{LOGO_PATH}' not found.")
    inv.drawImage(ImageReader(LOGO_PATH), x=-10, y=0, width=200, height=40, preserveAspectRatio=False, mask='auto')
    inv.scale(1, -1)
    inv.translate(-10, -40)

    # --- Heading ---
    inv.setFont("Times-Bold", 6)
    inv.drawCentredString(100, 55, heading)

    # --- Company/Invoice details ---
    inv.setFont("Times-Bold", 4)
    inv.drawString(15, 70, f"Invoice No.: {invoice_no}")
    inv.drawString(15, 80, f"Artisan Code: {artisan_code}")
    inv.drawString(15, 90, f"Date: {date}")

    inv.drawString(110, 70, f"Stall No.: {stall_no}")
    inv.drawString(110, 80, f"Customer Ph No.: {ph_no}")
    inv.drawString(110, 90, f"Payment Method: {payment_method}")

    # --- Items Table ---
    start_y = 100
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

    # --- Totals ---
    inv.setFont("Times-Bold", 5)
    inv.drawString(15, y + 10, f"Subtotal: {totals['subtotal']:.2f}")
    inv.drawString(15, y + 20, f"Total Discount: {totals['discount']:.2f}")
    inv.drawString(140, y + 10, f"Grand Total: {totals['grand_total']:.2f}")
    inv.drawString(140, y + 60, "Tulip")
    inv.drawString(140, y + 68, "Signature")


# ---------- Invoice Form ----------
st.subheader("🧾 Create New Invoice")

with st.form("invoice_form"):
    # --- Billing counter & company details ---
    billing_counter = st.text_input("Counter Name (e.g. MAIN)").strip().upper()
    col1, col2 = st.columns(2)
    with col1:
        stall_no = st.text_input("Stall Number")
        artisan_code = st.text_input("Artisan Code")
    with col2:
        date = st.date_input("Invoice Date", value=datetime.today()).strftime("%d-%m-%Y")
        ph_no = st.text_input("Customer Phone No.")
        payment_method = st.selectbox("Payment Method", ["Cash", "UPI"])

    # --- Invoice number ---
    all_df = fetch_sheet_df()
    invoice_no = get_next_invoice_no(billing_counter, all_df)

    # --- Items ---
    st.subheader("Add Items to Invoice")
    num_items = st.number_input("How many items?", min_value=1, step=1, value=1)
    items = []
    for i in range(num_items):
        with st.expander(f"Item {i + 1}"):
            name = st.text_input("Item Name", key=f"item_{i}")
            price = st.number_input("Price per unit", min_value=0.0, step=0.1, key=f"price_{i}")
            qty = st.number_input("Quantity", min_value=1, step=1, key=f"qty_{i}")
            discount_item = st.number_input(f"Discount % for Item {i + 1}", min_value=0.0, max_value=100.0, value=0.0, step=0.1, key=f"discount_{i}")

            total_before_discount = price * qty
            total_after_discount = total_before_discount * (1 - discount_item / 100)

            items.append({
                "s_no": str(i + 1),
                "item": name,
                "price": price,
                "qty": qty,
                "discount_percent": discount_item,
                "total": total_before_discount,
                "final_total": total_after_discount
            })
    # --- Live Subtotals (Realtime) ---
    subtotal = sum(it["total"] for it in items)
    discount_amt = sum(it["total"] - it["final_total"] for it in items)
    grand_total = sum(it["final_total"] for it in items)
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Subtotal", f"₹ {subtotal:.2f}")
    col2.metric("Discount", f"₹ {discount_amt:.2f}")
    col3.metric("Grand Total", f"₹ {grand_total:.2f}")
    # --- Generate button ---
    generate_invoice = st.form_submit_button("🧾 Generate Invoice")

# ---------- Generate Invoice Logic ----------
if generate_invoice:
    if not billing_counter or not stall_no:
        st.error("❌ Billing Counter and Stall Number are required.")
    else:
        # Create PDF
        buffer = BytesIO()
        height = 250 + 15 * len(items)
        inv = canvas.Canvas(buffer, pagesize=(200, height), bottomup=0)
        totals_dict = {"subtotal": subtotal, "discount": discount_amt, "grand_total": grand_total}
        draw_page(inv, invoice_no, artisan_code, date, stall_no, ph_no, payment_method, items, totals_dict, "INVOICE")
        inv.showPage()
        draw_page(inv, invoice_no, artisan_code, date, stall_no, ph_no, payment_method, items, totals_dict, "ARTISAN SLIP")
        inv.save()
        buffer.seek(0)

        # Download button
        st.download_button("📄 Download Invoice PDF", buffer, file_name=f"{invoice_no}.pdf", mime="application/pdf")

        # Prepare rows for Google Sheet
        rows = [[
            stall_no, invoice_no, date, ph_no, payment_method, artisan_code,
            it["item"], it["qty"], it["price"], it["total"],
            it["discount_percent"], it["final_total"], grand_total, "Active"
        ] for it in items]

        # Save to Google Sheets
        append_to_google_sheet(rows, username, config)
        fetch_sheet_df(refresh=True)  # refresh cache
        st.success("✅ Invoice saved & data refreshed!")


# ============================================
# Part 5: Past Invoices Viewer (Admin/Master Only)
# ============================================

if is_admin or is_master:
    st.subheader("📚 Previous Invoice Records")

    with st.expander("Show all past invoice entries"):
        df = fetch_sheet_df(refresh=True)   # 🔄 always fresh

        if not df.empty:
            # Display past invoices
            st.dataframe(df, use_container_width=True)

            # Select invoice for action
            invoice_ids = df["Invoice No"].unique()
            selected_invoice = st.selectbox("🧾 Select Invoice", invoice_ids)
            selected_df = df[df["Invoice No"] == selected_invoice]

            # Get invoice status
            invoice_status = selected_df["Status"].iloc[0] if "Status" in selected_df.columns else "Active"

            # Cancel or Restore
            worksheet = get_google_sheet()
            if invoice_status == "Active":
                if st.button("❌ Cancel This Invoice"):
                    all_data = worksheet.get_all_records()
                    df_all = pd.DataFrame(all_data)

                    # Update all rows for this invoice
                    for idx, row in df_all[df_all["Invoice No"] == selected_invoice].iterrows():
                        worksheet.update_cell(idx + 2, df_all.columns.get_loc("Status") + 1, "Cancelled")

                    fetch_sheet_df(refresh=True)
                    st.success(f"🛑 Invoice {selected_invoice} marked as Cancelled.")
            else:
                if st.button("↩️ Restore This Invoice"):
                    all_data = worksheet.get_all_records()
                    df_all = pd.DataFrame(all_data)

                    for idx, row in df_all[df_all["Invoice No"] == selected_invoice].iterrows():
                        worksheet.update_cell(idx + 2, df_all.columns.get_loc("Status") + 1, "Active")

                    fetch_sheet_df(refresh=True)
                    st.success(f"✅ Invoice {selected_invoice} restored.")

            # Reprint selected invoice
            if st.button("🖨️ Reprint Invoice as PDF"):
                invoice_items = selected_df.to_dict(orient="records")
                items = [{
                    "s_no": str(i + 1),
                    "item": r["Item"],
                    "price": float(r["Price"]),
                    "qty": int(r["Qty"]),
                    "total": float(r["Total (Item)"])
                } for i, r in enumerate(invoice_items)]

                stall_no = invoice_items[0]["Stall No"]
                invoice_no = invoice_items[0]["Invoice No"]
                date = invoice_items[0]["Date"]
                ph_no = invoice_items[0]["Phone No"]
                artisan_code = invoice_items[0].get("Artisan Code", "")
                payment_method = invoice_items[0].get("Payment Method", "Cash")

                # Totals
                subtotal = sum(it["total"] for it in items)
                discount_amt = sum(
                    (it["total"] * float(r.get("Discount%", 0)) / 100)
                    for it, r in zip(items, invoice_items)
                )
                grand_total = invoice_items[0].get("Final Total (Invoice)", subtotal - discount_amt)

                totals_dict = {"subtotal": subtotal, "discount": discount_amt, "grand_total": grand_total}

                # Generate PDF
                buffer = BytesIO()
                inv = canvas.Canvas(buffer, pagesize=(200, 250 + 15 * len(items)), bottomup=0)
                draw_page(inv, invoice_no, artisan_code, date, stall_no, ph_no, payment_method, items, totals_dict, "INVOICE")
                inv.showPage()
                draw_page(inv, invoice_no, artisan_code, date, stall_no, ph_no, payment_method, items, totals_dict, "ARTISAN SLIP")
                inv.save()
                buffer.seek(0)

                st.download_button("📥 Download Re-Generated PDF", buffer, file_name=f"{invoice_no}_copy.pdf")

        else:
            st.info("ℹ️ No invoice records found.")

# ============================================
# Part 6: Sales Dashboard (Admin/Master Only)
# ============================================

import plotly.express as px

if is_admin or is_master:
    st.subheader("📊 Sales Dashboard")

    with st.expander("View Sales Analytics"):
        df = fetch_sheet_df(refresh=True)

        if not df.empty:
            # Convert Date safely
            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")

            # ---------- Summary Stats ----------
            st.markdown("### 📌 Summary Stats")
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Revenue", f"₹{df['Final Total (Item)'].sum():,.2f}")
            col2.metric("Total Items Sold", int(df["Qty"].sum()))
            col3.metric("Total Invoices", df["Invoice No"].nunique())

            # ---------- Revenue Over Time ----------
            revenue_over_time = df.groupby("Date")["Final Total (Item)"].sum().reset_index()
            st.plotly_chart(
                px.bar(revenue_over_time, x="Date", y="Final Total (Item)", 
                       title="Revenue Over Time", color_discrete_sequence=["green"]),
                use_container_width=True
            )

            # ---------- Top-Selling Items ----------
            top_items = df.groupby("Item")["Qty"].sum().sort_values(ascending=False).head(10).reset_index()
            st.plotly_chart(
                px.bar(top_items, x="Item", y="Qty", 
                       title="Top-Selling Items", color_discrete_sequence=["#FFD700"]),
                use_container_width=True
            )

            # ---------- Stall-wise Revenue ----------
            stall_revenue = df.groupby("Stall No")["Final Total (Item)"].sum().sort_values(ascending=False).reset_index()
            st.plotly_chart(
                px.bar(stall_revenue, x="Stall No", y="Final Total (Item)", 
                       title="Stall-wise Revenue", color_discrete_sequence=["#FF0000"]),
                use_container_width=True
            )

            # ---------- Average Discount per Stall ----------
            avg_discount = df.groupby("Stall No")["Discount%"].mean().reset_index()
            st.plotly_chart(
                px.bar(avg_discount, x="Stall No", y="Discount%", 
                       title="Average Discount per Stall", color_discrete_sequence=["#FF69B4"]),
                use_container_width=True
            )

            # ---------- Total Discount Amount per Stall ----------
            df["Discount Amt"] = df["Price"] * df["Qty"] * (df["Discount%"] / 100)
            total_discount = df.groupby("Stall No")["Discount Amt"].sum().reset_index()
            st.plotly_chart(
                px.bar(total_discount, x="Stall No", y="Discount Amt", 
                       title="Total Discount ₹ Given per Stall", color_discrete_sequence=["#FFA500"]),
                use_container_width=True
            )

            # ---------- Revenue Share by Item ----------
            rev_items = df.groupby("Item")["Final Total (Item)"].sum().sort_values(ascending=False).reset_index()
            st.plotly_chart(
                px.pie(rev_items.head(10), values="Final Total (Item)", names="Item", 
                       title="Revenue Share by Item"),
                use_container_width=True
            )

        else:
            st.info("ℹ️ No sales data available for analytics.")

# ============================================
# Part 7: User Management (Master Only)
# ============================================

import bcrypt

if is_master:
    st.subheader("👤 User Management")

    # -------------------------------
    # Assign Location to User
    # -------------------------------
    st.subheader("📍 Assign Location to User")

    usernames_list = list(config["credentials"]["usernames"].keys())
    selected_user = st.selectbox("Select User", usernames_list)

    current_location = config["credentials"]["usernames"][selected_user].get("location", "")
    location_input = st.text_input(f"Enter Location for {selected_user}", value=current_location)

    if st.button("💾 Save Location"):
        config["credentials"]["usernames"][selected_user]["location"] = location_input
        update_config_on_github(config)
        st.success(f"✅ Location '{location_input}' assigned to '{selected_user}'")

    # Show existing users in a table
    st.subheader("📋 Existing Users")
    users_data = []
    for username, details in config["credentials"]["usernames"].items():
        location = details.get("location", "—")  # Default dash if not set
        users_data.append([username, details["name"], details["role"], location])

    st.table(pd.DataFrame(users_data, columns=["Username", "Name", "Role", "Location"]))

    st.markdown("---")
    st.markdown("### 🔐 Reset or Change User Password")

    # -------------------------------
    # Reset Password Form
    # -------------------------------
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
                # Master user must validate current password
                stored_hash = config['credentials']['usernames'][selected_user]['password']
                if not bcrypt.checkpw(current_pass_input.encode(), stored_hash.encode()):
                    st.error("🚫 Incorrect current password.")
                else:
                    hashed_pass = stauth.Hasher([new_pass]).generate()[0]
                    config['credentials']['usernames'][selected_user]['password'] = hashed_pass
                    update_config_on_github(config)
                    st.success(f"✅ Password for master user '{selected_user}' updated.")
                    st.rerun()
            else:
                # Non-master users — no current password required
                hashed_pass = stauth.Hasher([new_pass]).generate()[0]
                config['credentials']['usernames'][selected_user]['password'] = hashed_pass
                update_config_on_github(config)
                st.success(f"✅ Password for user '{selected_user}' updated.")
                st.rerun()

    st.markdown("---")
    st.markdown("### ➕ Create New User")

    # -------------------------------
    # Create User Form
    # -------------------------------
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
                update_config_on_github(config)
                st.success(f"✅ User '{new_username}' with role '{new_role}' created successfully.")
                st.rerun()

# ============================================
# Part 8: Invoice Search, Export & Footer
# ============================================

if is_admin or is_master:
    st.sidebar.markdown("### 📂 Invoice Search & Export")

    df = fetch_sheet_df()

    if not df.empty:
        # ---------- Filters ----------
        stall_filter = st.sidebar.multiselect("🔎 Filter by Stall No", sorted(df["Stall No"].unique()))
        payment_filter = st.sidebar.multiselect("💰 Payment Method", sorted(df["Payment Method"].unique()))
        status_filter = st.sidebar.multiselect("📌 Status", sorted(df["Status"].unique()))
        start_date = st.sidebar.date_input("📅 Start Date", value=None)
        end_date = st.sidebar.date_input("📅 End Date", value=None)

        # Apply filters
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

        # Show preview count
        st.sidebar.markdown(f"Showing **{len(filtered_df)}** filtered entries.")

        # ---------- Export ----------
        export_format = st.sidebar.radio("📁 Export Format", ["Excel", "CSV"], horizontal=True)
        export_filename = f"invoices_export.{ 'xlsx' if export_format == 'Excel' else 'csv' }"

        if export_format == "Excel":
            from io import BytesIO
            output = BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                filtered_df.to_excel(writer, index=False, sheet_name="Invoices")
            output.seek(0)
            st.sidebar.download_button(
                "📤 Export Filtered", data=output, file_name=export_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            csv = filtered_df.to_csv(index=False).encode("utf-8")
            st.sidebar.download_button(
                "📤 Export Filtered", data=csv, file_name=export_filename, mime="text/csv"
            )

    else:
        st.sidebar.info("ℹ️ No data available for filtering/export.")


# ---------- Footer ----------
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

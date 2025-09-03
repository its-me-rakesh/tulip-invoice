"""
invoice_generator.py — Modular Role‑Based Invoice System (refactored)
- Streamlit app for invoicing with Google Sheets backend
- Role-based auth, PDF generation, admin tools, dashboard & export
"""

# =====================
# 1) Imports & Page Config
# =====================
import os
import base64
from io import BytesIO
from datetime import datetime

import streamlit as st
import yaml
import pandas as pd
import plotly.express as px
import gspread
from google.oauth2.service_account import Credentials
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import streamlit_authenticator as stauth
import bcrypt
import requests
from github import Github

st.set_page_config(page_title="Invoice Generator", layout="centered")

# =====================
# 2) Secrets & Constants
# =====================
# Expect these in Streamlit secrets
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN")
GITHUB_REPO = st.secrets.get("GITHUB_REPO")
CONFIG_FILE_PATH = st.secrets.get("CONFIG_FILE_PATH", "config.yaml")

# =====================
# 3) Config & Auth Helpers
# =====================
@st.cache_data(show_spinner=False)
def load_config():
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)

def push_config_to_github():
    """Pushes the updated config.yaml to the GitHub repo."""
    try:
        # Load GitHub token and repo from Streamlit secrets
        token = st.secrets["GITHUB_TOKEN"]
        repo_name = st.secrets["GITHUB_REPO"]
        config_file_path = st.secrets["CONFIG_FILE_PATH"]

        g = Github(token)
        repo = g.get_repo(repo_name)

        # Read the local config.yaml
        with open(config_file_path, "r") as f:
            updated_content = f.read()

        # Get current file from repo
        contents = repo.get_contents(config_file_path)

        # Update the file in GitHub
        repo.update_file(
            path=config_file_path,
            message="Update config.yaml via Streamlit app",
            content=updated_content,
            sha=contents.sha
        )
        st.success("✅ Config file successfully updated in GitHub.")
    except Exception as e:
        st.error(f"❌ Failed to update config.yaml in GitHub: {e}")

def update_config_on_github(updated_config):
    try:
        # Read secrets
        github_token = st.secrets["GITHUB_TOKEN"]
        repo = st.secrets["GITHUB_REPO"]
        config_path = st.secrets["CONFIG_FILE_PATH"]

        # Get the file from GitHub (to fetch SHA for update)
        get_url = f"https://api.github.com/repos/{repo}/contents/{config_path}"
        headers = {"Authorization": f"token {github_token}"}
        r = requests.get(get_url, headers=headers)
        r.raise_for_status()
        file_info = r.json()
        sha = file_info["sha"]

        # Convert updated config dict to YAML
        import yaml
        yaml_content = yaml.dump(updated_config, sort_keys=False)

        # Encode to Base64
        encoded_content = base64.b64encode(yaml_content.encode()).decode()

        # Prepare commit payload
        commit_message = "Update config.yaml from Streamlit app"
        payload = {
            "message": commit_message,
            "content": encoded_content,
            "sha": sha,
            "branch": "main"  # Change if your default branch is different
        }

        # Send PUT request to GitHub API to update file
        put_url = f"https://api.github.com/repos/{repo}/contents/{config_path}"
        put_response = requests.put(put_url, headers=headers, json=payload)
        put_response.raise_for_status()

        st.success("✅ Config updated on GitHub successfully!")
        st.rerun()

    except Exception as e:
        st.error(f"❌ Failed to update config on GitHub: {e}")

# Authenticator
# ------------------------
with open("config.yaml") as file:
    config = yaml.safe_load(file)

authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

# =====================
# 4) Header & Login
# =====================
st.image("Tulip.jpeg", use_container_width=False, width=700)
st.markdown(
    "<div style='text-align: center; font-size: 14px; margin-bottom: 10px;'>Welcome to Tulip Billing</div>",
    unsafe_allow_html=True,
)

name, auth_status, username = authenticator.login("Login", "main")
if auth_status is False:
    st.error("Incorrect username or password.")
    st.stop()
elif auth_status is None:
    st.warning("Please enter your credentials.")
    st.stop()

# Persist login until manual logout
authenticator.logout("🔒 Logout", "sidebar")

# Useful session vars
st.session_state["username"] = username
role = config["credentials"]["usernames"][username]["role"]
is_master = role == "master"
is_admin = role == "admin"

st.success(f"Welcome, {name} 👋 | Role: {role.upper()}")
st.title("Shilp Samagam Mela Invoicing System")

# =====================
# 5) Google Sheets Helpers
# =====================

def _get_google_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = dict(st.secrets["gcp_service_account"])  # service account JSON
    base_creds = Credentials.from_service_account_info(creds_dict)
    scoped_creds = base_creds.with_scopes(scopes)
    gc = gspread.authorize(scoped_creds)
    sh = gc.open("invoices_records")  # Sheet name must match
    return sh.sheet1


@st.cache_data(ttl=300, show_spinner="Loading data from Google Sheets…")
def fetch_sheet_df() -> pd.DataFrame:
    try:
        worksheet = _get_google_sheet()
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        if df.empty:
            return df
        # Normalize column names & dtypes
        df.columns = df.columns.astype(str).str.strip()
        for col in [
            "Invoice No",
            "Stall No",
            "Phone No",
            "Payment Method",
            "Item",
            "Status",
        ]:
            if col in df.columns:
                df[col] = df[col].astype(str)
        return df
    except Exception as e:
        st.warning(f"⚠️ Failed to fetch Google Sheet data: {e}")
        return pd.DataFrame()


def append_to_google_sheet(rows: list[list]):
    """Append invoice rows, auto-inserting/upgrading header & user location."""
    try:
        worksheet = _get_google_sheet()
        # Target header (includes GST)
        target_header = [
            "Stall No",
            "Invoice No",
            "Date",
            "Phone No",
            "Payment Method",
            "Artisan Code",
            "Item",
            "Qty",
            "Price",
            "Total (Item)",
            "Discount%",
            "Final Total (Item)",
            "Final Total (Invoice)",   # GST-inclusive
            "GST%",
            "GST Amt",
            "Status",
            "Corporation",
            "Location",
        ]

        # Get current header row (strip blanks)
        current_header = [h.strip() for h in worksheet.row_values(1)]
        current_header = [h for h in current_header if h]  # remove empty strings

        if not current_header:
            # Sheet is empty → insert fresh header
            worksheet.insert_row(target_header, 1)
        else:
            # If mismatch → rewrite header fully to avoid duplicates
            if current_header != target_header:
                worksheet.update("1:1", [target_header])

        # Add location column from user profile
        current_user = st.session_state.get("username", "")
        location = (
            config["credentials"]["usernames"].get(current_user, {}).get("location", "")
        )

        # Ensure each row matches header length
        rows_with_location = []
        for r in rows:
            full_row = r.copy()
            # Fill GST% / GST Amt / Location if caller didn’t add
            while len(full_row) < len(target_header):
                if len(full_row) == target_header.index("Location"):
                    full_row.append(location)
                else:
                    full_row.append("")
            rows_with_location.append(full_row)

        worksheet.append_rows(rows_with_location, value_input_option="USER_ENTERED")

    except Exception as e:
        st.warning(f"⚠️ Failed to update Google Sheet: {e}")


# =====================
# 6) Invoice Creation Form
# =====================
st.subheader("1. Billing Counter")
billing_counter = st.text_input("Counter Name (e.g. MAIN)").strip().upper()

st.subheader("2. Company & Invoice Details")
col1, col2 = st.columns(2)
with col1:
    Corporation = st.selectbox("Corporation", ["NBCFDC", "NSFDC", "NSKFDC"])
    stall_no = st.text_input("Stall Number")
    artisan_code = st.text_input("Artisan Code")
with col2:
    date_str = st.date_input("Invoice Date", value=datetime.today()).strftime("%d-%m-%Y")
    ph_no = st.text_input("Customer Phone No.")
    payment_method = st.selectbox("Payment Method", ["Cash", "UPI", "Card"])
    gst_percent = st.selectbox("GST Rate (%)", (0, 3, 5, 12, 18, 28), index=0)

    

# Invoice number generation
invoice_no = ""
inv_numeric = 1
_all_df = fetch_sheet_df()
if billing_counter and not _all_df.empty and "Invoice No" in _all_df.columns:
    df_counter = _all_df[_all_df["Invoice No"].astype(str).str.startswith(billing_counter)]
    if not df_counter.empty:
        last = (
            df_counter["Invoice No"].astype(str).str.extract(rf"{billing_counter}_INV(\d+)")[0]
            .dropna()
            .astype(int)
            .max()
        )
        inv_numeric = int(last) + 1
invoice_no = f"{billing_counter}_INV{inv_numeric:02d}" if billing_counter else ""

# Items
st.subheader("3. Add Items to Invoice")
num_items = st.number_input("How many items?", min_value=1, step=1)
items: list[dict] = []
for i in range(num_items):
    with st.expander(f"Item {i + 1}"):
        name = st.text_input("Item Name", key=f"item_{i}")
        price = st.number_input("Price per unit", min_value=0.0, step=0.1, key=f"price_{i}")
        qty = st.number_input("Quantity", min_value=1, step=1, key=f"qty_{i}")
        discount_item = st.number_input(
            f"Discount % for Item {i + 1}",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=0.1,
            key=f"discount_{i}",
        )
        total_before_discount = price * qty
        total_after_discount = total_before_discount * (1 - discount_item / 100)
        items.append(
            {
                "s_no": str(i + 1),
                "item": name,
                "price": float(price),
                "qty": int(qty),
                "discount_percent": float(discount_item),
                "total": float(total_before_discount),
                "final_total": float(total_after_discount),
            }
        )

subtotal = sum(it["final_total"] for it in items)
gst_amount = subtotal * (gst_percent / 100.0)
grand_total_incl_gst = subtotal + gst_amount

st.markdown(f"### 🧾 Subtotal (After Discount): ₹ {subtotal:.2f}")
st.markdown(f"### 🧮 GST ({gst_percent}%): ₹ {gst_amount:.2f}")
st.markdown(f"## 💰 Total (GST Inclusive): ₹ {grand_total_incl_gst:.2f}")

# =====================
# 7) PDF Rendering Helper
# =====================

def _draw_page(inv: canvas.Canvas, heading: str, totals: dict):
    total_amount = totals["total_amount"]
    discount_amt = totals["discount_amt"]
    gst_percent = totals["gst_percent"]            
    gst_amount = totals["gst_amount"]              
    grand_total = totals["grand_total_incl_gst"]   

    inv.line(5, 45, 195, 45)
    inv.translate(10, 40)
    inv.scale(1, -1)

    logo_path = os.path.join(os.path.dirname(__file__), "Tulip.jpeg")
    if not os.path.exists(logo_path):
        # Fallback to cwd if running on some hosts
        logo_path = "Tulip.jpeg"
    inv.drawImage(
        ImageReader(logo_path), x=-10, y=0, width=200, height=40, preserveAspectRatio=False, mask="auto"
    )
    inv.scale(1, -1)
    inv.translate(-10, -40)
    inv.setFont("Times-Bold", 6)
    inv.drawCentredString(100, 55, heading)

    inv.setFont("Times-Bold", 4)
    # Left column
    inv.drawString(15, 70, f"Invoice No.: {invoice_no}")
    inv.drawString(15, 80, f"Artisan Code: {artisan_code}")
    inv.drawString(15, 90, f"Date: {date_str}")

    # Right column
    inv.drawString(110, 70, f"Stall No.: {stall_no}")
    inv.drawString(110, 80, f"Customer Ph No.: {ph_no}")
    inv.drawString(110, 90, f"Payment Method: {payment_method}")

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

    inv.setFont("Times-Bold", 4)
    inv.drawString(15,  y + 10, f"Subtotal (After Discount): {subtotal:.2f}")
    inv.drawString(15,  y + 20, f"Total Discount: {discount_amt:.2f}")
    inv.drawString(15,  y + 30, f"GST ({gst_percent}%): {gst_amount:.2f}")
    inv.drawString(15, y + 40, f"Grand Total (Incl GST): {grand_total:.2f}")
    inv.drawString(140, y + 60, "Tulip")
    inv.drawString(140, y + 68, "Signature")


# =====================
# 8) Generate Invoice (PDF + Save)
# =====================
missing = []
if not billing_counter:
    missing.append("Billing Counter")
if not stall_no:
    missing.append("Stall Number")

st.button_disabled = bool(missing)
if missing:
    st.error(
        f"Please fill required field(s): {', '.join(missing)} to enable invoice generation."
    )

if st.button("🧾 Generate Invoice", disabled=st.button_disabled):
    # Defensive guard
    if not billing_counter or not stall_no:
        st.error("Billing Counter and Stall Number are required.")
        st.stop()

    total_amount = sum(it["total"] for it in items)                       # before discount
    discount_amt = sum(it["total"] - it["final_total"] for it in items)   # discount value
    subtotal_after_discount = sum(it["final_total"] for it in items)      # after discount
    gst_amount_calc = subtotal_after_discount * (gst_percent / 100.0)
    grand_total_incl_gst_calc = subtotal_after_discount + gst_amount_calc
    
    totals = {
        "total_amount": total_amount,
        "discount_amt": discount_amt,
        "subtotal": subtotal_after_discount,
        "gst_percent": gst_percent,
        "gst_amount": gst_amount_calc,
        "grand_total_incl_gst": grand_total_incl_gst_calc,
    }

    buf = BytesIO()
    height = 250 + 15 * len(items)
    pdf = canvas.Canvas(buf, pagesize=(200, height), bottomup=0)
    _draw_page(pdf, "INVOICE", totals)
    pdf.showPage()
    _draw_page(pdf, "ARTISAN SLIP", totals)
    pdf.save()
    buf.seek(0)

    st.download_button(
        "📄 Download Invoice PDF",
        buf,
        file_name=f"{invoice_no}.pdf",
        mime="application/pdf",
    )

    # Prepare rows WITHOUT location (it will be added inside append_to_google_sheet)
    rows = [
        [
            stall_no,
            invoice_no,
            date_str,
            ph_no,
            payment_method,
            artisan_code,
            it["item"],
            it["qty"],
            it["price"],
            it["total"],
            it["discount_percent"],
            it["final_total"],
            grand_total_incl_gst_calc,  # ✅ store inclusive total
            gst_percent,                 # ✅ GST%
            gst_amount_calc,             # ✅ GST Amt
            "Active",
            # Location is auto-added inside append_to_google_sheet()
            Corporation,
        ]
        for it in items
    ]
    append_to_google_sheet(rows)
    fetch_sheet_df.clear()
    _ = fetch_sheet_df()  # re-fetch
    st.success("✅ Invoice saved to your database and data refreshed!")


# =====================
# 9) Past Invoices (Admin/Master)
# =====================
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

            invoice_status = (
                selected_df["Status"].iloc[0] if ("Status" in selected_df.columns and not selected_df.empty) else "Active"
            )

            if invoice_status == "Active":
                if st.button("❌ Cancel This Invoice"):
                    ws = _get_google_sheet()
                    all_data = ws.get_all_records()
                    df_all = pd.DataFrame(all_data)
                    for idx, _row in df_all[df_all["Invoice No"] == selected_invoice].iterrows():
                        ws.update_cell(idx + 2, df_all.columns.get_loc("Status") + 1, "Cancelled")
                    fetch_sheet_df.clear(); _ = fetch_sheet_df()
                    st.success(f"🛑 Invoice {selected_invoice} marked as Cancelled.")
            else:
                if st.button("↩️ Restore This Invoice"):
                    ws = _get_google_sheet()
                    all_data = ws.get_all_records()
                    df_all = pd.DataFrame(all_data)
                    for idx, _row in df_all[df_all["Invoice No"] == selected_invoice].iterrows():
                        ws.update_cell(idx + 2, df_all.columns.get_loc("Status") + 1, "Active")
                    fetch_sheet_df.clear(); _ = fetch_sheet_df()
                    st.success(f"✅ Invoice {selected_invoice} restored.")

            if st.button("🖨️ Generate PDF for Selected"):
                invoice_items = selected_df.to_dict(orient="records")
                items_copy = [
                    {
                        "s_no": str(i + 1),
                        "item": r["Item"],
                        "price": float(r["Price"]),
                        "qty": int(r["Qty"]),
                        "total": float(r["Total (Item)"]),
                    }
                    for i, r in enumerate(invoice_items)
                ]
                # Rehydrate header fields
                stall_no_sel = invoice_items[0]["Stall No"]
                invoice_no_sel = invoice_items[0]["Invoice No"]
                date_sel = invoice_items[0]["Date"]
                ph_sel = invoice_items[0]["Phone No"]
                artisan_sel = invoice_items[0].get("Artisan Code", "")
                pm_sel = invoice_items[0].get("Payment Method", "Cash")
                discount_percent = float(invoice_items[0].get("Discount%", 0))
                total_amount_sel = sum(it["total"] for it in items_copy)
                discount_amt_sel = total_amount_sel * discount_percent / 100.0
                subtotal_sel = total_amount_sel - discount_amt_sel
                
                # ✅ NEW: pull GST info if present; fallback to 0
                gst_percent_sel = float(invoice_items[0].get("GST%", 0) or 0)
                gst_amount_sel = float(invoice_items[0].get("GST Amt", 0) or 0)
                
                # ✅ The sheet's "Final Total (Invoice)" is GST-inclusive after our change
                grand_total_sel = float(invoice_items[0].get("Final Total (Invoice)", subtotal_sel + gst_amount_sel) or (subtotal_sel + gst_amount_sel))

            
                invoice_no, stall_no, date_str, ph_no, artisan_code, payment_method, items
                invoice_no_bkp, stall_no_bkp, date_bkp, ph_bkp, art_bkp, pm_bkp, items_bkp = (
                    invoice_no,
                    stall_no,
                    date_str,
                    ph_no,
                    artisan_code,
                    payment_method,
                    items,
                )
                invoice_no, stall_no, date_str, ph_no, artisan_code, payment_method, items = (
                    invoice_no_sel,
                    stall_no_sel,
                    date_sel,
                    ph_sel,
                    artisan_sel,
                    pm_sel,
                    items_copy,
                )

                buf2 = BytesIO()
                height2 = 250 + 15 * len(items)
                pdf2 = canvas.Canvas(buf2, pagesize=(200, height2), bottomup=0)
                _draw_page(
                    pdf2,
                    "INVOICE",
                    {
                        "total_amount": total_amount_sel,
                        "discount_amt": discount_amt_sel,
                        "grand_total": grand_total_sel,
                        "gst_percent": gst_percent_sel,
                        "gst_amount": gst_amount_sel,
                        "grand_total_incl_gst": grand_total_sel,
                    },
                )
                pdf2.showPage()
                _draw_page(
                    pdf2,
                    "ARTISAN SLIP",
                    {
                        "total_amount": total_amount_sel,
                        "discount_amt": discount_amt_sel,
                        "grand_total": grand_total_sel,
                        "gst_percent": gst_percent_sel,
                        "gst_amount": gst_amount_sel,
                        "grand_total_incl_gst": grand_total_sel,
                    },
                )
                pdf2.save(); buf2.seek(0)
                st.download_button("📥 Download Re-Generated PDF", buf2, file_name=f"{invoice_no_sel}_copy.pdf")

                # Restore
                invoice_no, stall_no, date_str, ph_no, artisan_code, payment_method, items = (
                    invoice_no_bkp,
                    stall_no_bkp,
                    date_bkp,
                    ph_bkp,
                    art_bkp,
                    pm_bkp,
                    items_bkp,
                )
        else:
            st.info("No invoice records found.")


# =====================
# 10) Sales Dashboard (Admin/Master)
# =====================
if is_admin or is_master:
    st.subheader("📊 Sales Dashboard")
    with st.expander("View Sales Analytics"):
        df = fetch_sheet_df()
        if not df.empty:
            if "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")

            st.markdown("### Summary Stats")
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Revenue", f"₹{df['Final Total (Item)'].sum():,.2f}")
            col2.metric("Total Items Sold", int(df["Qty"].sum()))
            col3.metric("Total Invoices", df["Invoice No"].nunique())

            st.plotly_chart(
                px.bar(
                    df.groupby("Date")["Final Total (Item)"].sum().reset_index(),
                    x="Date",
                    y="Final Total (Item)",
                    title="Revenue Over Time",
                    color_discrete_sequence=["green"],
                ),
                use_container_width=True,
            )
            st.plotly_chart(
                px.bar(
                    df.groupby("Item")["Qty"].sum().sort_values(ascending=False).head(10).reset_index(),
                    x="Item",
                    y="Qty",
                    title="Top-Selling Items",
                    color_discrete_sequence=["#FFD700"],
                ),
                use_container_width=True,
            )
            st.plotly_chart(
                px.bar(
                    df.groupby("Stall No")["Final Total (Item)"].sum().sort_values(ascending=False).reset_index(),
                    x="Stall No",
                    y="Final Total (Item)",
                    title="Stall-wise Revenue",
                    color_discrete_sequence=["#FF0000"],
                ),
                use_container_width=True,
            )
            st.plotly_chart(
                px.bar(
                    df.groupby("Stall No")["Discount%"].mean().reset_index(),
                    x="Stall No",
                    y="Discount%",
                    title="Average Discount per Stall",
                    color_discrete_sequence=["#FF69B4"],
                ),
                use_container_width=True,
            )
            df["Discount Amt"] = df["Price"] * df["Qty"] * (df["Discount%"] / 100)
            st.plotly_chart(
                px.bar(
                    df.groupby("Stall No")["Discount Amt"].sum().reset_index(),
                    x="Stall No",
                    y="Discount Amt",
                    title="Total Discount ₹ Given per Stall",
                    color_discrete_sequence=["#FFA500"],
                ),
                use_container_width=True,
            )
            rev_items = (
                df.groupby("Item")["Final Total (Item)"].sum().sort_values(ascending=False).reset_index()
            )
            st.plotly_chart(
                px.pie(
                    rev_items.head(10),
                    values="Final Total (Item)",
                    names="Item",
                    title="Revenue Share by Item",
                ),
                use_container_width=True,
            )
                        # 🔹 Revenue by Payment Method
            st.plotly_chart(
                px.bar(
                    df.groupby("Payment Method")["Final Total (Item)"].sum().reset_index(),
                    x="Payment Method",
                    y="Final Total (Item)",
                    title="Revenue by Payment Method",
                    color_discrete_sequence=["#1f77b4"]
                ),
                use_container_width=True
            )

            # 🔹 Revenue by Corporation
            if "Corporation" in df.columns:
                st.plotly_chart(
                    px.bar(
                        df.groupby("Corporation")["Final Total (Item)"].sum().reset_index(),
                        x="Corporation",
                        y="Final Total (Item)",
                        title="Revenue by Corporation",
                        color_discrete_sequence=["#2ca02c"]
                    ),
                    use_container_width=True
                )

        else:
            st.info("No sales data found.")


# =====================
# 11) User Management (Master)
# =====================
if is_master:
    st.subheader("👤 User Management")

    # ---- Assign Location ----
    st.subheader("Assign Location to User")
    usernames_list = list(config["credentials"]["usernames"].keys())
    selected_user = st.selectbox("Select User", usernames_list)
    current_location = config["credentials"]["usernames"][selected_user].get("location", "")
    location_input = st.text_input(f"Enter Location for {selected_user}", value=current_location)

    if st.button("Save Location"):
        config["credentials"]["usernames"][selected_user]["location"] = location_input
        with open("config.yaml", "w") as f:
            yaml.safe_dump(config, f, sort_keys=False)
        update_config_on_github(config)
        st.success(f"Location '{location_input}' assigned to '{selected_user}'")
        st.rerun()

    # ---- Existing Users ----
    st.subheader("Existing Users")
    users_data = []
    for uname, details in config["credentials"]["usernames"].items():
        users_data.append([uname, details["name"], details["role"], details.get("location", "—")])
    st.table(pd.DataFrame(users_data, columns=["Username", "Name", "Role", "Location"]))

    
    st.markdown("---")
    st.markdown("### 🔐 Reset or Change User Password")

    with st.form("reset_password_form"):
        existing_users = list(config["credentials"]["usernames"].keys())
        selected_user2 = st.selectbox("Select User", existing_users)
        selected_role2 = config["credentials"]["usernames"][selected_user2]["role"]

        current_pass_input = ""
        if selected_role2 == "master":
            current_pass_input = st.text_input(
                "Enter Current Password (required for master user)", type="password"
            )

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
            elif selected_role2 == "master":
                stored_hash = config["credentials"]["usernames"][selected_user2]["password"]
                if not bcrypt.checkpw(current_pass_input.encode(), stored_hash.encode("utf-8")):
                    st.error("🚫 Incorrect current password.")
                else:
                    hashed_pass = stauth.Hasher([new_pass]).generate()[0]
                    config["credentials"]["usernames"][selected_user2]["password"] = hashed_pass
                    with open("config.yaml", "w") as f:
                        yaml.safe_dump(config, f, sort_keys=False)
                    update_config_on_github(config)
                    st.success(f"✅ Password for master user '{selected_user2}' has been updated.")
                    st.rerun()
            else:
                hashed_pass = stauth.Hasher([new_pass]).generate()[0]
                config["credentials"]["usernames"][selected_user2]["password"] = hashed_pass
                with open("config.yaml", "w") as f:
                    yaml.safe_dump(config, f, sort_keys=False)
                update_config_on_github(config)
                st.success(f"✅ Password for user '{selected_user2}' has been updated.")
    st.markdown("### ❌ Delete Existing User")

    with st.form("delete_user_form"):
        existing_users = list(config["credentials"]["usernames"].keys())
        selected_user_to_delete = st.selectbox("Select User to Delete", existing_users)
    
        confirm_delete = st.checkbox(f"Yes, delete user '{selected_user_to_delete}' permanently")
        delete_btn = st.form_submit_button("Delete User")
    
        if delete_btn:
            if confirm_delete:
                # Prevent master from deleting themselves
                if config["credentials"]["usernames"][selected_user_to_delete]["role"] == "master":
                    st.error("🚫 Master user cannot be deleted.")
                else:
                    del config["credentials"]["usernames"][selected_user_to_delete]
                    update_config_on_github(config)
                    st.success(f"✅ User '{selected_user_to_delete}' has been deleted.")
                    st.rerun()
            else:
                st.warning("Please confirm deletion before proceeding.")

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
            elif new_username in config["credentials"]["usernames"]:
                st.error("🚫 Username already exists.")
            elif len(new_password) < 6:
                st.error("🔐 Password must be at least 6 characters.")
            else:
                hashed_password = stauth.Hasher([new_password]).generate()[0]
                config["credentials"]["usernames"][new_username] = {
                    "name": new_name,
                    "password": hashed_password,
                    "role": new_role,
                }
                with open("config.yaml", "w") as f:
                    yaml.safe_dump(config, f, sort_keys=False)
                update_config_on_github(config)
                st.success(
                    f"✅ User '{new_username}' with role '{new_role}' created successfully."
                )
                st.rerun()


# =====================
# 12) Invoice Search & CSV Export (Admin/Master)
# =====================
if is_admin or is_master:
    st.sidebar.markdown("### 📂 Invoice Search & Export")
    df = fetch_sheet_df()

    if not df.empty:
        # ---- Filters ----
        corp_filter = st.sidebar.multiselect(
            "🏢 Corporation", sorted(df["Corporation"].unique())
        )
        stall_filter = st.sidebar.multiselect(
            "🔎 Filter by Stall No", sorted(df["Stall No"].dropna().unique())
        )
        payment_filter = st.sidebar.multiselect(
            "💰 Payment Method", sorted(df["Payment Method"].dropna().unique())
        )
        status_filter = st.sidebar.multiselect(
            "📌 Status", sorted(df["Status"].dropna().unique())
        )
        use_date_filter = st.sidebar.checkbox("📅 Enable Date Filter", value=False)
        if use_date_filter:
            start_date = st.sidebar.date_input("Start Date")
            end_date = st.sidebar.date_input("End Date")

        # ---- Apply Filters ----
        filtered_df = df.copy()
        if "Date" in filtered_df.columns:
            filtered_df["Date"] = pd.to_datetime(
                filtered_df["Date"], dayfirst=True, errors="coerce"
            )
        if corp_filter:
            filtered_df = filtered_df[filtered_df["Corporation"].isin(corp_filter)]
        if stall_filter:
            filtered_df = filtered_df[filtered_df["Stall No"].isin(stall_filter)]
        if payment_filter:
            filtered_df = filtered_df[filtered_df["Payment Method"].isin(payment_filter)]
        if status_filter:
            filtered_df = filtered_df[filtered_df["Status"].isin(status_filter)]
        if use_date_filter:
            if start_date:
                filtered_df = filtered_df[
                    filtered_df["Date"] >= pd.to_datetime(start_date)
                ]
            if end_date:
                filtered_df = filtered_df[
                    filtered_df["Date"] <= pd.to_datetime(end_date)
                ]

        # ---- Preview & CSV Export (single button) ----
        st.sidebar.markdown(f"Showing **{len(filtered_df)}** filtered entries.")
        csv_bytes = filtered_df.to_csv(index=False).encode("utf-8")
        st.sidebar.download_button(
            "📤 Download CSV",
            data=csv_bytes,
            file_name="invoices_export.csv",
            mime="text/csv",
        )
    else:
        st.sidebar.info("No data available for filtering/export.")


# =====================
# 13) Footer
# =====================
st.markdown(
    """
    <hr style='margin-top: 50px;'>
    <div style='text-align: center; font-size: 12px; color: gray;'>
        Invoicing System Developed by <b>Rakesh Chourasia</b> |
        <a href='https://www.linkedin.com/feed/' target='_blank'>LinkedIn</a>
    </div>
    """,
    unsafe_allow_html=True,
)

import streamlit as st
import pandas as pd
from io import BytesIO

# -----------------------------
# BASIC CONFIG
# -----------------------------
st.set_page_config(page_title="FAOA 501(c)(3) Treasurer Tool", layout="wide")

st.title("FAOA Bank Statement → IRS Category Classifier")

st.markdown("""
Upload a **CSV or Excel** bank statement and this tool will:

1. Classify each transaction into IRS Form 1023/990-EZ categories  
2. Produce a summary of totals by IRS category  
3. Let you download the categorized data and the summary as CSV files  

**Assumptions for v1:**
- Your export has at least `Description` and `Amount` columns  
- Deposits are positive; withdrawals are negative in `Amount`  
- No bank data is stored in this app – everything is processed in memory only
""")

# -----------------------------
# (Optional) SIMPLE PASSWORD GATE
# -----------------------------
# If you set APP_PASSWORD in Streamlit "Secrets", this will enforce it.
APP_PASSWORD = st.secrets.get("APP_PASSWORD", "")

if APP_PASSWORD:
    pwd = st.text_input("Enter FAOA treasurer password", type="password")
    if pwd != APP_PASSWORD:
        st.stop()  # Do not proceed until password is correct

# -----------------------------
# IRS CATEGORY LABELS
# -----------------------------
CATEGORY_LABELS = {
    "1": "1 - Gifts, grants, contributions received",
    "2": "2 - Membership fees received",
    "3": "3 - Gross investment income",
    "4": "4 - Net unrelated business income",
    "6": "6 - Value of services/facilities furnished by government",
    "7": "7 - Other revenue",
    "9": "9 - Gross receipts from activities related to exempt purpose",
    "14": "14 - Fundraising expenses",
    "15": "15 - Contributions, gifts, grants paid out",
    "16": "16 - Disbursements to/for members",
    "18": "18 - Other salaries and wages",
    "19": "19 - Interest expense",
    "22": "22 - Professional fees",
    "23": "23 - Other expenses not classified above",
}


# -----------------------------
# CORE CLASSIFICATION LOGIC
# -----------------------------
def categorize_row(row: pd.Series) -> str:
    """
    Apply FAOA -> IRS classification rules based on transaction description and amount.
    This is v1 and can be refined over time as you tighten your bookkeeping rules.
    """
    desc = str(row.get("Description", "")).lower()
    amount_raw = row.get("Amount", 0)

    try:
        amount = float(amount_raw)
    except Exception:
        amount = 0.0

    # ---- REVENUE RULES ----

    # Membership via Affinipay & Stripe
    if "affinipay" in desc:
        return CATEGORY_LABELS["2"]

    if "stripe" in desc and amount > 0:
        # Journal subscription vs membership threshold
        if abs(amount) < 20:
            # Treat as exempt-purpose receipts (journal subs)
            return CATEGORY_LABELS["9"]
        else:
            # Membership dues
            return CATEGORY_LABELS["2"]

    # Corporate sponsorships / donations (wire or check)
    if any(x in desc for x in ["sponsorship", "sponsor", "corp sponsor", "donation", "donor"]):
        return CATEGORY_LABELS["1"]

    # Interest income
    if "interest" in desc and amount > 0:
        return CATEGORY_LABELS["3"]

    # ---- EXPENSE RULES ----

    # SaaS / tech tools (WildApricot, ConvertKit, Squarespace, Network Solutions, Apple.com, etc.)
    if any(x in desc for x in ["wildapricot", "convertkit", "kit.com", "squarespace",
                               "networksolutio", "apple.com"]):
        # Treat as other operating expenses
        return CATEGORY_LABELS["23"]

    # Fundraising materials / printing / gala venue / banquet
    if any(x in desc for x in ["minuteman press", "upprinting", "printing",
                               "fundraising", "gala", "banquet", "ballroom"]):
        return CATEGORY_LABELS["14"]

    # Awards donated out to PME institutions (Maxter Group, Awards Recognition, etc.)
    if any(x in desc for x in ["awards recognition", "maxter group"]):
        return CATEGORY_LABELS["15"]

    # Chapter events & member-focused events / withdrawals
    if any(x in desc for x in [
        "chapter event",
        "cash withdrawal",
        "atm withdrawal",
        "chapter dinner",
        "chapter lunch",
        "chapter meeting"
    ]):
        return CATEGORY_LABELS["16"]

    # Professional fees – legal, accounting, consulting
    if any(x in desc for x in ["cooley", "legal", "attorney", "law firm",
                               "cpa", "accounting", "bookkeeping", "consulting fee"]):
        return CATEGORY_LABELS["22"]

    # Payment processor / merchant fees (credit card processing, gateways)
    if any(x in desc for x in ["authnet gateway", "bkcrd fees", "merchant fee",
                               "cardconnect", "processing fee"]):
        return CATEGORY_LABELS["23"]

    # Interest expense (credit card or loan interest)
    if "interest" in desc and amount < 0:
        return CATEGORY_LABELS["19"]

    # ---- DEFAULTS ----
    if amount > 0:
        # Unmatched deposits → other revenue (you can tighten later)
        return CATEGORY_LABELS["7"]
    else:
        # Unmatched withdrawals → other expenses
        return CATEGORY_LABELS["23"]


# -----------------------------
# FILE UPLOAD
# -----------------------------
uploaded_file = st.file_uploader(
    "Upload a bank statement (CSV or Excel)", type=["csv", "xlsx"]
)

if uploaded_file is None:
    st.info("Upload a bank statement file to begin.")
    st.stop()

# Read the file into a DataFrame
try:
    if uploaded_file.name.lower().endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)
except Exception as e:
    st.error(f"Could not read file: {e}")
    st.stop()

st.subheader("Raw data preview")
st.dataframe(df.head())

# -----------------------------
# BASIC COLUMN CHECK
# -----------------------------
required_cols = ["Description", "Amount"]
missing = [c for c in required_cols if c not in df.columns]

if missing:
    st.error(
        f"Missing required columns: {missing}. "
        f"Make sure your exported file has at least 'Description' and 'Amount' columns."
    )
    st.stop()

# -----------------------------
# APPLY CLASSIFICATION
# -----------------------------
df["IRS Category"] = df.apply(categorize_row, axis=1)

st.subheader("Categorized transactions (preview)")
st.dataframe(df.head(50))

# -----------------------------
# SUMMARY BY IRS CATEGORY
# -----------------------------
summary = (
    df.groupby("IRS Category")["Amount"]
    .sum()
    .reset_index()
    .sort_values("IRS Category")
)

st.subheader("Totals by IRS category")
st.dataframe(summary)

# -----------------------------
# DOWNLOAD HELPERS
# -----------------------------
def to_csv_bytes(dataframe: pd.DataFrame) -> bytes:
    return dataframe.to_csv(index=False).encode("utf-8")


categorized_csv = to_csv_bytes(df)
summary_csv = to_csv_bytes(summary)

st.download_button(
    label="Download categorized transactions CSV",
    data=categorized_csv,
    file_name="faoa_categorized_statement.csv",
    mime="text/csv",
)

st.download_button(
    label="Download IRS category totals CSV",
    data=summary_csv,
    file_name="faoa_irs_category_totals.csv",
    mime="text/csv",
)

st.success("Processing complete. You can now download the categorized data and totals.")

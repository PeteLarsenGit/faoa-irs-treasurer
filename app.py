import streamlit as st
import pandas as pd

st.set_page_config(page_title="FAOA 501(c)(3) Treasurer Tool", layout="wide")

st.title("FAOA Bank Statement → IRS Category Classifier")

st.markdown("""
Upload a **CSV or Excel** bank statement and this tool will:

1. Classify each transaction into IRS Form 1023/990-EZ categories  
2. Produce a summary of totals by IRS category  
3. Let you download the categorized data and the summary as CSV files  

**Assumptions for this tool:**
- Your file has at least two columns:  
  - `Description` – text description of the transaction  
  - `Amount` – deposits positive, withdrawals negative  
- No bank data is stored by this app – everything is processed in memory only.
""")

with st.expander("If your statement is a PDF – how to convert it to CSV with an AI assistant"):
    st.markdown("""
If your bank only gives you a **PDF statement**, do this before using this tool:

1. Open an AI assistant (e.g., ChatGPT) that supports **file upload**.  
2. Upload your **PDF bank statement**.  
3. Use a prompt like this:

> **Sample prompt:**  
> *“You are a data assistant. I will upload a PDF bank statement.  
> Extract all **transaction rows** and convert them into a **CSV table** with the exact headers:  
> `Date,Description,Amount`.  
> - One row per transaction.  
> - `Amount` must be numeric (no `$` or commas).  
> - Deposits should be **positive** numbers, withdrawals or debits should be **negative** numbers.  
> - Do **not** include beginning/ending balances, running balance columns, account numbers, page headers, or footers.  
> Return only raw CSV text, with the first line as the header row, no extra commentary or markdown.”*

4. Copy the CSV text the AI gives you into a `.csv` file, or download it if the AI offers a file.  
5. Upload that CSV file into this FAOA tool.
""")

# -----------------------------
# SIMPLE PASSWORD GATE (optional)
# -----------------------------
APP_PASSWORD = st.secrets.get("APP_PASSWORD", "")

if APP_PASSWORD:
    pwd = st.text_input("Enter FAOA treasurer password", type="password")
    if pwd != APP_PASSWORD:
        st.stop()

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
def classify_row(row: pd.Series) -> tuple[str, bool]:
    """
    Return (IRS category label, needs_review).

    needs_review = True  => we fell back to 'other' and want treasurer input.
    needs_review = False => rule-based, no treasurer action required.
    """
    desc = str(row.get("Description", "")).lower()
    amount_raw = row.get("Amount", 0)

    try:
        amount = float(amount_raw)
    except Exception:
        amount = 0.0

    # ---- HARD IGNORE: balance / non-transaction rows ----
    if "balance" in desc and not any(
        kw in desc for kw in ["deposit", "withdrawal", "paid from", "pos debit", "ach"]
    ):
        return "IGNORE", False

    # -----------------
    # REVENUE RULES
    # -----------------

    # Membership via Affinipay
    if "affinipay" in desc and amount > 0:
        return CATEGORY_LABELS["2"], False

    # Stripe transfers – journal vs membership (cutoff = $9)
    if "stripe transfer" in desc and amount > 0:
        if abs(amount) < 9:  # < $9 → Category 9, >= $9 → Category 2
            return CATEGORY_LABELS["9"], False
        else:
            return CATEGORY_LABELS["2"], False

    # Corporate sponsorships / donations
    if any(x in desc for x in ["sponsorship", "sponsor", "corp sponsor", "donation", "donor"]):
        return CATEGORY_LABELS["1"], False

    # Interest income
    if "interest" in desc and amount > 0:
        return CATEGORY_LABELS["3"], False

    # -----------------
    # EXPENSE RULES
    # -----------------

    # SaaS / tech subscriptions (WildApricot, Airtable, Squarespace, etc.)
    if any(x in desc for x in [
        "wild apricot", "wildapricot",
        "convertkit", "kit.com",
        "squarespace",
        "airtable.com", "airtable",
        "networksolutio", "network solutions",
        "apple.com"
    ]):
        return CATEGORY_LABELS["23"], False  # known SaaS / operating expense

    # Fundraising materials / gala / printing
    if any(x in desc for x in [
        "minuteman press", "upprinting", "printing",
        "fundraising", "gala", "banquet", "ballroom"
    ]):
        return CATEGORY_LABELS["14"], False

    # Awards donated to PME (Maxter Group, Awards Recognition)
    if any(x in desc for x in ["awards recognition", "maxter group"]):
        return CATEGORY_LABELS["15"], False

    # Chapter events / member-focused events
    if any(x in desc for x in [
        "chapter event", "chapter dinner", "chapter lunch", "chapter meeting",
        "paypal *sam", "paypal sam"
    ]):
        return CATEGORY_LABELS["16"], False

    # Professional fees – contractors, Upwork, legal, accounting
    if any(x in desc for x in [
        "cooley", "legal", "attorney", "law firm",
        "cpa", "accounting", "bookkeeping",
        "consulting fee", "upwork"
    ]):
        return CATEGORY_LABELS["22"], False

    # Payment processor / merchant fees
    if any(x in desc for x in [
        "authnet gateway", "bkcrd fees", "merchant fee",
        "cardconnect", "processing fee"
    ]):
        return CATEGORY_LABELS["23"], False

    # Interest expense
    if "interest" in desc and amount < 0:
        return CATEGORY_LABELS["19"], False

    # -----------------
    # FALLBACKS → TREASURER REVIEW REQUIRED
    # -----------------
    if amount > 0:
        # Unmatched deposits → other revenue (needs review)
        return CATEGORY_LABELS["7"], True
    else:
        # Unmatched withdrawals → other expenses (needs review)
        return CATEGORY_LABELS["23"], True


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

# Clean up obvious junk rows
if "Description" in df.columns:
    df = df.dropna(subset=["Description"])
    df = df[~df["Description"].str.contains("balance", case=False, na=False)]

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
result = df.apply(classify_row, axis=1, result_type="expand")
result.columns = ["IRS Category", "Needs Review"]
df[["IRS Category", "Needs Review"]] = result

# Drop ignored rows (balances, etc.)
df = df[df["IRS Category"] != "IGNORE"]

st.subheader("Categorized transactions (initial pass)")
st.dataframe(df.head(50))

# -----------------------------
# MANUAL REVIEW FOR UNCLASSIFIED ITEMS
# -----------------------------
review_df = df[df["Needs Review"]]

if not review_df.empty:
    st.subheader("Transactions requiring manual classification")

    st.write(
        "These did not match any automatic rule. "
        "Use the dropdown to choose the correct IRS category for each transaction."
    )

    cat_options = list(CATEGORY_LABELS.values())

    review_df = st.data_editor(
        review_df,
        column_config={
            "IRS Category": st.column_config.SelectboxColumn(
                "IRS Category",
                options=cat_options,
            )
        },
        num_rows="fixed",
        use_container_width=True,
        key="review_editor",
    )

    # Update main df with treasurer's selections
    df.update(review_df)

# After manual edits, we no longer care about Needs Review flag
df = df.drop(columns=["Needs Review"])

st.subheader("Final categorized transactions")
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

st.success("Processing complete. Review any manually-classified rows, then download the categorized data and totals.")

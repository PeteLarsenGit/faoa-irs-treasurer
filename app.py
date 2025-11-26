import streamlit as st
import pandas as pd
import io

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
# MONTH / YEAR INPUTS (REQUIRED FOR EXPORT)
# -----------------------------
st.markdown("### Report period")

MONTHS = [
    ("January", 1),
    ("February", 2),
    ("March", 3),
    ("April", 4),
    ("May", 5),
    ("June", 6),
    ("July", 7),
    ("August", 8),
    ("September", 9),
    ("October", 10),
    ("November", 11),
    ("December", 12),
]

month_name = st.selectbox(
    "Report month",
    options=[m[0] for m in MONTHS],
    index=0,
    help="Select the month this bank statement covers (use the month the statement ENDS in).",
)
month_number = dict(MONTHS)[month_name]

year = st.number_input(
    "Report year",
    min_value=2000,
    max_value=2100,
    value=2024,
    step=1,
    help="Enter the calendar year for this statement (e.g., 2024).",
)

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

# Threshold to treat a non-membership deposit as a likely sponsorship
LARGE_SPONSOR_THRESHOLD = 500.0  # adjust if you want a different cutoff

# -----------------------------
# CORE CLASSIFICATION LOGIC
# -----------------------------
def classify_row(row: pd.Series) -> tuple[str, bool, bool]:
    """
    Return (IRS category label, needs_review, potential_sponsorship).

    needs_review = True  => we want treasurer input.
    potential_sponsorship = True => large positive deposit likely to be a sponsor.
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
        return "IGNORE", False, False

    # ---- INTERNAL TRANSFERS TO/FROM SAVINGS (IGNORE) ----
    if "transfer" in desc and "savings" in desc:
        # Internal move between checking and savings; not revenue or expense
        return "IGNORE", False, False

    potential_sponsorship = False

    # -----------------
    # REVENUE RULES
    # -----------------

    # Membership via Affinipay
    if "affinipay" in desc and amount > 0:
        return CATEGORY_LABELS["2"], False, False

    # Stripe transfers – journal vs membership (cutoff = $9)
    if "stripe transfer" in desc and amount > 0:
        if abs(amount) < 9:  # < $9 → Category 9, >= $9 → Category 2
            return CATEGORY_LABELS["9"], False, False
        else:
            return CATEGORY_LABELS["2"], False, False

    # Corporate sponsorships / donations (explicitly identifiable)
    if any(x in desc for x in ["sponsorship", "sponsor", "corp sponsor", "donation", "donor"]):
        # Explicit donations → Category 1, still want sponsor name
        return CATEGORY_LABELS["1"], True, True

    # Interest income
    if "interest" in desc and amount > 0:
        return CATEGORY_LABELS["3"], False, False

    # -----------------
    # EXPENSE RULES
    # -----------------

    # Professional fees – contractors, SaaS we treat as professional, legal, accounting, etc.
    if any(x in desc for x in [
        "cooley", "legal", "attorney", "law firm",
        "cpa", "accounting", "bookkeeping",
        "consulting fee", "upwork",
        "airtable.com", "airtable",
        "g suite", "gsuite", "google workspace", "google*gsuite",
        "wild apricot", "wildapricot",
        "squarespace",
        "authnet gateway",
        "affinipay", "affinipayllc",
    ]):
        return CATEGORY_LABELS["22"], False, False

    # SaaS / tech subscriptions that are NOT in the explicit professional-fee list above
    # (e.g., ConvertKit, Squarespace clones, generic hosting if added later)
    if any(x in desc for x in [
        "convertkit", "kit.com",
        "networksolutio", "network solutions",
        "apple.com"
    ]):
        return CATEGORY_LABELS["23"], False, False  # known SaaS / operating expense

    # Awards donated to PME (Maxter Group, Awards Recognition)
    if any(x in desc for x in ["awards recognition", "maxter group"]):
        return CATEGORY_LABELS["15"], False, False

    # Chapter events / member-focused events
    # Always require review + label, since these sometimes should be fundraising (14).
    if any(x in desc for x in [
        "chapter event", "chapter dinner", "chapter lunch", "chapter meeting",
        "paypal *sam", "paypal sam"
    ]):
        return CATEGORY_LABELS["16"], True, False  # Needs Review for 16

    # Payment processor / merchant fees (non-Affinipay/Authnet, which we treated as 22 above)
    if any(x in desc for x in [
        "bkcrd fees", "merchant fee",
        "cardconnect", "processing fee"
    ]):
        return CATEGORY_LABELS["23"], False, False

    # Interest expense
    if "interest" in desc and amount < 0:
        return CATEGORY_LABELS["19"], False, False

    # -----------------
    # FALLBACKS → TREASURER REVIEW REQUIRED
    # -----------------
    if amount > 0:
        # Large unknown positive deposit → likely sponsorship
        if amount >= LARGE_SPONSOR_THRESHOLD:
            potential_sponsorship = True
            return CATEGORY_LABELS["1"], True, potential_sponsorship
        # Smaller unknown revenue
        return CATEGORY_LABELS["7"], True, False
    else:
        # Unmatched withdrawals → other expenses (needs review; may be flagged for further investigation)
        return CATEGORY_LABELS["23"], True, False


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

# Ensure Date column exists for exports
if "Date" not in df.columns:
    df["Date"] = ""

# Ensure helper columns exist
for col in [
    "Member/Event Label",
    "Event Location",
    "Event Purpose",
    "Sponsor Name",
    "Itemization Label",
    "Needs Further Investigation",
]:
    if col not in df.columns:
        if col == "Needs Further Investigation":
            df[col] = False
        else:
            df[col] = ""

# Attach Month / Year to every row
df["Month"] = month_number
df["Year"] = int(year)

# -----------------------------
# APPLY CLASSIFICATION
# -----------------------------
result = df.apply(classify_row, axis=1, result_type="expand")
result.columns = ["IRS Category", "Needs Review", "Potential Sponsorship"]
df[["IRS Category", "Needs Review", "Potential Sponsorship"]] = result

# Drop ignored rows (balances, internal savings transfers, etc.)
df = df[df["IRS Category"] != "IGNORE"]

# Force review for categories that require itemization:
# 7 (Other revenue), 9 (Gross receipts from exempt purpose),
# 15 (Contributions paid out), 16 (Disbursements to/for members), 23 (Other expenses)
df["Needs Review"] = df["Needs Review"] | df["IRS Category"].str.startswith(
    ("7 ", "9 ", "15 ", "16 ", "23 ")
)

st.subheader("Categorized transactions (initial pass)")
st.dataframe(df.head(50))

# -----------------------------
# MANUAL RECONCILIATION SECTION
# -----------------------------
review_df = df[df["Needs Review"]]

if not review_df.empty:
    st.subheader("Manual Reconciliation")

    st.markdown("""
The rows below **must** be reviewed and reconciled:

- **Category 7 – OTHER REVENUE:**  
  Use **Itemization Label** to group similar types (e.g., `Journal ads`, `Journal subscriptions`, `Misc reimbursements`).  

- **Category 9 – GROSS RECEIPTS FROM EXEMPT PURPOSE:**  
  Use **Itemization Label** to identify each **program service revenue source** (e.g., `FAOA Journal Sales`, `Bridge Program Fees`).  

- **Category 15 – CONTRIBUTIONS/GIFTS/GRANTS PAID OUT:**  
  Use **Itemization Label** to capture **vendor or grant type** (e.g., `Grant – NDU`, `Scholarship – AFIT`).  

- **Category 16 – DISBURSEMENTS TO/FOR MEMBERS:**  
  These are **individual events**. For each row:  
  - Fill **Member/Event Label** (e.g., `Hawaii Chapter Event – Mar 2024`)  
  - Fill **Event Location** (e.g., `Honolulu, HI`)  
  - Fill **Event Purpose** (e.g., `Networking & professional development`)  

- **Category 23 – OTHER EXPENSES NOT CLASSIFIED ABOVE:**  
  Use **Itemization Label** to describe the type (e.g., `Website hosting`, `SaaS – WildApricot`, `Bank fees`).  
  If a transaction **cannot be clearly associated with any known or documented FAOA activity or purpose**,  
  set **Needs Further Investigation** to **True** (Treasurer deems further investigation).

- **Potential Sponsorships (large deposits):**  
  Any row with **“Potential Sponsorship = True”** is a large deposit that is **probably a sponsorship**.  
  - Confirm the IRS Category is **1 - Gifts, grants, contributions received** (or adjust if wrong).  
  - Enter the **Sponsor Name** (e.g., `Boeing`, `Lockheed Martin`) so we can send tax documentation later.

**How to edit:**  
- Click once in the **IRS Category** cell to reveal the dropdown, then choose the correct category.  
- The columns are ordered as: Date, Description, Amount, IRS Category, Needs Further Investigation, …  
- Use the check box for **Needs Further Investigation** when appropriate.  
- Fill in the text fields where applicable.
""")

    cat_options = list(CATEGORY_LABELS.values())

    # Reorder columns so IRS Category is 5th and Needs Further Investigation is 6th
    desired_order = [
        "Date",
        "Description",
        "Amount",
        "IRS Category",
        "Needs Further Investigation",
        "Member/Event Label",
        "Event Location",
        "Event Purpose",
        "Sponsor Name",
        "Itemization Label",
        "Potential Sponsorship",
    ]
    existing_cols = list(review_df.columns)
    ordered_cols = [c for c in desired_order if c in existing_cols] + [
        c for c in existing_cols if c not in desired_order
    ]
    review_df = review_df[ordered_cols]

    review_df = st.data_editor(
        review_df,
        column_config={
            "IRS Category": st.column_config.SelectboxColumn(
                "IRS Category (click cell for dropdown)",
                options=cat_options,
                help="Click once in the cell, then choose from the dropdown list.",
            ),
            "Itemization Label": st.column_config.TextColumn(
                "Itemization Label (for 7, 9, 15, 23)",
                help="Short label for consolidated itemization (e.g., 'Journal ads', 'Grant – NDU', 'Website hosting').",
            ),
            "Member/Event Label": st.column_config.TextColumn(
                "Member/Event Label (for Category 16 items)",
                help="Name the event, e.g., 'Hawaii Chapter Event – Mar 2024'.",
            ),
            "Event Location": st.column_config.TextColumn(
                "Event Location (for Category 16 items)",
                help="Where the event took place, e.g., 'Honolulu, HI').",
            ),
            "Event Purpose": st.column_config.TextColumn(
                "Event Purpose (for Category 16 items)",
                help="Short purpose, e.g., 'Networking & professional development'.",
            ),
            "Sponsor Name": st.column_config.TextColumn(
                "Sponsor Name (for Category 1 items)",
                help="Enter the sponsor/donor name for tax documentation, e.g., 'Boeing'.",
            ),
            "Potential Sponsorship": st.column_config.CheckboxColumn(
                "Potential Sponsorship",
                help="True means this is a large deposit likely to be a sponsorship.",
                disabled=True,
            ),
            "Needs Further Investigation": st.column_config.CheckboxColumn(
                "Needs Further Investigation",
                help="Set to True if the treasurer deems this transaction requires further investigation.",
            ),
        },
        num_rows="fixed",
        use_container_width=True,
        key="review_editor",
    )

    # Update main df with treasurer's selections
    df.update(review_df)

# After manual edits, we no longer care about Needs Review flag in outputs
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

# Attach Month / Year to summary (for annual consolidation)
summary["Month"] = month_number
summary["Year"] = int(year)

st.subheader("Totals by IRS category")
st.dataframe(summary)

# -----------------------------
# EXPORTS
# -----------------------------
def build_monthly_activity_csv(df: pd.DataFrame) -> bytes:
    """
    Machine-readable FAOA Monthly Financial Activity Report:
    - One row per transaction
    - Includes Month, Year, IRS Category code & label, and all itemization fields
    This is what you'll import 1–12 of into the annual consolidation tool.
    """
    out = df.copy()

    # Split IRS Category into code + label
    out["IRS Category Code"] = out["IRS Category"].str.split(" ", n=1).str[0]
    out["IRS Category Label"] = (
        out["IRS Category"].str.split(" ", n=1).str[1].str.lstrip("- ").fillna("")
    )

    # Ensure consistent column order
    cols_order = [
        "Year",
        "Month",
        "Date",
        "Description",
        "Amount",
        "IRS Category Code",
        "IRS Category Label",
        "Itemization Label",
        "Member/Event Label",
        "Event Location",
        "Event Purpose",
        "Sponsor Name",
        "Potential Sponsorship",
        "Needs Further Investigation",
    ]

    # Ensure all required columns exist
    for c in cols_order:
        if c not in out.columns:
            if c in ["Amount", "Month", "Year"]:
                out[c] = 0
            elif c in ["Potential Sponsorship", "Needs Further Investigation"]:
                out[c] = False
            else:
                out[c] = ""

    out = out[cols_order]

    return out.to_csv(index=False).encode("utf-8")


def build_full_export_with_itemization(df: pd.DataFrame) -> bytes:
    """
    Human/IRS-style CSV:
    1) Full transaction table
    2) B. ITEMIZED SECTIONS (text sections) for 7, 9, 15, 16, 23.
    3) Separate subsection for Category 23 items flagged as 'Needs Further Investigation'.
    Not intended for automated import; this is for documentation/records.
    """
    output = io.StringIO()

    df.to_csv(output, index=False)

    output.write("\n\nB. ITEMIZED SECTIONS (MUST APPEAR BELOW THE TABLE)\n")

    # Helper to write consolidated itemization section
    def write_consolidated_section(title: str, cat_prefix: str):
        sub = df[df["IRS Category"].str.startswith(cat_prefix)]
        if sub.empty:
            return
        output.write(f"\n{title}\n")
        tmp = sub.copy()
        tmp["Itemization Label"] = tmp["Itemization Label"].replace("", "UNLABELED").fillna("UNLABELED")
        grouped = (
            tmp.groupby("Itemization Label")["Amount"]
            .sum()
            .reset_index()
            .sort_values("Itemization Label")
        )
        output.write("Itemization Label,Total Amount\n")
        for _, r in grouped.iterrows():
            output.write(f"{r['Itemization Label']},{r['Amount']}\n")

    # (7) OTHER REVENUE – CONSOLIDATED ITEMIZATION
    write_consolidated_section(
        "(7) OTHER REVENUE – CONSOLIDATED ITEMIZATION (one summarized line per type)",
        "7 "
    )

    # (9) GROSS RECEIPTS FROM EXEMPT PURPOSE – CONSOLIDATED ITEMIZATION
    write_consolidated_section(
        "(9) GROSS RECEIPTS FROM EXEMPT PURPOSE – CONSOLIDATED ITEMIZATION (one summarized line per program service revenue source)",
        "9 "
    )

    # (15) CONTRIBUTIONS/GIFTS/GRANTS PAID OUT – CONSOLIDATED ITEMIZATION
    write_consolidated_section(
        "(15) CONTRIBUTIONS/GIFTS/GRANTS PAID OUT – CONSOLIDATED ITEMIZATION (one summarized line per vendor/type)",
        "15 "
    )

    # (16) DISBURSEMENTS TO/FOR MEMBERS – INDIVIDUAL EVENTS
    cat16 = df[df["IRS Category"].str.startswith("16 ")]
    if not cat16.empty:
        output.write("\n(16) DISBURSEMENTS TO/FOR MEMBERS – INDIVIDUAL EVENTS (each event must list Date, Location, Purpose, Amount)\n")
        output.write("Date,Member/Event Label,Event Location,Event Purpose,Amount\n")
        for _, r in cat16.iterrows():
            date_val = r["Date"] if pd.notna(r.get("Date", "")) else ""
            output.write(
                f"{date_val},"
                f"{r.get('Member/Event Label','')},"
                f"{r.get('Event Location','')},"
                f"{r.get('Event Purpose','')},"
                f"{r['Amount']}\n"
            )

    # (23) OTHER EXPENSES NOT CLASSIFIED ABOVE – CONSOLIDATED ITEMIZATION
    write_consolidated_section(
        "(23) OTHER EXPENSES NOT CLASSIFIED ABOVE – CONSOLIDATED ITEMIZATION (one summarized line per type)",
        "23 "
    )

    # (23-FI) CATEGORY 23 ITEMS MARKED "NEEDS FURTHER INVESTIGATION"
    flag23 = df[
        (df["IRS Category"].str.startswith("23 ")) &
        (df["Needs Further Investigation"] == True)
    ]
    if not flag23.empty:
        output.write(
            "\n(23-FI) CATEGORY 23 ITEMS MARKED 'NEEDS FURTHER INVESTIGATION' "
            "(internal review – not a separate IRS category)\n"
        )
        output.write("Date,Description,Amount,Itemization Label\n")
        for _, r in flag23.iterrows():
            date_val = r["Date"] if pd.notna(r.get("Date", "")) else ""
            output.write(
                f"{date_val},"
                f"{str(r.get('Description','')).replace(',', ' ')},"
                f"{r['Amount']},"
                f"{str(r.get('Itemization Label','')).replace(',', ' ')}\n"
            )

    return output.getvalue().encode("utf-8")


def to_csv_bytes_simple(dataframe: pd.DataFrame) -> bytes:
    return dataframe.to_csv(index=False).encode("utf-8")


monthly_report_csv = build_monthly_activity_csv(df)
itemized_export_csv = build_full_export_with_itemization(df)
summary_csv = to_csv_bytes_simple(summary)

st.download_button(
    label="Download FAOA Monthly Financial Activity Report (machine-readable CSV)",
    data=monthly_report_csv,
    file_name=f"FAOA_Monthly_Financial_Activity_Report_{int(year)}_{month_number:02d}.csv",
    mime="text/csv",
)

st.download_button(
    label="Download categorized transactions CSV with itemized sections (human/IRS-style)",
    data=itemized_export_csv,
    file_name=f"FAOA_Monthly_Itemized_Sections_{int(year)}_{month_number:02d}.csv",
    mime="text/csv",
)

st.download_button(
    label="Download IRS category totals CSV",
    data=summary_csv,
    file_name=f"FAOA_IRS_Category_Totals_{int(year)}_{month_number:02d}.csv",
    mime="text/csv",
)

st.success("Processing complete. Use the Manual Reconciliation section to resolve any remaining items, then download the monthly report and supporting CSVs.")

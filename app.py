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

REVENUE_CODES = {"1", "2", "3", "4", "6", "7", "9"}
EXPENSE_CODES = {"14", "15", "16", "18", "19", "22", "23"}

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
        if abs(amount) < 9:  # < $9 → Category 9 (journal-type exempt receipts)
            return CATEGORY_LABELS["9"], False, False  # we'll also auto-label later
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

# -----------------------------
# STRIPE < $9 → JOURNAL SUBSCRIPTIONS (AUTO)
# -----------------------------
amount_series = pd.to_numeric(df["Amount"], errors="coerce").fillna(0.0)
journal_mask = (
    df["Description"].str.lower().str.contains("stripe transfer", na=False)
    & (amount_series > 0)
    & (amount_series.abs() < 9)
)

# Ensure they are Category 9 (journal-type exempt receipts)
df.loc[journal_mask, "IRS Category"] = CATEGORY_LABELS["9"]

# Auto-set itemization label if not already set
df.loc[journal_mask & (df["Itemization Label"] == ""), "Itemization Label"] = "Journal subscriptions"

# -----------------------------
# CONTINUE PIPELINE
# -----------------------------
st.subheader("Categorized transactions (initial pass)")
st.dataframe(df.head(50))

# Force review for categories that require itemization:
# 7 (Other revenue), 9 (Gross receipts from exempt purpose),
# 15 (Contributions paid out), 16 (Disbursements to/for members), 23 (Other expenses)
df["Needs Review"] = df["Needs Review"] | df["IRS Category"].str.startswith(
    ("7 ", "9 ", "15 ", "16 ", "23 ")
)

# But Stripe 'journal subscription' transfers under $9 are handled automatically
df.loc[journal_mask, "Needs Review"] = False

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
# EXPORTS – MACHINE CSV + TEXT REPORT
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


def build_text_report(
    df: pd.DataFrame,
    summary: pd.DataFrame,
    month_name: str,
    year_val: int,
) -> bytes:
    """
    Human-readable monthly report as plain text:

    “[Month/Year] Foreign Area Officer Association Financial Report”

    Revenue Categories
    ...
    Expense Categories
    ...
    Itemized Revenue (by IRS category)
    ...
    Itemized Expenses (by IRS category)
    ...
    Needs Further Investigation total
    """
    out = io.StringIO()

    out.write(f"{month_name} {year_val} Foreign Area Officer Association Financial Report\n")
    out.write("Foreign Area Officer Association (FAOA)\n")
    out.write("-" * 72 + "\n\n")

    # --- Split summary into revenue vs expense ---
    def split_code_label(cat: str):
        parts = cat.split(" ", 1)
        code = parts[0]
        label = parts[1].lstrip("- ") if len(parts) > 1 else ""
        return code, label

    summary_rows = []
    for _, row in summary.iterrows():
        code, label = split_code_label(row["IRS Category"])
        amt = float(row["Amount"])
        summary_rows.append((code, label, amt))

    # Revenue categories
    out.write("REVENUE CATEGORIES\n")
    has_rev = False
    for code, label, amt in summary_rows:
        if code in REVENUE_CODES and abs(amt) > 0.0001:
            has_rev = True
            out.write(f"  {code} - {label}: {amt:,.2f}\n")
    if not has_rev:
        out.write("  (No revenue recorded for this period.)\n")
    out.write("\n")

    # Expense categories
    out.write("EXPENSE CATEGORIES\n")
    has_exp = False
    for code, label, amt in summary_rows:
        if code in EXPENSE_CODES and abs(amt) > 0.0001:
            has_exp = True
            out.write(f"  {code} - {label}: {amt:,.2f}\n")
    if not has_exp:
        out.write("  (No expenses recorded for this period.)\n")
    out.write("\n")

    # local numeric series
    amount_series_local = pd.to_numeric(df["Amount"], errors="coerce").fillna(0.0)

    # ------------------------
    # ITEMIZED REVENUE (BY IRS CATEGORY)
    # ------------------------
    out.write("ITEMIZED REVENUE\n")

    any_itemized_rev = False
    for code, label, _ in summary_rows:
        if code not in REVENUE_CODES:
            continue

        cat_df = df[df["IRS Category"].str.startswith(f"{code} ")]
        if cat_df.empty:
            continue

        # Only bother if there is some sort of breakdown info
        has_item_labels = (cat_df["Itemization Label"] != "").any()
        has_sponsors = (cat_df["Sponsor Name"] != "").any()

        if not (has_item_labels or has_sponsors):
            # No granular breakdown; skip detailed listing
            continue

        any_itemized_rev = True
        out.write(f"  Category {code} – {label}:\n")

        if code == "1":
            # Sponsorship / donor detail by Sponsor Name
            sponsor_items = cat_df[cat_df["Sponsor Name"] != ""]
            if sponsor_items.empty:
                out.write("    (No sponsor names recorded.)\n")
            else:
                grouped_s = (
                    sponsor_items.groupby("Sponsor Name")["Amount"]
                    .sum()
                    .reset_index()
                    .sort_values("Sponsor Name")
                )
                for _, r in grouped_s.iterrows():
                    out.write(f"    {r['Sponsor Name']}: {float(r['Amount']):,.2f}\n")
        else:
            tmp = cat_df.copy()
            tmp["Itemization Label"] = tmp["Itemization Label"].replace(
                "", "UNLABELED"
            ).fillna("UNLABELED")
            grouped = (
                tmp.groupby("Itemization Label")["Amount"]
                .sum()
                .reset_index()
                .sort_values("Itemization Label")
            )
            for _, r in grouped.iterrows():
                out.write(f"    {r['Itemization Label']}: {float(r['Amount']):,.2f}\n")

        out.write("\n")

    if not any_itemized_rev:
        out.write("  (No itemized revenue entries.)\n\n")

    # ------------------------
    # ITEMIZED EXPENSES (BY IRS CATEGORY)
    # ------------------------
    out.write("ITEMIZED EXPENSES\n")

    any_itemized_exp = False
    for code, label, _ in summary_rows:
        if code not in EXPENSE_CODES:
            continue

        cat_df = df[df["IRS Category"].str.startswith(f"{code} ")]
        if cat_df.empty:
            continue

        # Category 16 – list individual events
        if code == "16":
            if cat_df.empty:
                continue
            any_itemized_exp = True
            out.write(f"  Category 16 – {label} (individual events):\n")
            out.write("    Date | Event | Location | Purpose | Amount\n")
            for _, r in cat_df.iterrows():
                date_val = str(r.get("Date", "") or "")
                evt = str(r.get("Member/Event Label", "") or "")
                loc = str(r.get("Event Location", "") or "")
                purp = str(r.get("Event Purpose", "") or "")
                amt = float(r["Amount"])
                out.write(
                    f"    {date_val} | {evt} | {loc} | {purp} | {amt:,.2f}\n"
                )
            out.write("\n")
        else:
            # Other expense categories – consolidate by Itemization Label when present
            if not (cat_df["Itemization Label"] != "").any():
                # If no labels at all, skip detailed listing for this category
                continue

            any_itemized_exp = True
            out.write(f"  Category {code} – {label} (consolidated by type):\n")
            tmp = cat_df.copy()
            tmp["Itemization Label"] = tmp["Itemization Label"].replace(
                "", "UNLABELED"
            ).fillna("UNLABELED")
            grouped_e = (
                tmp.groupby("Itemization Label")["Amount"]
                .sum()
                .reset_index()
                .sort_values("Itemization Label")
            )
            for _, r in grouped_e.iterrows():
                out.write(f"    {r['Itemization Label']}: {float(r['Amount']):,.2f}\n")
            out.write("\n")

    if not any_itemized_exp:
        out.write("  (No itemized expense entries.)\n\n")

    # ------------------------
    # NEEDS FURTHER INVESTIGATION
    # ------------------------
    nfi_mask = df["Needs Further Investigation"] == True
    nfi_total = df.loc[nfi_mask, "Amount"].sum()
    nfi_count = int(nfi_mask.sum())

    out.write("NEEDS FURTHER INVESTIGATION (Treasurer Flagged)\n")
    if nfi_count == 0:
        out.write("  (None flagged this period.)\n")
    else:
        out.write(f"  Count of flagged transactions: {nfi_count}\n")
        out.write(f"  Net total of flagged amounts: {nfi_total:,.2f}\n")

    out.write("\n")
    out.write("End of report.\n")

    return out.getvalue().encode("utf-8")


# Build exports
monthly_report_csv = build_monthly_activity_csv(df)
text_report = build_text_report(df, summary, month_name, int(year))

# Download buttons
st.download_button(
    label="Download FAOA Monthly Financial Activity Report (machine-readable CSV)",
    data=monthly_report_csv,
    file_name=f"FAOA_Monthly_Financial_Activity_Report_{int(year)}_{month_number:02d}.csv",
    mime="text/csv",
)

st.download_button(
    label="Download formatted monthly financial report (text)",
    data=text_report,
    file_name=f"FAOA_Financial_Report_{int(year)}_{month_number:02d}.txt",
    mime="text/plain",
)

st.success("Processing complete. Use the Manual Reconciliation section to resolve any remaining items, then download the monthly report and supporting files.")

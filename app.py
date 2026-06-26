import streamlit as st
import pandas as pd
import re
from rapidfuzz import fuzz
from pypinyin import pinyin, Style

# --- PASSWORD GATE ---
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct"):
        return True
    st.text_input("Enter password", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state:
        st.error("Incorrect password")
    return False

if not check_password():
    st.stop()

# --- CORE LOGIC FUNCTIONS ---

def fix_postal(postal):
    """Step 1: Ensure 6-digits by adding leading zero if missing."""
    p = str(postal).strip().replace(".0", "")
    p = re.sub(r'\D', '', p)
    if len(p) == 5:
        return "0" + p
    return p

def deep_normalize(text):
    """Step 4: Strip brackets, dashes, and convert Chinese to Pinyin."""
    if not text or pd.isna(text):
        return ""
    t = str(text).lower()
    t = re.sub(r'\(.*?\)', '', t)
    t = t.split('-')[0].strip()
    py_list = pinyin(t, style=Style.NORMAL, errors='default')
    t_pinyin = " ".join([i[0] for i in py_list])
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', t_pinyin)
    return cleaned.strip()

# --- CACHED DATA LOADERS ---

@st.cache_data(show_spinner=False)
def load_file(uploaded_file):
    if uploaded_file.name.endswith(".xlsx"):
        return pd.read_excel(uploaded_file)
    return pd.read_csv(uploaded_file)

@st.cache_data(show_spinner="Indexing Salesforce records — this only runs once...")
def preprocess_sf(df_sf_raw, sf_name_col, sf_post_col):
    df = df_sf_raw.copy()
    df['name_norm']    = df[sf_name_col].apply(deep_normalize)
    df['postal_fixed'] = df[sf_post_col].apply(fix_postal)
    return df

# --- SIDEBAR HELP PANEL ---

with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/1/1b/Flag_of_Singapore.svg/320px-Flag_of_Singapore.svg.png", width=40)
    st.title("How to use this app")

    st.markdown("---")

    with st.expander("📁 Step 1 — Upload your files", expanded=False):
        st.markdown("""
**Salesforce Master** (your source of truth)
Must contain:
- Outlet / account name
- Postal code
- Grid ID
- Account status

**New Lead List** (what you want to check)
Must contain:
- Outlet name
- Street address
- Postal code

Accepted formats: `.csv` or `.xlsx`
        """)

    with st.expander("🗂 Step 2 — Map your columns", expanded=False):
        st.markdown("""
After uploading, you'll see two column mapping panels.

Match each dropdown to the correct column in your file.
Column names don't need to match exactly — you're telling
the app which column holds which data.

**Tip:** If your postal code column stores numbers,
the app will auto-fix 5-digit codes by adding a leading zero.
        """)

    with st.expander("🎯 Priority tiers explained", expanded=True):
        st.markdown("""
After matching, every lead gets a priority:

| Tier | Match Score | What to do |
|------|-------------|------------|
| **P1** — New | No match | ✅ Action this lead |
| **P2** — Potential | 50–69% | 🔍 Review manually |
| **P3** — Duplicate | 70–99% | ⚠️ Likely in SF already |
| **P4** — Duplicate | 100% | ❌ Skip — exact match |
| **N/A** — Invalid | — | 🗑 Bad address or name |
        """)

    with st.expander("❓ FAQs", expanded=False):
        st.markdown("""
**Why is my lead showing as Invalid?**
Either the postal code is missing and the address only says
"Singapore", or the outlet name contains a blacklisted word
(e.g. hotel, bar, food court).

**Why is a real restaurant showing as Duplicate?**
The name and postal code closely match an existing SF record.
Check the SF Match Name column — it will show you what it
matched against.

**The match score looks wrong. What now?**
Download the results and filter for P2 leads. Review the
SF Match Name column to decide manually.

**How do I update the app?**
Contact your Sales Ops team lead.
        """)

    st.markdown("---")
    st.caption("SF Deduplicator · Sales Ops · Internal use only")

# --- MAIN UI ---

st.set_page_config(page_title="SF Deduplicator Waterfall", layout="wide")
st.title("🇸🇬 Sales Ops: Priority Waterfall Deduplicator")
st.markdown("Upload your files, map the columns, and run the waterfall to classify your leads.")

col1, col2 = st.columns(2)
with col1:
    sf_file   = st.file_uploader("1. Upload Salesforce Master",  type=['csv', 'xlsx'])
with col2:
    lead_file = st.file_uploader("2. Upload New Lead List",      type=['csv', 'xlsx'])

if sf_file and lead_file:
    with st.spinner("Loading files..."):
        df_sf_raw = load_file(sf_file)
        df_leads  = load_file(lead_file)

    st.divider()
    st.subheader("Column Mapping")
    c1, c2 = st.columns(2)

    with c1:
        st.info("Salesforce Master")
        sf_name_col   = st.selectbox("Name (SF)",        df_sf_raw.columns)
        sf_post_col   = st.selectbox("Postal Code (SF)", df_sf_raw.columns)
        sf_grid_col   = st.selectbox("Grid ID",          df_sf_raw.columns)
        sf_status_col = st.selectbox("Account Status",   df_sf_raw.columns)

    with c2:
        st.info("New Leads")
        ld_name_col = st.selectbox("Name (Leads)",           df_leads.columns)
        ld_addr_col = st.selectbox("Address/Street (Leads)", df_leads.columns)
        ld_post_col = st.selectbox("Postal Code (Leads)",    df_leads.columns)

    if st.button("🚀 Run Priority Waterfall", type="primary"):

        df_sf = preprocess_sf(df_sf_raw, sf_name_col, sf_post_col)

        invalid_keywords = [
            "bar", "pub", "club", "hotel", "boutique", "capsule",
            "food court", "food centre", "foodcenter", "eating house",
            "cantine", "brewery", "liquor", "wine",
            "酒吧", "酒店", "美食广场", "美食中心"
        ]

        results      = []
        progress_bar = st.progress(0)
        total        = len(df_leads)

        for idx, lead in df_leads.iterrows():
            l_name   = str(lead[ld_name_col])
            l_addr   = str(lead[ld_addr_col]).strip()
            l_postal = fix_postal(lead[ld_post_col])

            final_status   = "NEW"
            priority_level = "P1"
            best_score     = 0
            match_data     = {"grid": "", "name": "", "status": ""}

            is_postal_missing = l_postal in ["", "nan", "0", "000000"]
            if is_postal_missing and l_addr.lower() == "singapore":
                final_status   = "Invalid - No address"
                priority_level = "N/A"

            elif any(word in l_name.lower() for word in invalid_keywords):
                final_status   = "Invalid - Not restaurant"
                priority_level = "N/A"

            else:
                l_name_norm = deep_normalize(l_name)
                candidates  = df_sf[df_sf['postal_fixed'] == l_postal]

                for _, row in candidates.iterrows():
                    score = fuzz.token_sort_ratio(l_name_norm, row['name_norm'])
                    if score > best_score:
                        best_score = score
                        match_data = {
                            "grid":   row[sf_grid_col],
                            "name":   row[sf_name_col],
                            "status": row[sf_status_col],
                        }

                if best_score == 100:
                    final_status, priority_level = "DUPLICATE", "P4"
                elif best_score >= 70:
                    final_status, priority_level = "DUPLICATE", "P3"
                elif best_score >= 50:
                    final_status, priority_level = "POTENTIAL", "P2"
                else:
                    final_status, priority_level = "NEW", "P1"

            res_row = lead.to_dict()
            res_row.update({
                "MATCH_STATUS":      final_status,
                "PRIORITY":          priority_level,
                "MATCH_SCORE":       f"{int(best_score)}%",
                "SF_GRID_ID":        match_data["grid"]   if best_score >= 50 else "",
                "SF_MATCH_NAME":     match_data["name"]   if best_score >= 50 else "",
                "SF_ACCOUNT_STATUS": match_data["status"] if best_score >= 50 else "",
            })
            results.append(res_row)
            progress_bar.progress((idx + 1) / total)

        final_df = pd.DataFrame(results)

        st.success("✅ Waterfall Matching Complete!")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Leads",          len(final_df))
        m2.metric("New (P1)",             len(final_df[final_df["PRIORITY"] == "P1"]))
        m3.metric("Potential (P2)",        len(final_df[final_df["PRIORITY"] == "P2"]))
        m4.metric("Duplicate (P3/P4)",    len(final_df[final_df["PRIORITY"].isin(["P3","P4"])]))

        st.dataframe(final_df, use_container_width=True)
        st.download_button(
            "📥 Download Final Lead Sheet",
            final_df.to_csv(index=False),
            "waterfall_results.csv",
            mime="text/csv",
        )

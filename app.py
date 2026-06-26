import streamlit as st
import pandas as pd
import re
from rapidfuzz import fuzz
from pypinyin import pinyin, Style

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
    t = re.sub(r'\(.*?\)', '', t)   # Strip brackets
    t = t.split('-')[0].strip()     # Strip after dashes

    # Convert Chinese characters to Pinyin
    py_list = pinyin(t, style=Style.NORMAL, errors='default')
    t_pinyin = " ".join([i[0] for i in py_list])

    # Keep only alphanumeric and spaces for Token Sorting
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', t_pinyin)
    return cleaned.strip()

# --- CACHED DATA LOADERS ---

@st.cache_data(show_spinner=False)
def load_file(uploaded_file):
    """Load CSV or Excel into a DataFrame, cached so re-uploads don't re-read."""
    if uploaded_file.name.endswith(".xlsx"):
        return pd.read_excel(uploaded_file)
    return pd.read_csv(uploaded_file)

@st.cache_data(show_spinner="Indexing Salesforce records — this only runs once...")
def preprocess_sf(df_sf_raw, sf_name_col, sf_post_col):
    """
    Normalize names and fix postal codes for the entire SF master.
    Cached by file content + column selection — won't re-run unless inputs change.
    """
    df = df_sf_raw.copy()
    df['name_norm']    = df[sf_name_col].apply(deep_normalize)
    df['postal_fixed'] = df[sf_post_col].apply(fix_postal)
    return df

# --- STREAMLIT UI SETUP ---

st.set_page_config(page_title="SF Deduplicator Waterfall", layout="wide")
st.title("🇸🇬 Sales Ops: Priority Waterfall Deduplicator")
st.markdown("Processing leads using your custom rule hierarchy and tiering.")

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

        # --- PRE-PROCESS SF MASTER (cached) ---
        df_sf = preprocess_sf(df_sf_raw, sf_name_col, sf_post_col)

        # Step 3 blacklist keywords
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

            # --- WATERFALL CHECKS ---

            # Step 2: Address validation
            is_postal_missing = l_postal in ["", "nan", "0", "000000"]
            if is_postal_missing and l_addr.lower() == "singapore":
                final_status   = "Invalid - No address"
                priority_level = "N/A"

            # Step 3: Name blacklist
            elif any(word in l_name.lower() for word in invalid_keywords):
                final_status   = "Invalid - Not restaurant"
                priority_level = "N/A"

            # Steps 4 & 5: Deep clean + fuzzy token match
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

                # Assign status & priority from confidence score
                if best_score == 100:
                    final_status, priority_level = "DUPLICATE", "P4"
                elif best_score >= 70:
                    final_status, priority_level = "DUPLICATE", "P3"
                elif best_score >= 50:
                    final_status, priority_level = "POTENTIAL", "P2"
                else:
                    final_status, priority_level = "NEW", "P1"

            # Merge matched SF metrics back onto the lead row
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

        # --- SUMMARY METRICS ---
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Leads",   len(final_df))
        m2.metric("New (P1)",      len(final_df[final_df["PRIORITY"] == "P1"]))
        m3.metric("Potential (P2)", len(final_df[final_df["PRIORITY"] == "P2"]))
        m4.metric("Duplicate (P3/P4)", len(final_df[final_df["PRIORITY"].isin(["P3","P4"])]))

        st.dataframe(final_df, use_container_width=True)
        st.download_button(
            "📥 Download Final Lead Sheet",
            final_df.to_csv(index=False),
            "waterfall_results.csv",
            mime="text/csv",
        )

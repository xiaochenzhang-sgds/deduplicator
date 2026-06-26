import streamlit as st
import pandas as pd
import re
from rapidfuzz import fuzz, process
from pypinyin import pinyin, Style

# set_page_config MUST be the first Streamlit call
st.set_page_config(page_title="SF Deduplicator Waterfall", layout="wide")

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

    st.title("🇸🇬 SF Deduplicator")
    st.text_input("Enter password", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state:
        st.error("Incorrect password")
    return False

if not check_password():
    st.stop()

# --- CORE LOGIC ---

def fix_postal(postal):
    p = str(postal).strip().replace(".0", "")
    p = re.sub(r'\D', '', p)
    if len(p) == 5:
        return "0" + p
    return p

def deep_normalize(text):
    if not text or pd.isna(text):
        return ""
    t = str(text).lower()
    t = re.sub(r'\(.*?\)', '', t)
    t = t.split('-')[0].strip()
    py_list = pinyin(t, style=Style.NORMAL, errors='default')
    t_pinyin = " ".join([i[0] for i in py_list])
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', t_pinyin)
    return cleaned.strip()

# --- CACHED LOADERS ---

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

# --- SIDEBAR ---

with st.sidebar:
    st.title("⚙️ Settings & Help")
    st.markdown("---")

    # Match threshold sliders
    with st.expander("🎚 Match Thresholds", expanded=True):
        st.caption("Adjust the sensitivity of each priority tier.")
        p2_threshold = st.slider(
            "P2 (Potential) starts at",
            min_value=30, max_value=65, value=50, step=5,
            help="Leads with a match score at or above this are marked Potential (P2)"
        )
        p3_threshold = st.slider(
            "P3 (Duplicate) starts at",
            min_value=p2_threshold + 5, max_value=95, value=70, step=5,
            help="Leads with a match score at or above this are marked Duplicate (P3)"
        )
        st.caption(
            f"P1 < {p2_threshold}% · "
            f"P2 {p2_threshold}–{p3_threshold - 1}% · "
            f"P3 {p3_threshold}–99% · "
            f"P4 = 100%"
        )

    # Blacklist editor
    with st.expander("🚫 Blacklist Keywords", expanded=False):
        st.caption("Leads whose names contain these words are marked Invalid. One keyword per line.")
        default_keywords = "\n".join([
            "bar", "pub", "club", "hotel", "boutique", "capsule",
            "food court", "food centre", "foodcenter", "eating house",
            "cantine", "brewery", "liquor", "wine",
            "酒吧", "酒店", "美食广场", "美食中心"
        ])
        blacklist_input = st.text_area("Keywords", value=default_keywords, height=220)
        invalid_keywords = [k.strip().lower() for k in blacklist_input.split("\n") if k.strip()]
        st.caption(f"{len(invalid_keywords)} keywords active")

    st.markdown("---")

    with st.expander("📁 Preparing your files", expanded=False):
        st.markdown("""
**Salesforce Master** must have:
- Outlet / account name
- Postal code
- Grid ID
- Account status

**New Lead List** must have:
- Outlet name
- Street address
- Postal code

Accepted formats: `.csv` or `.xlsx`
        """)

    with st.expander("🎯 Priority tiers", expanded=False):
        st.markdown("""
| Tier | Score | Action |
|------|-------|--------|
| **P1** New | No match | ✅ Action this lead |
| **P2** Potential | Adjustable | 🔍 Review manually |
| **P3** Duplicate | Adjustable | ⚠️ Likely in SF |
| **P4** Duplicate | 100% | ❌ Skip — exact match |
| **N/A** Invalid | — | 🗑 Skip entirely |

Use the threshold sliders above to tune P2 and P3 sensitivity.
        """)

    with st.expander("❓ FAQs", expanded=False):
        st.markdown("""
**Why is my lead showing as Invalid?**
Either the postal code is missing and the address only says
"Singapore", or the name contains a blacklisted keyword.

**Why does a real restaurant show as Duplicate?**
Check the SF Match Name column — it shows what it matched
against. Raise the P3 threshold if needed.

**The match score looks wrong. What now?**
Download the results and review P2 leads manually using
the SF Match Name column.

**Can I change the keywords?**
Yes — use the Blacklist Keywords panel above.
        """)

    st.markdown("---")
    st.caption("SF Deduplicator · Sales Ops · Internal use only")

# --- MAIN UI ---

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

    # Column mapping validation
    sf_selections = [sf_name_col, sf_post_col, sf_grid_col, sf_status_col]
    ld_selections = [ld_name_col, ld_addr_col, ld_post_col]
    sf_dupes = len(sf_selections) != len(set(sf_selections))
    ld_dupes = len(ld_selections) != len(set(ld_selections))

    if sf_dupes:
        st.warning("⚠️ You've mapped the same Salesforce column twice. Please check your selections.")
    if ld_dupes:
        st.warning("⚠️ You've mapped the same Lead column twice. Please check your selections.")

    if st.button("🚀 Run Priority Waterfall", type="primary", disabled=(sf_dupes or ld_dupes)):

        df_sf = preprocess_sf(df_sf_raw, sf_name_col, sf_post_col)

        # Pre-group SF by postal code for faster lookups
        sf_by_postal = {k: v.reset_index(drop=True) for k, v in df_sf.groupby('postal_fixed')}

        results      = []
        progress_bar = st.progress(0)
        status_text  = st.empty()
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
                candidates  = sf_by_postal.get(l_postal)

                if candidates is not None and not candidates.empty:
                    # Vectorised: extractOne is faster than a manual loop
                    result = process.extractOne(
                        l_name_norm,
                        candidates['name_norm'].tolist(),
                        scorer=fuzz.token_sort_ratio
                    )
                    if result:
                        best_score    = result[1]
                        best_row      = candidates.iloc[result[2]]
                        match_data    = {
                            "grid":   best_row[sf_grid_col],
                            "name":   best_row[sf_name_col],
                            "status": best_row[sf_status_col],
                        }

                if best_score == 100:
                    final_status, priority_level = "DUPLICATE", "P4"
                elif best_score >= p3_threshold:
                    final_status, priority_level = "DUPLICATE", "P3"
                elif best_score >= p2_threshold:
                    final_status, priority_level = "POTENTIAL", "P2"
                else:
                    final_status, priority_level = "NEW", "P1"

            res_row = lead.to_dict()
            res_row.update({
                "MATCH_STATUS":      final_status,
                "PRIORITY":          priority_level,
                "MATCH_SCORE":       f"{int(best_score)}%",
                "SF_GRID_ID":        match_data["grid"]   if best_score >= p2_threshold else "",
                "SF_MATCH_NAME":     match_data["name"]   if best_score >= p2_threshold else "",
                "SF_ACCOUNT_STATUS": match_data["status"] if best_score >= p2_threshold else "",
            })
            results.append(res_row)

            progress_bar.progress((idx + 1) / total)
            status_text.caption(f"Processing {idx + 1:,} of {total:,} leads...")

        status_text.empty()
        final_df = pd.DataFrame(results)

        st.success("✅ Waterfall Matching Complete!")

        # --- SUMMARY METRICS ---
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Total Leads",           len(final_df))
        m2.metric("🟢 New (P1)",           len(final_df[final_df["PRIORITY"] == "P1"]))
        m3.metric("🟡 Potential (P2)",     len(final_df[final_df["PRIORITY"] == "P2"]))
        m4.metric("🔴 Duplicate (P3/P4)",  len(final_df[final_df["PRIORITY"].isin(["P3", "P4"])]))
        m5.metric("⚪ Invalid (N/A)",      len(final_df[final_df["PRIORITY"] == "N/A"]))

        # --- SUMMARY CHART ---
        st.subheader("Priority Breakdown")
        priority_order = ["P1", "P2", "P3", "P4", "N/A"]
        chart_data = (
            final_df["PRIORITY"]
            .value_counts()
            .reindex(priority_order, fill_value=0)
            .rename("Count")
            .reset_index()
            .rename(columns={"index": "Priority"})
        )
        st.bar_chart(chart_data.set_index("Priority"))

        # --- COLOUR-CODED TABBED RESULTS ---
        def style_priority(row):
            colour_map = {
                "P1":  "background-color: #d4edda",
                "P2":  "background-color: #fff3cd",
                "P3":  "background-color: #f8d7da",
                "P4":  "background-color: #f8d7da",
                "N/A": "background-color: #e2e3e5",
            }
            return [colour_map.get(row["PRIORITY"], "")] * len(row)

        def show_table(df):
            if df.empty:
                st.info("No leads in this category.")
            else:
                st.dataframe(
                    df.style.apply(style_priority, axis=1),
                    use_container_width=True
                )

        n_p1  = len(final_df[final_df["PRIORITY"] == "P1"])
        n_p2  = len(final_df[final_df["PRIORITY"] == "P2"])
        n_dup = len(final_df[final_df["PRIORITY"].isin(["P3", "P4"])])
        n_inv = len(final_df[final_df["PRIORITY"] == "N/A"])

        st.subheader("Results")
        tab_all, tab_p1, tab_p2, tab_dup, tab_inv = st.tabs([
            f"All ({len(final_df)})",
            f"🟢 New P1 ({n_p1})",
            f"🟡 Potential P2 ({n_p2})",
            f"🔴 Duplicates ({n_dup})",
            f"⚪ Invalid ({n_inv})",
        ])

        with tab_all:
            show_table(final_df)
        with tab_p1:
            show_table(final_df[final_df["PRIORITY"] == "P1"])
        with tab_p2:
            show_table(final_df[final_df["PRIORITY"] == "P2"])
        with tab_dup:
            show_table(final_df[final_df["PRIORITY"].isin(["P3", "P4"])])
        with tab_inv:
            show_table(final_df[final_df["PRIORITY"] == "N/A"])

        # --- DOWNLOAD ---
        st.download_button(
            "📥 Download Full Results",
            final_df.to_csv(index=False),
            "waterfall_results.csv",
            mime="text/csv",
        )

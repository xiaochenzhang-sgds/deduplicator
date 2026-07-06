import streamlit as st
import pandas as pd
import re
from itertools import combinations
from rapidfuzz import fuzz, process
from pypinyin import pinyin, Style

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

# --- STATUS GROUPINGS ---

ACTIVE_PIPELINE = [
    "active", "new", "collecting documents", "negotiation",
    "menu processing", "onboarding", "quality check"
]
WIN_BACK        = ["lost", "terminated"]
WIN_BACK_FAILED = ["win back failed"]

def get_status_group(status, is_closed_name):
    if is_closed_name:
        return "win_back"
    s = str(status).strip().lower()
    if s in WIN_BACK_FAILED: return "win_back_failed"
    if s in WIN_BACK:        return "win_back"
    return "active_pipeline"

def get_risk_and_action(status_a, status_b):
    """Assign risk level and recommended action based on the status combination of a duplicate pair."""
    def grp(s):
        sl = str(s).strip().lower()
        if sl in WIN_BACK_FAILED: return "wbf"
        if sl in WIN_BACK:        return "winback"
        return "pipeline"

    ga, gb = grp(status_a), grp(status_b)
    pair   = frozenset([ga, gb])

    if pair == frozenset(["pipeline"]):
        sa, sb = str(status_a).strip().lower(), str(status_b).strip().lower()
        if sa == "active" and sb == "active":
            return "🔴 High",   "Merge required — two live active accounts"
        return     "🔴 High",   "Being worked twice — check with rep"
    if pair == frozenset(["pipeline", "winback"]):
        return     "🟡 Medium", "Review — possibly stale record from prior attempt"
    if pair == frozenset(["winback"]):
        return     "🟢 Low",    "Low priority — both inactive"
    if "wbf" in pair:
        return     "🟢 Low",    "Informational — win-back already attempted"
    return         "🟡 Medium", "Review recommended"

# --- CORE FUNCTIONS ---

def fix_postal(postal):
    p = str(postal).strip().replace(".0", "")
    p = re.sub(r'\D', '', p)
    if len(p) == 5: return "0" + p
    return p

def extract_unit(address):
    """
    Extract and normalise unit number from address string.
    Handles: #03-12  #3-12  03-12  3-12  # 03-12  #B1-05
    All normalised to e.g. '3-12', 'b1-5' (leading zeros stripped).
    Digits capped at 3 to avoid matching phone numbers.
    """
    if not address or pd.isna(address):
        return ""
    match = re.search(
        r'#?\s*([A-Za-z]?\d{1,3}[A-Za-z]?)\s*-\s*(\d{1,3}[A-Za-z]?)',
        str(address)
    )
    if not match:
        return ""
    def norm(part):
        p = part.lower()
        m = re.match(r'^([a-z]*)(\d+)([a-z]*)$', p)
        if m:
            prefix, num, suffix = m.groups()
            return f"{prefix}{int(num)}{suffix}"
        return p
    return f"{norm(match.group(1))}-{norm(match.group(2))}"

def has_closed_marker(name):
    if not name or pd.isna(name): return False
    return bool(re.search(r'-\s*closed', str(name).strip().lower()))

def deep_normalize(text):
    if not text or pd.isna(text): return ""
    t = str(text).lower()
    t = re.sub(r'\(.*?\)', '', t)
    t = t.split('-')[0].strip()
    py_list  = pinyin(t, style=Style.NORMAL, errors='default')
    t_pinyin = " ".join([i[0] for i in py_list])
    return re.sub(r'[^a-zA-Z0-9\s]', '', t_pinyin).strip()

# --- CACHED LOADERS ---

@st.cache_data(show_spinner=False)
def load_file(uploaded_file):
    if uploaded_file.name.endswith(".xlsx"):
        return pd.read_excel(uploaded_file)
    return pd.read_csv(uploaded_file)

@st.cache_data(show_spinner="Indexing Salesforce records — this only runs once...")
def preprocess_sf(df_raw, name_col, post_col, addr_col, status_col):
    df = df_raw.copy()
    df['is_closed_name'] = df[name_col].apply(has_closed_marker)
    df['name_norm']      = df[name_col].apply(deep_normalize)
    df['postal_fixed']   = df[post_col].apply(fix_postal)
    df['unit_extracted'] = df[addr_col].apply(extract_unit) if addr_col else ""
    df['status_group']   = df.apply(
        lambda r: get_status_group(r[status_col], r['is_closed_name']), axis=1
    )
    return df

# --- INTRA-SF DUPLICATE FINDER ---

def find_sf_duplicates(df, name_col, addr_col, status_col, grid_col, id_col, threshold):
    """
    Find suspected duplicate pairs within the SF master.
    Trigger: same postal code + same extracted unit number + name similarity >= threshold.
    Returns a list of pair dicts.
    """
    pairs = []

    for postal, postal_grp in df.groupby('postal_fixed'):
        # Only look at accounts that have a unit number
        with_unit = postal_grp[postal_grp['unit_extracted'] != ""]
        if with_unit.empty:
            continue

        for unit, unit_grp in with_unit.groupby('unit_extracted'):
            if len(unit_grp) < 2:
                continue

            rows = unit_grp.reset_index(drop=True)
            for i, j in combinations(range(len(rows)), 2):
                a = rows.iloc[i]
                b = rows.iloc[j]

                score = fuzz.token_sort_ratio(a['name_norm'], b['name_norm'])
                if score < threshold:
                    continue

                risk, action = get_risk_and_action(a[status_col], b[status_col])

                pairs.append({
                    "ACCT_A_SF_ID":     a[id_col],
                    "ACCT_A_NAME":      a[name_col],
                    "ACCT_A_STATUS":    a[status_col],
                    "ACCT_A_ADDRESS":   a[addr_col],
                    "ACCT_A_POSTAL":    a['postal_fixed'],
                    "ACCT_A_GRID":      a[grid_col],
                    "ACCT_B_SF_ID":     b[id_col],
                    "ACCT_B_NAME":      b[name_col],
                    "ACCT_B_STATUS":    b[status_col],
                    "ACCT_B_ADDRESS":   b[addr_col],
                    "ACCT_B_POSTAL":    b['postal_fixed'],
                    "ACCT_B_GRID":      b[grid_col],
                    "NAME_SCORE":       f"{score}%",
                    "SHARED_UNIT":      unit,
                    "RISK_LEVEL":       risk,
                    "RECOMMENDED_ACTION": action,
                })

    return pairs

# --- SIDEBAR ---

with st.sidebar:
    st.title("⚙️ Settings & Help")
    st.markdown("---")

    with st.expander("🎚 Match Thresholds", expanded=True):
        st.caption("Lead deduplication thresholds.")
        p2_threshold = st.slider("P2 / WB starts at", 30, 65, 50, 5)
        p3_threshold = st.slider("P3 (Duplicate) starts at", p2_threshold + 5, 95, 70, 5)
        st.caption(
            f"P1 < {p2_threshold}% · "
            f"P2 {p2_threshold}–{p3_threshold - 1}% · "
            f"P3 {p3_threshold}–99% · P4 = 100%"
        )

    with st.expander("🔄 Status groupings", expanded=False):
        st.markdown("""
| Result | SF Statuses |
|--------|-------------|
| **P2/P3/P4** | Active, New, Collecting Docs, Negotiation, Menu Processing, Onboarding, Quality Check |
| **WB** Win-back | Lost, Terminated, any `- Closed` name |
| **WBF** Skip | Win Back Failed |
        """)

    with st.expander("🚫 Blacklist Keywords", expanded=False):
        st.caption("One per line — leads containing these are marked Invalid (only if no SF match is found).")
        default_kw = "\n".join([
            "bar", "pub", "club", "hotel", "boutique", "capsule",
            "food court", "food centre", "foodcenter", "eating house",
            "cantine", "brewery", "liquor", "wine",
            "酒吧", "酒店", "美食广场", "美食中心"
        ])
        blacklist_input  = st.text_area("Keywords", value=default_kw, height=200)
        invalid_keywords = [k.strip().lower() for k in blacklist_input.split("\n") if k.strip()]
        st.caption(f"{len(invalid_keywords)} keywords active")

    with st.expander("🎯 Priority tiers", expanded=False):
        st.markdown("""
| Tier | Meaning | Action |
|------|---------|--------|
| **P1** New | No SF match | ✅ New biz team |
| **P2** Potential | Partial match, active acct | 🔍 Review |
| **P3/P4** Duplicate | Strong match, active acct | ❌ Skip |
| **WB** Win-back | Match vs Lost / Terminated / Closed | 🔄 Win-back team |
| **WBF** Win-back Failed | Match vs Win Back Failed | ❌ Skip |
| **SL** Same Location | Same unit, different name | 🔍 New biz, known address |
| **N/A** Invalid | Bad address, or blacklisted with no SF match | 🗑 Skip |
        """)

    with st.expander("❓ FAQs", expanded=False):
        st.markdown("""
**What is WB (Win-back)?**
Matched a Lost, Terminated, or `- Closed` SF account.
Route to win-back team — different pitch from new biz.

**What is SL (Same Location)?**
Same postal + unit number, different name. Likely a new
business that took over the space.

**How does unit extraction work?**
The tool reads `#XX-YY` patterns from the address field
automatically — no separate column needed. Handles
#03-12, 03-12, 3-12, #3-12 and similar formats.

**Why doesn't "Sushi Bar" get marked invalid anymore?**
The blacklist keyword check now only runs as a last resort,
after SF matching. If a lead matches an existing SF account
(duplicate, potential, win-back, or same-location), that
match always wins over the keyword filter. Only leads with
no SF match at all and a blacklisted word get marked Invalid.

**What is the SF Account Audit?**
A separate tool that finds suspected duplicates within
your Salesforce master itself — not comparing against
new leads, but checking SF against SF.
        """)

    st.markdown("---")
    st.caption("SF Deduplicator · Sales Ops · Internal use only")

# --- MAIN UI ---

st.title("🇸🇬 Sales Ops: SF Data Quality Suite")

main_tab1, main_tab2 = st.tabs(["🔍 Lead Deduplication", "🏢 SF Account Audit"])

# ═══════════════════════════════════════════════════════════════
# TAB 1 — LEAD DEDUPLICATION
# ═══════════════════════════════════════════════════════════════

with main_tab1:
    st.markdown("Upload your files, map the columns, and run the waterfall to classify your leads.")

    col1, col2 = st.columns(2)
    with col1:
        sf_file   = st.file_uploader("1. Upload Salesforce Master",  type=['csv', 'xlsx'], key="sf_lead")
    with col2:
        lead_file = st.file_uploader("2. Upload New Lead List",      type=['csv', 'xlsx'], key="lead_file")

    if sf_file and lead_file:
        with st.spinner("Loading files..."):
            df_sf_raw = load_file(sf_file)
            df_leads  = load_file(lead_file)

        st.divider()
        st.subheader("Column Mapping")
        c1, c2 = st.columns(2)

        with c1:
            st.info("Salesforce Master")
            sf_name_col   = st.selectbox("Name (SF)",        df_sf_raw.columns, key="sf_name")
            sf_post_col   = st.selectbox("Postal Code (SF)", df_sf_raw.columns, key="sf_post")
            sf_addr_col   = st.selectbox("Address (SF)",     df_sf_raw.columns, key="sf_addr")
            sf_grid_col   = st.selectbox("Grid ID",          df_sf_raw.columns, key="sf_grid")
            sf_status_col = st.selectbox("Account Status",   df_sf_raw.columns, key="sf_status")

        with c2:
            st.info("New Leads")
            ld_name_col = st.selectbox("Name (Leads)",           df_leads.columns, key="ld_name")
            ld_addr_col = st.selectbox("Address/Street (Leads)", df_leads.columns, key="ld_addr")
            ld_post_col = st.selectbox("Postal Code (Leads)",    df_leads.columns, key="ld_post")

        sf_sel   = [sf_name_col, sf_post_col, sf_addr_col, sf_grid_col, sf_status_col]
        ld_sel   = [ld_name_col, ld_addr_col, ld_post_col]
        sf_dupes = len(sf_sel) != len(set(sf_sel))
        ld_dupes = len(ld_sel) != len(set(ld_sel))
        if sf_dupes: st.warning("⚠️ Duplicate column selection in Salesforce mapping.")
        if ld_dupes: st.warning("⚠️ Duplicate column selection in Leads mapping.")

        st.caption("ℹ️ Unit numbers (e.g. #03-12) are extracted automatically from the address fields.")

        if st.button("🚀 Run Priority Waterfall", type="primary",
                     disabled=(sf_dupes or ld_dupes), key="run_lead"):

            df_sf        = preprocess_sf(df_sf_raw, sf_name_col, sf_post_col, sf_addr_col, sf_status_col)
            sf_by_postal = {k: v.reset_index(drop=True) for k, v in df_sf.groupby('postal_fixed')}

            results      = []
            progress_bar = st.progress(0)
            status_text  = st.empty()
            total        = len(df_leads)

            for idx, lead in df_leads.iterrows():
                l_name   = str(lead[ld_name_col])
                l_addr   = str(lead[ld_addr_col]).strip()
                l_postal = fix_postal(lead[ld_post_col])
                l_unit   = extract_unit(l_addr)

                final_status   = "NEW"
                priority_level = "P1"
                best_score     = 0
                match_data     = {"grid": "", "name": "", "status": ""}

                is_postal_missing = l_postal in ["", "nan", "0", "000000"]

                if is_postal_missing and l_addr.lower() == "singapore":
                    final_status, priority_level = "Invalid - No address", "N/A"

                else:
                    # --- Run matching FIRST, before the blacklist check ---
                    l_name_norm = deep_normalize(l_name)
                    candidates  = sf_by_postal.get(l_postal)

                    sl_hit = None
                    if l_unit and candidates is not None:
                        sl_pool = candidates[
                            (candidates['unit_extracted'] == l_unit) &
                            (candidates['unit_extracted'] != "")
                        ]
                        if not sl_pool.empty:
                            sl_hit = sl_pool.iloc[0]

                    if candidates is not None and not candidates.empty:
                        result = process.extractOne(
                            l_name_norm,
                            candidates['name_norm'].tolist(),
                            scorer=fuzz.token_sort_ratio
                        )
                        if result:
                            best_score = result[1]
                            best_row   = candidates.iloc[result[2]]
                            match_data = {
                                "grid":         best_row[sf_grid_col],
                                "name":         best_row[sf_name_col],
                                "status":       best_row[sf_status_col],
                                "status_group": best_row['status_group'],
                            }

                    sg             = match_data.get("status_group", "")
                    is_blacklisted = any(word in l_name.lower() for word in invalid_keywords)

                    if   best_score == 100 and sg == "active_pipeline":
                        final_status, priority_level = "DUPLICATE",        "P4"
                    elif best_score >= p3_threshold and sg == "active_pipeline":
                        final_status, priority_level = "DUPLICATE",        "P3"
                    elif best_score >= p2_threshold and sg == "active_pipeline":
                        final_status, priority_level = "POTENTIAL",        "P2"
                    elif best_score >= p2_threshold and sg == "win_back":
                        final_status, priority_level = "WIN-BACK",         "WB"
                    elif best_score >= p2_threshold and sg == "win_back_failed":
                        final_status, priority_level = "WIN-BACK FAILED",  "WBF"
                    elif sl_hit is not None and best_score < p2_threshold:
                        final_status, priority_level = "SAME LOCATION - NEW BUSINESS", "SL"
                        match_data = {
                            "grid":   sl_hit[sf_grid_col],
                            "name":   sl_hit[sf_name_col],
                            "status": sl_hit[sf_status_col],
                        }
                    elif is_blacklisted:
                        # Only fall back to "Invalid - Not restaurant" once we know
                        # there is no genuine SF match of any kind.
                        final_status, priority_level = "Invalid - Not restaurant", "N/A"
                    else:
                        final_status, priority_level = "NEW", "P1"

                show_match = best_score >= p2_threshold or priority_level == "SL"
                res_row = lead.to_dict()
                res_row.update({
                    "MATCH_STATUS":      final_status,
                    "PRIORITY":          priority_level,
                    "MATCH_SCORE":       f"{int(best_score)}%",
                    "SF_GRID_ID":        match_data.get("grid",   "") if show_match else "",
                    "SF_MATCH_NAME":     match_data.get("name",   "") if show_match else "",
                    "SF_ACCOUNT_STATUS": match_data.get("status", "") if show_match else "",
                })
                results.append(res_row)
                progress_bar.progress((idx + 1) / total)
                status_text.caption(f"Processing {idx + 1:,} of {total:,} leads...")

            status_text.empty()
            final_df = pd.DataFrame(results)
            st.success("✅ Waterfall Matching Complete!")

            m_cols = st.columns(7)
            for col, (label, val) in zip(m_cols, [
                ("Total",           len(final_df)),
                ("🟢 New P1",       len(final_df[final_df["PRIORITY"] == "P1"])),
                ("🟡 Potential P2", len(final_df[final_df["PRIORITY"] == "P2"])),
                ("🔴 Duplicate",    len(final_df[final_df["PRIORITY"].isin(["P3","P4"])])),
                ("🟣 Win-back WB",  len(final_df[final_df["PRIORITY"] == "WB"])),
                ("🔵 Same Loc SL",  len(final_df[final_df["PRIORITY"] == "SL"])),
                ("⚪ Invalid",      len(final_df[final_df["PRIORITY"] == "N/A"])),
            ]):
                col.metric(label, val)

            st.subheader("Priority Breakdown")
            priority_order  = ["P1","P2","P3","P4","WB","WBF","SL","N/A"]
            priority_counts = final_df["PRIORITY"].value_counts()
            chart_data = pd.DataFrame({
                "Priority": priority_order,
                "Count":    [priority_counts.get(p, 0) for p in priority_order]
            }).set_index("Priority")
            st.bar_chart(chart_data)

            def style_lead(row):
                return [{ "P1":"background-color:#d4edda","P2":"background-color:#fff3cd",
                           "P3":"background-color:#f8d7da","P4":"background-color:#f8d7da",
                           "WB":"background-color:#ede9fc","WBF":"background-color:#e2e3e5",
                           "SL":"background-color:#d0e8fb","N/A":"background-color:#e2e3e5",
                         }.get(row["PRIORITY"], "")] * len(row)

            def show_lead_table(df):
                if df.empty: st.info("No leads in this category.")
                else: st.dataframe(df.style.apply(style_lead, axis=1), use_container_width=True)

            counts = {p: len(final_df[final_df["PRIORITY"] == p])
                      for p in ["P1","P2","P3","P4","WB","WBF","SL","N/A"]}

            st.subheader("Results")
            tabs = st.tabs([
                f"All ({len(final_df)})",
                f"🟢 New P1 ({counts['P1']})",
                f"🟡 Potential P2 ({counts['P2']})",
                f"🔴 Duplicates ({counts['P3']+counts['P4']})",
                f"🟣 Win-back WB ({counts['WB']})",
                f"🔵 Same Location SL ({counts['SL']})",
                f"⚪ Invalid ({counts['N/A']})",
            ])
            with tabs[0]: show_lead_table(final_df)
            with tabs[1]: show_lead_table(final_df[final_df["PRIORITY"] == "P1"])
            with tabs[2]: show_lead_table(final_df[final_df["PRIORITY"] == "P2"])
            with tabs[3]: show_lead_table(final_df[final_df["PRIORITY"].isin(["P3","P4"])])
            with tabs[4]: show_lead_table(final_df[final_df["PRIORITY"] == "WB"])
            with tabs[5]: show_lead_table(final_df[final_df["PRIORITY"] == "SL"])
            with tabs[6]: show_lead_table(final_df[final_df["PRIORITY"] == "N/A"])

            st.download_button(
                "📥 Download Full Results", final_df.to_csv(index=False),
                "waterfall_results.csv", mime="text/csv", key="dl_lead"
            )

# ═══════════════════════════════════════════════════════════════
# TAB 2 — SF ACCOUNT AUDIT
# ═══════════════════════════════════════════════════════════════

with main_tab2:
    st.markdown(
        "Upload your Salesforce master to find suspected duplicate accounts "
        "within the database. Run this occasionally for data hygiene."
    )

    audit_file = st.file_uploader(
        "Upload Salesforce Master (for audit)", type=['csv', 'xlsx'], key="audit_file"
    )

    if audit_file:
        with st.spinner("Loading file..."):
            df_audit_raw = load_file(audit_file)

        st.divider()
        st.subheader("Column Mapping")
        a1, a2 = st.columns(2)

        with a1:
            au_name_col   = st.selectbox("Account Name",   df_audit_raw.columns, key="au_name")
            au_post_col   = st.selectbox("Postal Code",    df_audit_raw.columns, key="au_post")
            au_addr_col   = st.selectbox("Address",        df_audit_raw.columns, key="au_addr")
        with a2:
            au_status_col = st.selectbox("Account Status", df_audit_raw.columns, key="au_status")
            au_grid_col   = st.selectbox("Grid ID",        df_audit_raw.columns, key="au_grid")
            au_id_col     = st.selectbox("SF 18 Char ID",  df_audit_raw.columns, key="au_id")

        audit_threshold = st.slider(
            "Name similarity threshold for audit",
            min_value=50, max_value=100, value=70, step=5,
            key="audit_thresh",
            help="Pairs with name similarity at or above this are flagged as suspected duplicates"
        )
        st.caption(
            f"Pairs with name similarity ≥ {audit_threshold}% at the same postal code "
            f"and unit number will be flagged."
        )

        if st.button("🔍 Run SF Account Audit", type="primary", key="run_audit"):

            with st.spinner("Preprocessing SF accounts — normalising names and extracting unit numbers..."):
                df_audit = preprocess_sf(
                    df_audit_raw, au_name_col, au_post_col, au_addr_col, au_status_col
                )

            with st.spinner("Scanning for suspected duplicates..."):
                pairs = find_sf_duplicates(
                    df_audit, au_name_col, au_addr_col,
                    au_status_col, au_grid_col, au_id_col,
                    audit_threshold
                )

            if not pairs:
                st.success(
                    f"✅ No suspected duplicates found at {audit_threshold}% similarity threshold. "
                    f"Your SF data looks clean."
                )
            else:
                pairs_df = pd.DataFrame(pairs)
                st.success(f"✅ Audit complete — {len(pairs_df):,} suspected duplicate pairs found.")

                # Metrics
                high   = len(pairs_df[pairs_df["RISK_LEVEL"] == "🔴 High"])
                medium = len(pairs_df[pairs_df["RISK_LEVEL"] == "🟡 Medium"])
                low    = len(pairs_df[pairs_df["RISK_LEVEL"] == "🟢 Low"])

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Total Pairs",    len(pairs_df))
                m2.metric("🔴 High Risk",   high)
                m3.metric("🟡 Medium Risk", medium)
                m4.metric("🟢 Low Risk",    low)

                # Risk breakdown chart
                st.subheader("Risk Breakdown")
                risk_chart = pd.DataFrame({
                    "Risk":  ["🔴 High", "🟡 Medium", "🟢 Low"],
                    "Pairs": [high, medium, low]
                }).set_index("Risk")
                st.bar_chart(risk_chart)

                # Colour-coded tabbed results
                def style_audit(row):
                    return [{
                        "🔴 High":   "background-color:#f8d7da",
                        "🟡 Medium": "background-color:#fff3cd",
                        "🟢 Low":    "background-color:#d4edda",
                    }.get(row["RISK_LEVEL"], "")] * len(row)

                def show_audit_table(df):
                    if df.empty:
                        st.info("No pairs in this category.")
                    else:
                        st.dataframe(
                            df.style.apply(style_audit, axis=1),
                            use_container_width=True
                        )

                st.subheader("Suspected Duplicate Pairs")
                a_tabs = st.tabs([
                    f"All ({len(pairs_df)})",
                    f"🔴 High Risk ({high})",
                    f"🟡 Medium Risk ({medium})",
                    f"🟢 Low Risk ({low})",
                ])
                with a_tabs[0]: show_audit_table(pairs_df)
                with a_tabs[1]: show_audit_table(pairs_df[pairs_df["RISK_LEVEL"] == "🔴 High"])
                with a_tabs[2]: show_audit_table(pairs_df[pairs_df["RISK_LEVEL"] == "🟡 Medium"])
                with a_tabs[3]: show_audit_table(pairs_df[pairs_df["RISK_LEVEL"] == "🟢 Low"])

                st.download_button(
                    "📥 Download Audit Results",
                    pairs_df.to_csv(index=False),
                    "sf_audit_results.csv",
                    mime="text/csv",
                    key="dl_audit"
                )

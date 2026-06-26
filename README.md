[README.md](https://github.com/user-attachments/files/29371666/README.md)
# 🇸🇬 SF Deduplicator — Priority Waterfall

A Streamlit app for deduplicating new lead lists against the Salesforce master account database. Built for Sales Ops teams to quickly classify incoming leads before CRM entry.

---

## What it does

Runs each new lead through a 5-step waterfall of rules and assigns a priority tier:

| Priority | Status | What it means |
|----------|--------|---------------|
| **P1** | New | No match found. High priority — action this lead. |
| **P2** | Potential | Partial name match (50–69%). Review before actioning. |
| **P3** | Duplicate | Strong name match (70–99%). Likely already in Salesforce. |
| **P4** | Duplicate | Exact match (100%). Already in Salesforce. Do not action. |
| **N/A** | Invalid | Failed address or name validation. Skip entirely. |

---

## How the waterfall works

1. **Postal code fix** — auto-corrects 5-digit codes to 6 digits (adds leading zero)
2. **Address check** — flags leads with no valid address
3. **Name blacklist** — removes hotels, bars, food courts, and other non-restaurant entries
4. **Name normalisation** — strips brackets, removes text after dashes, converts Chinese to Pinyin
5. **Fuzzy token match** — matches normalised names within the same postal code using order-insensitive scoring

---

## Preparing your files

The app accepts `.csv` or `.xlsx` uploads. You will map columns manually inside the app, so exact column names don't matter — but your files must contain the following information:

**Salesforce Master must have:**
- Outlet/account name
- Postal code
- Grid ID
- Account status

**New Lead List must have:**
- Outlet name
- Street address
- Postal code

---

## Accessing the app

The app is hosted on Streamlit Cloud and requires a password. Contact your Sales Ops team lead for the access link and password.

---

## Running locally (optional)

```bash
git clone https://github.com/YOUR_USERNAME/sf-deduplicator.git
cd sf-deduplicator
pip install -r requirements.txt
streamlit run app.py
```

> Note: You will need to create a `.streamlit/secrets.toml` file locally with `password = "yourpassword"` for the login to work.

---

## Tech stack

- [Streamlit](https://streamlit.io) — UI framework
- [pandas](https://pandas.pydata.org) — data processing
- [rapidfuzz](https://github.com/maxbachmann/RapidFuzz) — fuzzy string matching
- [pypinyin](https://github.com/mozillazg/python-pinyin) — Chinese to Pinyin conversion

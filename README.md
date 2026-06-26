# 🇸🇬 SF Deduplicator Waterfall

A Streamlit app for deduplicating Salesforce leads against a master account list using a priority waterfall rule hierarchy, fuzzy name matching, and Chinese-to-Pinyin normalization.

## Features
- Postal code validation & auto-correction
- Keyword blacklist filtering (bars, hotels, food courts, etc.)
- Fuzzy token matching with deep name normalization (supports Chinese characters)
- Priority tiering: P1 (New) → P4 (Exact Duplicate)
- Summary metrics dashboard

## Running locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploying to Streamlit Community Cloud

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io) and click **New app**
3. Select your repo, branch (`main`), and set the main file path to `app.py`
4. Click **Deploy**

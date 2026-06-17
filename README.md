---
title: Barren Business Development
emoji: 📊
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8501
tags:
- streamlit
- automation
- machine-learning
pinned: false
license: other
short_description: Intelligent Transaction Classification Automation.
---

# Barren Business Development
### Intelligent Transaction Classification Automation

A simple, reliable tool that classifies transactions to the right **account code**
automatically. Seed a few rows with the correct code, and the app propagates those
codes to every similar transaction — scoring its confidence and flagging anything
uncertain for a quick review. A file that used to take an hour is done in minutes.

> Generic and industry-neutral: works for property management, trades, e‑commerce,
> or any data-heavy workflow that needs clean, consistent transaction classification.

---

## What it does

- 📤 **Upload** an Excel or CSV transaction export.
- 🌱 **Seed** a few rows with the correct account / category code.
- ⚙️ **Fill Down Automation** — propagate codes using a hierarchy of:
  - **Keyword rules** (deterministic, saved for reuse)
  - **Learned memory** (codes you've approved before)
  - **Similarity** (TF-IDF by default; optional semantic embeddings)
  - **Optional ML** (Logistic Regression, plus SetFit when installed)
- 🔎 **Review queue** — approve uncertain rows; approvals train the system.
- 📊 **Models & History** — track learning progress and past runs.
- 💾 **Export** — Excel (formatting preserved + summary tab) or a clean CSV.

The demo runs on the lightweight engines (TF-IDF + Logistic Regression) so it
starts fast. Semantic / SetFit are optional.

> 📖 In-app help: click **"User Guide"** in the sidebar for a non-technical walkthrough.

---

## Quick demo

1. Open the app.
2. On the dashboard, click **Load sample dataset** (neutral, multi-industry data).
3. Click **Full Intelligent Run** in the toolbar.
4. Approve a few rows in the **Review Queue**, or add a rule under **Rules**.
5. Click **Export** to download the result.

---

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run main.py
```

Open <http://localhost:8501>.

---

## Deploy to Hugging Face Spaces

This repo deploys as a **Docker** Space (the `Dockerfile` controls the build and
binds port **8501**).

1. Create a new Space → **SDK: Docker**.
2. Push this folder to the Space's git remote:

   ```bash
   cd code_down_ML
   git init -b main
   git add -A
   git commit -m "Barren Business Development — initial deploy"
   git remote add origin https://huggingface.co/spaces/<your-user>/<space-name>
   git push -u origin main
   ```

3. The Space builds and launches automatically.

### Persistence & demo behavior

- Runtime data (SQLite DB, models, logs) is written under **`/data`**
  (`FILLDOWN_DATA_DIR=/data`, set in the `Dockerfile`). Enable **Persistent storage**
  in Space settings to keep rules/learning across restarts. The app falls back to a
  temp dir automatically if the FS is read-only — it never crashes.
- In demo / HF context the app starts in a clean state (no clients, rules, or
  history) so every visitor sees an unused tool.

| Variable | Default | Purpose |
| --- | --- | --- |
| `FILLDOWN_DEMO` | `1` | Demo banner + fresh-state reset on start. |
| `FILLDOWN_DEMO_RESET` | `1` | `0` keeps data across restarts. |
| `FILLDOWN_DATA_DIR` | `/data` | Writable root for DB/models/logs. |
| `FILLDOWN_MAX_ROWS` | `20000` | Soft row cap per upload (`0` = unlimited). |

---

## Project structure

```
code_down_ML/
├── main.py                 # Streamlit entry point
├── Dockerfile              # HF Spaces (Docker SDK), port 8501
├── config.yaml             # thresholds + column mapping
├── requirements.txt        # core engines (lightweight)
├── requirements-semantic.txt  # optional semantic + SetFit
├── .streamlit/config.toml  # basic dark theme + server settings
├── src/                    # engines, config, logic
├── ui/                     # Streamlit views
├── utils/                  # storage, account codes, sample data
├── models/                 # pydantic schemas
└── tests/                  # pytest suite + smoke test
```

---

## Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python smoke_test.py
```

---

## License & contact

Proprietary prototype — see `LICENSE`. All rights reserved.
**Barren Business Development** — barren.danny@gmail.com · (614) 440-3220 · dannybarren.com

# Streamlit Cloud Deployment

## Prerequisites

- GitHub account with this repo (public or with Streamlit Cloud given access).
- Streamlit Cloud account — https://share.streamlit.io/
- OpenRouter API key with a **monthly spending limit set** —
  https://openrouter.ai/keys ($5–10/month is reasonable for early testing).

## Steps

### 1. Push the latest commit to GitHub

```bash
git add .
git commit -m "Stage 5: Streamlit Cloud deploy prep"
git push origin main
```

Confirm at https://github.com/CindaX/geo_toolkit that the `main` branch
contains:

- `geo_toolkit_app.py` (entry script)
- `requirements.txt`
- `shared/secrets.py` (layered secret access)
- `.streamlit/secrets.toml.example` (template, real one is gitignored)

### 2. Create the Streamlit Cloud app

1. Go to https://share.streamlit.io/ → **New app**.
2. Pick the repo `CindaX/geo_toolkit`, branch `main`.
3. **Main file path**: `geo_toolkit_app.py`.
4. **App URL** (optional): pick a slug like `geo-toolkit` (full URL becomes
   `geo-toolkit.streamlit.app`).
5. Click **Advanced settings** → set **Python version** to `3.12` (the closest
   stable to local dev's 3.14; 3.11 also works).
6. Don't click Deploy yet — set secrets first (next step).

### 3. Set secrets on Streamlit Cloud

Under **Advanced settings → Secrets**, paste the same TOML you'd use locally
in `.streamlit/secrets.toml`:

```toml
OPENROUTER_API_KEY = "sk-or-v1-...your-real-key..."

# Optional (defaults are fine):
# OPENROUTER_REFERER = "https://geo-toolkit.streamlit.app"
# OPENROUTER_APP_NAME = "geo_toolkit"
```

The runtime reads via `shared.secrets.get_secret()`, which tries
`st.secrets` first and falls back to `.env` locally.

### 4. Deploy

Click **Deploy**. First build pulls `requirements.txt` (~60–90 seconds) and
boots the app. Watch the build log for errors.

Once green, the app is live at `https://geo-toolkit.streamlit.app/` (or your
chosen slug). All three tools are accessible via the sidebar:

- `/` and `/audit`  → GEO Audit (default page)
- `/prompts`        → Prompt Opportunity Engine
- `/assets`         → Asset Generator

### 5. Verify

- Open the URL — sidebar should show **🌍 GEO Toolkit** with three tools.
- Click into each tool — landing hero, form, and footer should render.
- Run a small audit (e.g. `https://stripe.com`) to confirm OpenRouter API
  works end-to-end. Cost: roughly $0.05–$0.15 for one full audit.
- Cross-tool prefill: after the audit completes, navigate to Prompt Engine —
  the brand name should be auto-filled with a "📥 imported from Audit" banner.

## Updating the app

Streamlit Cloud auto-redeploys on every `git push` to `main`. To rotate
the API key, edit it in **Settings → Secrets** (no redeploy needed).

## Local-vs-Cloud differences

| Concern | Local dev | Streamlit Cloud |
|---|---|---|
| Secrets source | `.env` (via python-dotenv) | `.streamlit/secrets.toml` (dashboard) |
| Python version | whatever's in `.venv` (3.14) | pinned to 3.12 (or what you chose) |
| Disk cache | `data/cache/` persists across runs | ephemeral — wiped on each redeploy |
| Crawler `[FETCH]` logs | visible in terminal | visible in Streamlit Cloud's "Logs" tab |

## Spend safety checklist

- [ ] OpenRouter dashboard: monthly spending limit set (e.g. $10/month).
- [ ] App URL is private/unlisted until you're ready for traffic.
- [ ] No auto-trigger Streamlit features (charts/dataframes are fine).
- [ ] Cache dir is gitignored — no leaked crawl payloads in repo.

## Troubleshooting

**Build fails on `pip install`** → check `requirements.txt` for typos.
Floors are conservative; if a package is too new, lower the floor.

**App boots but shows "Missing required secret OPENROUTER_API_KEY"** →
the secret name on Streamlit Cloud must match exactly (case-sensitive).
Re-paste from the example file.

**Crawler returns 403 on some sites** → already handled by the 3-step UA
fallback in `shared/crawler.py::_fetch`. If you see `attempt 3/3` fail
in logs, the site has stricter WAF — note it as a known limitation.

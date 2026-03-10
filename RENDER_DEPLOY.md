# Deploy Vireel backend to Render

Frontend stays on Vercel; backend runs on Render so you don’t need to start the site locally or use ngrok.

## 1. Push repo to GitHub

Ensure your project is in a GitHub repo and push the latest code (including `render.yaml` and `requirements.txt`).

## 2. Create a Render web service

1. Go to [Render](https://render.com) and sign in.
2. **New** → **Web Service**.
3. Connect the GitHub repo and select the clipper project.
4. Render can auto-detect from `render.yaml`, or set:
   - **Runtime:** Python
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn webapp.server:app --host 0.0.0.0 --port $PORT`
   - **Plan:** Free

## 3. Environment variables (production)

In the Render dashboard → your service → **Environment**, add:

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | Your OpenAI API key (optional; for story enhance / Whisper). |
| `TIKTOK_CLIENT_KEY` | TikTok app Client Key. |
| `TIKTOK_CLIENT_SECRET` | TikTok app Client Secret. |
| `APP_BASE_URL` | **Set after first deploy** (see step 5). |

Do **not** set `PORT`; Render provides it. If your Vercel frontend uses a different URL, add it via `CORS_ORIGINS` (comma-separated list).

## 4. Deploy

Click **Deploy**. Wait until the service is live. The first deploy may take a few minutes.

## 5. Set APP_BASE_URL and TikTok redirect

1. After deploy, copy the **backend URL** (e.g. `https://vireel-backend.onrender.com`).
2. In Render → **Environment**, set:
   - `APP_BASE_URL` = that URL (no trailing slash), e.g. `https://vireel-backend.onrender.com`
3. **Redeploy** so the backend picks up `APP_BASE_URL`.
4. In the **TikTok Developer Portal**, set the redirect URI for your app to:
   - `{APP_BASE_URL}/api/tiktok/callback`  
   e.g. `https://vireel-backend.onrender.com/api/tiktok/callback`

## 6. Point the frontend at the backend

- **If the frontend is on Vercel:** Set the production API base URL so the app calls the Render backend:
  - In your Vercel project, set an env var (e.g. `API_BASE` or use a build step to inject `window.__API_BASE__`).
  - In `webapp/web/index.html`, you can set `window.__API_BASE__ = 'https://your-render-url.onrender.com';` before loading `app.js`, or inject that from a Vercel env at build time.
- **If users open the app from the Render backend URL:** The backend injects the correct API base automatically; no extra config.

## 7. Health check

Render can use **GET /health** for health checks. It returns `{"ok": true}`.

## Local workflow (unchanged)

- Run the backend locally with `START_WEBSITE.bat`, `start_vireel_backend.bat`, or `python webapp/server.py`.
- Local `.env` is still loaded from the repo root; no need to change local setup.

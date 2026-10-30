# WAMSS Dashboard (frontend)

Vite + React + TypeScript dashboard for the WAMSS (Workplace Activity Monitoring
and Surveillance System) backend at `../backend`.

## Prerequisites

- Node.js 18 or newer
- The WAMSS backend running (default expected at `http://localhost:8000`)

## Setup

```sh
cp .env.example .env   # optional; only needed to point local dev at a backend
npm install
npm run dev
```

Then open the URL printed by Vite (default `http://localhost:5173`).

## Configuration

The backend base URL is read from `VITE_API_URL` (a Vite build-time env var).

- **Local dev** (`npm run dev`): set `VITE_API_URL=http://localhost:8000`
  (see `.env.example`) so the dev server talks to your local backend.
- **Docker deployment**: leave `VITE_API_URL` empty (the default). The
  dashboard then calls the same-origin path `/api/*`, which the bundled
  nginx reverse-proxies to the backend with the API key attached
  server-side. See `nginx.conf.template` and `../docs/AUTH.md`.

If `VITE_API_URL` is unset, the app defaults to the same-origin `/api` path.

## Build

```sh
npm run build
```

Type-checks (`tsc -b`) and emits a production bundle into `dist/`.

## Features

- **Backend health**: `/health` and `/health/db` status with green/red indicators
- **Live events table**: polls `GET /events` every 5 seconds, newest first; rows with
  decision `REVIEW_REQUIRED` or alert `RESTRICTED_ZONE_DETECTED` are highlighted
- **Camera filter**: dropdown of camera IDs seen in events, filters the table
- **Send test event**: form posting to `POST /event`, shows the returned
  decision and alerts

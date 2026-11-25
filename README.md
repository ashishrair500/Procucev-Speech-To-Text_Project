# Real-time Speech-to-Text (Hindi + English)

A web app where you speak into your browser microphone and watch a **live
transcript** appear, including speech that mixes Hindi and English in the same
sentence (**Hinglish**). When you stop, the transcript is saved to PostgreSQL,
and a dashboard lets you view, edit, search and delete every saved recording.

Built for **Assignment 1: Real-time Transcription**.
Stack: **Python (FastAPI) · PostgreSQL · Gemini Live API**.

**Repository:** <https://github.com/ashishrair500/Procucev-Speech-To-Text_Project>
**Live demo:** _link added after deployment_

---

## 1. What it does

- **Live transcription** while you speak (text updates as you talk).
- **Hindi + English in one sentence** — Hindi words in Devanagari, English words
  in Latin: `कल meeting में deadline है, please report भेज देना।`
- **Save on stop** — the finished transcript is written to PostgreSQL.
- **Full CRUD dashboard** — list, search, paginate, view, edit, delete, copy and
  download as `.txt`.
- **Language hint** dropdown (Hindi + English mixed / English only / Hindi only).
- Automatic **reconnect** if the Gemini session drops, and clear error messages
  for denied mic permission, lost connection or quota limits.
- **Light, responsive interface** — left-aligned editorial layout, a single teal
  accent, hairline rules instead of cards, and a stacked table view down to 360 px.

> **Note on the assignment text:** the brief says "Text to Speech", which is
> almost certainly a typo for **Speech to Text**. The whole task converts voice
> into text, and that is what this project does.

---

## 2. Architecture

```
 ┌──────────────┐   mic audio (16 kHz mono PCM)   ┌──────────────────┐
 │   Browser    │ ───────── WebSocket ──────────► │  FastAPI server  │
 │              │                                  │                  │
 │ AudioWorklet │ ◄──── partial / final text ────  │  session manager │
 │  downmix +   │                                  │        │         │
 │  resample    │                                  │        ▼         │
 │              │        JSON REST (CRUD)          │  Gemini Live API │
 │  dashboard   │ ◄──────────────────────────────► │ (bidiGenerate)   │
 └──────────────┘                                  │        │         │
                                                   │        ▼         │
                                                   │  PostgreSQL      │
                                                   └──────────────────┘
```

**Data flow, in plain words**

1. You click **Start**. The browser asks for microphone permission.
2. An **AudioWorklet** reads the mic, down-mixes to mono, resamples to **16 kHz**,
   converts to **16-bit little-endian PCM** and emits **100 ms frames** (3200 bytes).
3. Each frame is sent as a **binary WebSocket** message to the FastAPI backend.
4. The backend keeps **one Gemini Live session per connected user** and forwards
   each frame into it (`send_realtime_input`).
5. Gemini streams back `interim_input_transcription` (the live, growing text) and,
   at the end, a finalised `input_transcription`. The backend forwards text to the
   browser over the same WebSocket.
6. You click **Stop**. The backend sends `audio_stream_end`, waits for the final
   text, and **saves one row** in PostgreSQL, returning it to the browser.
7. The dashboard talks to the REST API to list, search, edit and delete rows.

**Why a backend proxy instead of calling Gemini from the browser:** the API key
must never ship to the browser, where anyone could read it from the page source
and burn your quota. The browser talks to your server; your server talks to Gemini.

**In production** the database is **Neon Postgres**. Application query traffic
goes through the pooled endpoint and schema DDL goes through the direct endpoint
(the reason is in section 3). Locally, docker-compose runs PostgreSQL 16 instead.

**Key components**

| Piece | File | Responsibility |
|---|---|---|
| Gemini session manager | `app/providers/gemini_live.py` | open / keep alive / reconnect / close the upstream session |
| WebSocket bridge | `app/routers/ws.py` | browser audio ⇄ Gemini ⇄ database |
| REST CRUD | `app/routers/transcriptions.py` | create / list / read / update / delete |
| Models & schemas | `app/models.py`, `app/schemas.py` | table + request/response shapes |
| Settings | `app/config.py` | all environment variables |
| Frontend | `static/` | mic capture, live view, dashboard |
| AudioWorklet | `static/pcm-worklet.js` | down-mix + resample + 16-bit PCM framing |

---

## 3. Technology choices

| Layer | Choice | Why |
|---|---|---|
| Speech-to-text | **Gemini Live API** (`gemini-3.5-transcribe-live`) | Truly streaming, handles Hinglish well, no GPU/model hosting |
| Backend | **FastAPI** | Native WebSocket support, simple REST, automatic API docs |
| Database | **PostgreSQL** (16 in local Docker, 18 on Neon) | Relational, free hosted options (Neon/Supabase) |
| ORM | **SQLAlchemy 2 (async)** | Standard, works with Postgres and SQLite (tests) |
| Frontend | **Plain HTML/CSS/JS** | No build step and nothing to ship; a light editorial theme (Fraunces / DM Sans / IBM Plex Mono, one teal accent) with no UI framework |
| Browser audio | **Web Audio API + AudioWorklet** | Produces the exact PCM format Gemini Live expects |
| Packaging | **Docker + docker-compose** | One-command local setup; required by most free hosts |

### Three implementation details worth knowing

- **`api_version` must be pinned to `v1beta`.** `google-genai` 3.6.1 defaults to
  `v1`, but the Live websocket endpoint only exists on `v1beta`/`v1alpha` — with
  the default the handshake fails with **HTTP 404**. See
  `GEMINI_API_VERSION` in `.env.example`.
- **`interim_input_transcription.text` is cumulative**, not a delta — the live
  panel *replaces* the text on each event instead of appending. The final
  `input_transcription` is the authoritative complete transcript, and it is
  followed by `generation_complete`.
- **Queries use the pooled database URL; schema DDL uses the direct one.** Neon's
  pooled endpoint runs PgBouncer in transaction mode, which cannot run schema
  operations. So `app/database.py` builds two engines and runs `CREATE TABLE` on
  `DATABASE_URL_UNPOOLED`. It also rewrites the provider's libpq URL for asyncpg:
  the bare `postgresql://` scheme is switched to `+asyncpg`, `sslmode` becomes a
  connect arg, `channel_binding` is dropped (asyncpg rejects it), and the
  prepared-statement cache is disabled on `-pooler` hosts.

### About the model name

`gemini-3.5-transcribe-live` is a purpose-built Live **transcription** model: it
only implements `bidiGenerateContent` and listens rather than chatting back,
which is exactly what the brief asks for. The model name lives in an environment
variable (`GEMINI_LIVE_MODEL`) so it can be swapped without touching code; the
key on this account can also reach `gemini-3.1-flash-live-preview` and
`gemini-3.8-live` (configured as fallbacks).

---

## 4. Database

One table is enough for the core brief.

### `transcriptions`

| Column | Type | Notes |
|---|---|---|
| `id` | Integer, PK | auto-generated |
| `title` | Text, not null | defaults to the recording date/time |
| `text` | Text, not null | the full transcript |
| `language_hint` | Text, nullable | `hi-en`, `en` or `hi` |
| `duration_seconds` | Integer, nullable | length of the recording |
| `source` | Text, not null | `gemini-live` |
| `created_at` | Timestamp (tz) | set on insert |
| `updated_at` | Timestamp (tz) | updated on every edit |

Indexes on `created_at` and `title` keep the history list fast. Search is a
case-insensitive `LIKE` on `title` and `text`. Tables are created automatically on
startup (`create_all`) on the **direct** database URL, since PgBouncer's
transaction pooling cannot run schema DDL; Alembic can replace this later.

---

## 5. API reference

Base URL: `http://localhost:8000` · interactive docs at **`/docs`**.

| Method | Path | Purpose | Success |
|---|---|---|---|
| `POST` | `/api/transcriptions` | create a transcription | `201` |
| `GET` | `/api/transcriptions` | list with `?page=&page_size=&search=` | `200` |
| `GET` | `/api/transcriptions/{id}` | read one | `200` / `404` |
| `PUT` | `/api/transcriptions/{id}` | update `title` and/or `text` | `200` / `404` |
| `DELETE` | `/api/transcriptions/{id}` | delete | `204` / `404` |
| `GET` | `/health` | health check (used by the host) | `200` |
| `WS` | `/ws/transcribe` | live audio ⇄ transcript | — |

Validation errors return `422`.

**`GET /api/transcriptions` example**

```json
{
  "items": [
    {
      "id": 1,
      "title": "Recording 25 Nov 2026, 19:52:25",
      "text": "नमस्ते, कल मीटिंग में डेडलाइन है। प्लीज रिपोर्ट भेज देना।",
      "language_hint": "hi-en",
      "duration_seconds": 4,
      "source": "gemini-live",
      "created_at": "2026-11-25T14:22:25.362189Z",
      "updated_at": "2026-11-25T14:22:25.362189Z"
    }
  ],
  "total": 1, "page": 1, "page_size": 10, "pages": 1
}
```

**WebSocket protocol** (`/ws/transcribe`)

Client → server:

| Message | Meaning |
|---|---|
| `{"type":"start","language_hint":"hi-en"}` | begin a session |
| `<binary>` | raw 16 kHz mono 16-bit PCM |
| `{"type":"stop"}` | finish and save |
| `{"type":"ping"}` | keepalive |

Server → client:

| Message | Meaning |
|---|---|
| `{"type":"status","state":"…","message":"…"}` | progress (`connecting`/`listening`/`saving`/`idle`) |
| `{"type":"partial","text":"…"}` | cumulative live transcript |
| `{"type":"final","text":"…"}` | transcript at stop |
| `{"type":"saved","transcription":{…}}` | row persisted to the DB |
| `{"type":"error","message":"…"}` | human-readable error |

---

## 6. Environment variables

Only the **names** are listed here; never commit real values. Copy
`.env.example` to `.env` and fill it in.

| Name | Required | Default | Purpose |
|---|---|---|---|
| `GEMINI_API_KEY` | ✅ | — | AI Studio key (server-side only) |
| `GEMINI_LIVE_MODEL` | — | `gemini-3.5-transcribe-live` | primary Live model |
| `GEMINI_FALLBACK_MODELS` | — | `gemini-3.1-flash-live-preview,gemini-3.8-live` | tried on failure |
| `GEMINI_API_VERSION` | — | `v1beta` | **must be `v1beta`** for the Live endpoint |
| `DATABASE_URL` | ✅ | compose `db` service | SQLAlchemy async URL for **all query traffic** (the pooled endpoint on Neon) |
| `DATABASE_URL_UNPOOLED` | — | falls back to `DATABASE_URL` | **direct** endpoint, used for schema DDL at startup |
| `ENVIRONMENT` | — | `development` | shown in `/health` |
| `ALLOWED_ORIGINS` | — | `*` | CORS / WebSocket origin allow-list |
| `MAX_SESSION_SECONDS` | — | `900` | hard cap per recording |
| `MAX_CONCURRENT_SESSIONS` | — | `4` | protects the free quota |
| `MAX_TRANSCRIPT_CHARS` | — | `50000` | input size guard |

---

## 7. Running it locally

### Option A — Docker (recommended, one command)

Needs Docker Desktop running. Requires a `.env` file (see below).

```bash
cp .env.example .env          # then paste your GEMINI_API_KEY
docker compose up --build     # starts PostgreSQL + the app
```

Open <http://localhost:8000>. Stop with `Ctrl+C`; add `-d` to run detached.

> `docker-compose` sets `DATABASE_URL` to the `db` service automatically, so the
> `localhost` value in `.env.example` is overridden inside the container.

### Option B — without Docker

You need a PostgreSQL database. The quickest is to run just the DB in Docker and
the app on your machine:

```bash
docker compose up -d db                       # PostgreSQL on :5432

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                          # set GEMINI_API_KEY + localhost DB URL
uvicorn app.main:app --reload
```

Open <http://localhost:8000>.

> The browser microphone only works on **HTTPS or `localhost`** — deployed
> versions must be HTTPS (all the hosts below provide it).

### Verifying without a microphone

Two helper scripts stream *synthesised* speech (macOS `say`) through the exact
same path, so you can test the pipeline without talking:

```bash
# 1. Proves the key, model and config work (streams a WAV/PCM file or --say text)
python scripts/gemini_live_test.py --say "नमस्ते, कल meeting में deadline है"

# 2. Full browser→server→Gemini→DB path against a running server
python scripts/ws_smoke.py "नमस्ते, कल मीटिंग में deadline है, please report भेज देना।"

# 3. Hinglish accuracy matrix (prints the table in section 9)
cd scripts && python language_test.py
```

---

## 8. Tests

```bash
pip install -r requirements-dev.txt
pytest
```

21 tests cover create, defaults, read, `404`, empty/oversized input (`422`),
pagination, search (title and text), update, delete, `/health`, database-URL
normalisation, the pooled/direct DDL split, and a regression guard for the
`/health` engine reference. They run against a **throwaway SQLite database**, so
no Postgres or Gemini key is needed.

```
.....................                                                    [100%]
21 passed
```

Manual test areas (per the brief) are listed in section 11.

---

## 9. Hindi + English mixing — real results

Test phrases were synthesised with a Hindi voice and an English voice to imitate
code-switching, then streamed through the app. Output below is genuine.

| # | Spoken | Transcript returned by Gemini |
|---|---|---|
| 1 | नमस्ते, मेरा नाम आशीष है। / I work as a software engineer. | नमस्ते, मेरा नाम आशीष है। I work as a software engineer. |
| 2 | कल client meeting में / we need to discuss the project deadline. | Kal client meeting mein we need to discuss the project deadline. |
| 3 | मुझे एक नया laptop चाहिए / because the current one is too slow. | मुझे एक नया लैपटॉप चाहिए। Because the current one is too slow. |
| 4 | Please send me the report / आज शाम तक, धन्यवाद। | Please send me the report. आज शाम तक। धन्यवाद। |
| 5 | हमारी team ने / deployed the new feature / पिछले हफ़्ते। | हमारी टीम ने deployed the new feature पिछले हफ्ते। |
| 6 | Budget बढ़ाना पड़ेगा / otherwise the timeline will slip. | बजट बढ़ाना पड़ेगा। |

**Reading the results.** The app is configured for **Devanagari Hindi + Latin
English** and rows 1, 3, 4 and 5 show exactly that. Two honest observations:

- The model's **script for short Hindi fragments is not fully deterministic**.
  Row 2 came back entirely in Roman (`Kal … mein`), and on a repeat run row 6
  did too. The system instruction asks for Devanagari, but with very short
  Hindi spans next to English the model sometimes switches to Roman.
- Row 6 **dropped the English tail** once. This looks like a synthetic-audio
  artifact: the two `say` voices differ in loudness, and the injected digital
  silence can be read as end-of-turn. Re-running the same sentence (with the
  same voice for both halves, or a shorter/longer gap) captured both languages
  every time, so it is not a systematic bug — but it is exactly the kind of
  boundary behaviour to be aware of with real code-switching.

Accuracy depends on **microphone quality, accent and background noise**.

---

## 10. Deployment (free tier)

Recommended: **Render (app) + Neon (database)** — both have free plans, provide
HTTPS, and Render supports WebSockets.

1. **Push** this repo to GitHub at
   `ashishrair500/Procucev-Speech-To-Text_Project` (secrets stay out —
   `.gitignore` excludes `.env`).
2. **Provision Neon Postgres.** Click through the Neon console, or use the Neon
   CLI to link the project and pull the credentials straight into `.env`:

   ```bash
   npm i -g neon@latest
   neon login
   neon init -y --org-id <org-id> --project-id <project-id> \
             --branch production --services postgres
   neon deploy
   ```

   You need **both** strings it writes: `DATABASE_URL` (pooled) and
   `DATABASE_URL_UNPOOLED` (direct). Strip any surrounding quotes before pasting
   them into Render — Render treats quotes as part of the value.
3. **Create a Render web service** from the repo. It reads `render.yaml`
   (Docker runtime, free plan, health check `/health`), or you can pick Docker
   manually in the dashboard.
4. **Add the four secrets** (the blueprint marks them `sync: false`, so Render
   prompts for them):

   | Variable | Value |
   |---|---|
   | `GEMINI_API_KEY` | your AI Studio key |
   | `DATABASE_URL` | Neon **pooled** connection string |
   | `DATABASE_URL_UNPOOLED` | Neon **direct** connection string |
   | `ALLOWED_ORIGINS` | `*` for the first deploy, then your Render URL |

   `render.yaml` supplies everything else (`GEMINI_LIVE_MODEL`,
   `GEMINI_API_VERSION=v1beta`, the limits, and so on).
5. **Deploy**, open the HTTPS link, allow the microphone, and speak a mixed
   sentence. Confirm saving, editing and deleting work against the hosted
   database. Then set `ALLOWED_ORIGINS` to
   `https://<service-name>.onrender.com` and redeploy to lock down the CORS and
   WebSocket origin checks.

> **Do not set `PORT`.** Render assigns it (default `10000`) and **fails the
> deploy** if it cannot detect the bound port, so the `Dockerfile` starts Uvicorn
> on `${PORT:-8000}` — 8000 remains the default for local Docker and compose.

### Free-tier caveats (worth knowing for the demo)

- The free Render instance **sleeps when idle**, so the first load can take
  30–60 s.
- The **Gemini free tier has rate limits** and may use submitted data to improve
  Google products — **don't speak anything private** in a demo.
- Free databases can **pause** when idle; wake them before sharing a link.

---

## 11. Testing plan (manual checks)

| Area | What to check |
|---|---|
| CRUD API | create, list, read, update, delete, not-found, invalid input |
| Pagination & search | correct counts and filtering |
| Audio pipeline | correct sample rate/format sent to Gemini |
| Language mixing | the phrases in section 9, re-recorded live |
| Reliability | network drop, session expiry, quota error, mic denied |
| Browsers | Chrome and Edge on desktop, Chrome on Android |
| Deployed app | all of the above on the live HTTPS link |

**Verified on this machine:** all 21 tests pass; the WebSocket pipeline saved
real transcripts end to end against **both** local PostgreSQL and **Neon**; CRUD
was exercised against Neon over REST (`201/200/200/204/404`); the Docker image
builds and honours `$PORT` (verified by running it with `PORT=9000`); the
dashboard renders with no console errors; search, pagination and the view/edit
dialogs work (checked in Chromium).

---

## 12. Security & reliability

- Secrets live only in environment variables; `.env` is git-ignored and
  `.env.example` holds placeholders.
- The Gemini key is used **server-side only**.
- Input is validated and size-limited (`422` on empty/oversized text).
- `MAX_CONCURRENT_SESSIONS` and `MAX_SESSION_SECONDS` protect the free quota.
- The dashboard uses `textContent` for all user data, so transcripts **cannot
  inject scripts**.
- Gemini session expiry / `go_away` triggers an automatic reconnect that keeps
  the transcript already collected; quota errors show a clear message.

---

## 13. Known limitations

- Streaming transcripts can **change slightly** as more audio arrives; partial
  text may be corrected.
- **Script for short Hindi fragments is not fully deterministic** (see section 9).
- Accuracy drops with noise, accents and rapid code-switching.
- Free API/hosting limits cap session length and concurrent users.
- **No user accounts** — every visitor sees all saved transcriptions.
- Single server process (the session counter is per-process).

**Future improvements:** Alembic migrations, authentication, per-IP rate
limiting, a self-hosted `faster-whisper` provider behind the same interface for
vendor independence, and a background queue for saving.

---

## 14. Project layout

```
.
├── app/
│   ├── main.py                 # FastAPI app, /health, static serving
│   ├── config.py               # settings from env
│   ├── database.py             # two engines (pooled queries, direct DDL), URL normalisation
│   ├── models.py               # transcriptions table
│   ├── schemas.py              # pydantic models
│   ├── crud.py                 # DB operations
│   ├── routers/
│   │   ├── transcriptions.py   # REST CRUD
│   │   └── ws.py               # WebSocket bridge
│   └── providers/
│       └── gemini_live.py      # Gemini Live session manager
├── static/
│   ├── index.html
│   ├── styles.css
│   ├── app.js                  # mic capture, live view, dashboard
│   └── pcm-worklet.js          # 16 kHz mono 16-bit PCM AudioWorklet
├── scripts/
│   ├── gemini_live_test.py     # standalone Gemini smoke test
│   ├── ws_smoke.py             # end-to-end WebSocket test
│   └── language_test.py        # Hinglish accuracy matrix
├── tests/                      # pytest API + database-URL tests
├── Dockerfile                  # binds $PORT, falls back to 8000
├── docker-compose.yml          # local app + PostgreSQL 16
├── requirements.txt / requirements-dev.txt
├── render.yaml                 # Render deploy blueprint
├── neon.ts                     # Neon config-as-code policy (Postgres only)
├── package.json                # dependencies for neon.ts (@neon/config, @neon/env)
├── .neon                       # linked Neon org / project / branch (git-ignored)
└── .env.example
```

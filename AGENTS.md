# AGENTS.md — NakaVid

Authoritative for **stack, layout, architecture, code conventions, domain language, testing, and database/migration rules** in this repo. Read it at the start of every task. If a Paperclip charter conflicts with this file, **this file wins.**

NakaVid is the self-hosted, privacy-respecting video CMS: ingest, split, organise, tag, score, and easy manual combine. It is **internal-first, single-user in v1, LAN-only**. The AI montage engine is a **separate product (Nebla)** — not built here. NakaVid's job ends at a scored, tagged, browsable clip library plus manual combine; Nebla consumes that clip pool.

## Read first (before exploring)

Before grepping the codebase, read `.index/repo-map.md` — the machine-generated current map of the repo (present once the codebase indexer targets NakaVid). Locate things there; only read source for what the map doesn't cover. This file is the durable conventions; the repo map is the always-current structure.

## Stack

- **Web + backend: Django** (single language for the server). Django serves the CMS pages and JSON endpoints, owns the ORM, migrations, local session auth, and the admin.
- **DB: self-hosted PostgreSQL** (local to the LAN — *not* hosted Supabase). Django ORM; Django migrations.
- **Interactivity: React islands** (Vite build, TypeScript), mounted only on the genuinely-interactive surfaces (lesson-view timeline/scrub, drag-combine). **Not** a full SPA — everything else is Django templates + the admin. If a surface is CRUD, a form, or a status list, it's Django, not React.
- **Pipeline worker: Python**, running as a Django management command that **shares the Django ORM models**. It does ffprobe / OpenCV / librosa / ffmpeg. It talks to the web app only through job rows in Postgres.
- **Streaming: Caddy** (reverse proxy) range-serves video bytes from disk behind a Django auth check (X-Accel-Redirect–style internal redirect). App authorises; proxy serves bytes — never stream video through Django view code.
- **Storage: local filesystem** on the storage box. No cloud object store, no CDN.

## Privacy — hard rule (non-negotiable)

Footage — full videos, clips, or frames — **never leaves the LAN.** No code path may upload video, clips, or frames to any third-party cloud. This is the product, not a preference.

- Enforce **structurally**, not by convention: the app and worker boxes have no outbound egress to media/cloud endpoints at the network layer, and the Caddyfile encodes that posture. Code rules are the backstop, not the guarantee.
- The **only** ever-permitted external call is a future, opt-in, founder-signed-off Claude Vision *thumbnail-still* tagging step (a still, never footage). **Out of scope now** — until it ships with explicit sign-off, there are **no external image/media calls at all.**
- No analytics, telemetry, or error reporting that transmits frames, content-revealing filenames, or clip data off-box.

## Project layout

```
manage.py
config/                  # Django project: settings, urls, wsgi, asgi
apps/
  library/               # videos, clips, tags — models, views, admin, templates
  pipeline/              # jobs, scoring params, the worker management command
  accounts/              # thin: local session auth wiring
templates/               # Django templates (CMS pages)
static/                  # collected static + built island bundles
frontend/                # Vite + React islands (TS)
  src/islands/           # timeline-scrub/, drag-combine/  (mount points only)
tests/                   # pytest-django
scripts/                 # ops helpers (non-migration)
.index/                  # repo-map (indexer output, when enabled)
Caddyfile                # reverse proxy: auth-gated range streaming + egress posture
pyproject.toml           # deps (uv), ruff + black config
```

Do not touch `backups/`.

## Architecture

- **Django templates + the admin do the boring half.** Clips browser, tag manager, ingest form, queue status → templates. Raw CRUD over videos/tags/jobs/lookup rows → Django Admin (internal-only, safe to lean on). Build no React for these.
- **React islands do the interactive half, and only that.** The lesson-view timeline/scrub and the drag-combine builder mount as islands on their Django pages (root div + data props → Vite bundle). Keep the JS surface minimal.
- **The worker owns the pipeline; Django owns the CMS.** They meet only at the DB job queue and the storage path convention. Django never runs long ffmpeg jobs in a request — it **enqueues** a job row; the worker (a management command) claims it with `SELECT … FOR UPDATE SKIP LOCKED`, runs it, writes status back. (Django-Q/Celery is the scale-up path; the Postgres-queue + management-command worker is the v1 default — lowest infra on one box.)
- **Streaming is a proxy handoff.** The video route checks auth in Django, then returns an internal-redirect header pointing Caddy at the on-disk file; Caddy serves it with HTTP range support. No large-file bytes flow through Django.
- **Storage path convention** (filename encodes its parent, so the DB link is recoverable from disk alone):
  ```
  /nakavid/originals/{year}/{month}/{date}_{class}_{theme}/{filename}
  /nakavid/highlights/{year}/{month}/{date}_{class}_{theme}/{stem}__clip_{NNN}.mp4 (+ .jpg)
  /nakavid/combines/{title}_{date}.mp4
  ```

## Code conventions

Two convention sets, by side of the seam:

**Python (Django) — the majority of the repo**
- Type hints on function signatures. Format with **black**, lint with **ruff**; keep them green.
- Idiomatic Django: fat models / thin views, `select_related`/`prefetch_related` to avoid N+1, `login_required` (or middleware) on every video/clip view — **auth is never in a template**, it's in the view/middleware layer.
- Validate external input at the boundary (Django forms / DRF serializers if used).
- Minimal, focused changes — only the files the task needs. No new dependencies unless necessary (`uv`, latest stable).

**TypeScript (React islands) — the thin interactive layer**
- Strict TS (no `any`, no `!`). Named exports, kebab-case filenames, `cn()` + `cva`, the org frontend-design conventions. These are shared with the Class-Breeze/GTM front-end work, so they stay parallel.

## Domain language (use these exact terms in code, UI, and docs)

- **Video** — a source file. **Type A** = long recording (40–90 min, landscape; archival + extraction). **Type B** = in-the-moment clip (<5 min, mixed orientation; already usable).
- **Clip** — a trimmed segment with start/end timestamps. Type A clips are pipeline-extracted; a Type B video is itself a clip (start = 0).
- **Highlight score** — 0–100 from face count, smile, motion, audio energy. Drives sorting; consumed downstream by Nebla for montage selection.
- **Energy curve** — per-window scores stored on a clip (not just the headline number). NakaVid **computes and stores** it; **Nebla consumes** it. Preserve it — it is the handoff to the montage product.
- **Tag** — controlled-vocab or free label on videos/clips.
- **Combine** — a *manual*, user-assembled concatenation of clips. NakaVid's deterministic assembly. This is **not** the montage engine — beat maps, EDLs, the AI Planner, arcs, and locks are **Nebla's**, not here.

*(This section is the domain source of truth until/unless split into `CONTEXT.md`.)*

## Testing

- Cover everything **deterministic**: ffprobe parse, window-slicing, trim/encode, thumbnail generation, storage-path encode/decode, clip-row insertion, the job-claim (SKIP LOCKED) logic, the stream route's auth/redirect handoff.
- **pytest + pytest-django.** Run the narrowest covering set: `pytest tests/<path> -k <pattern>`. Add tests for new behaviour. Keep coverage **≥ 70%**.
- **CI-green is the merge gate.** Red tests = not done.

## Database / migrations (follow exactly — the classic footguns)

- Schema change → edit models → `python manage.py makemigrations` → **commit the generated migration file(s)**. Never hand-edit a generated migration.
- One-off data fixes are **idempotent management commands**, not migrations.
- **Lookup vs choices:** user-extensible vocabularies (tag categories, and anything a user might add without a deploy) are **lookup tables** (FK to a model), matching the org-wide "no brittle enums" rule. Fixed internal state machines (job status: `pending·processing·done·error`) may use Django `TextChoices` — idiomatic and not user-extended. (Judgment call; flag if you'd rather all-lookup for cross-repo symmetry.)
- Design the schema **multi-user-ready** even though v1 is single-user, and carry a **per-video privacy flag** field even though consent tracking is out of scope for v1.

## The scoring boundary (heightened care)

The pipeline splits into two kinds of work that ship differently:

- **Deterministic harness — autonomous.** ffprobe probe, window slicing, ffmpeg trim/encode, thumbnails, clip-row insertion, storage paths, job-claim, the stream handoff. Correct answers exist; build, test hard, normal PR.
- **Scoring quality — gated.** Signal weights and thresholds (face/smile/motion/audio weights, window size, step, min clip length, min gap, peak count) are **tunable parameters with sensible defaults, never hard-coded magic numbers.** Whether the extracted clips are *genuinely good highlights* is the **founder's call on real footage** — not the agent's. Scoring-tuning issues ship as a **draft PR + founder handoff** (labelled `scoring`), not the auto "ready-to-merge" path, even on green CI. v1 scoring uses **no LLM** — OpenCV/librosa heuristics only.

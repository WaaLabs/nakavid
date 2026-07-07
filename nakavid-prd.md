# PRD: NakaVid

**Product:** Self-hosted, privacy-respecting video CMS — ingest, split, organise, tag, AI highlight extraction, and easy manual combine.
**Owner:** Matt (WaaLabs)
**Status:** Approved — build starting (Phase 1)
**Last updated:** 2026-07-07
**Relationship to Nebla:** NakaVid produces a **scored, tagged clip library** and does deterministic manual combine. **Nebla** (separate product) consumes that clip pool to make AI, beat-synced montages. The boundary is the clip library: everything up to it is NakaVid; the montage engine is not built here.

*(Stack, conventions, and schema mechanics are authoritative in the repo's `AGENTS.md`, not duplicated here. This PRD is the product spec.)*

---

## 1. Problem statement

Skippy (and any event-running org) accumulates two kinds of footage: long recordings (40+ min lessons, keynotes, matches, live streams) and short in-the-moment clips (games, crowd reactions, phone b-roll). There is no system to organise it by class/date/theme, extract the best moments, link clips to their source, or assemble something quickly. Good footage rots; institutional memory is lost. NakaVid is the durable library and the extraction/organise layer that fixes this — on-premises, so sensitive footage never leaves the building.

## 2. Goals

- Store all footage in a structured, durable, browsable library.
- Automatically extract scored, tagged highlight clips from long recordings.
- Link every clip back to its source with exact timestamps.
- Compute and store a **per-window energy curve** per clip (the handoff Nebla needs).
- Let a human quickly do a **manual combine** (deterministic concatenation) from a filtered clip set.
- Keep all footage and metadata **on the LAN** — a hard rule, not a positioning nicety.

## 3. Non-goals (v1)

- **The AI montage engine** — beat maps, EDLs, the Planner, energy arcs. That is Nebla.
- Multi-tenant SaaS (single-org v1; schema multi-user-ready).
- Attendance or grade tracking (Class-Breeze owns this).
- Public-facing parent/customer portal.
- Live streaming / real-time recording.
- Generative video (this edits real footage).
- Mobile app (browser-first).

## 4. Users

**Primary:** Matt — uploads footage, reviews clips, tags, does manual combines.
**Secondary (future):** Kasumi / staff — review and flag clips.
Single-user v1; schema designed for multi-user from the start.

## 5. Source video types

The two-type model generalises beyond the classroom — "long-form recordings" vs "in-the-moment capture," which holds for events, sports, seminars equally.

**Type A — Long recordings.** 40–90+ min, landscape. Lesson recording, keynote, match, full stream. Value: archival + highlight extraction. Not shareable directly. **Processing:** AI scanning to find the best 30–90s moments (energy, faces, motion, audio peaks); these feed the clip library.

**Type B — In-the-moment clips.** <5 min, mixed orientation. Phone clip, crowd reaction, b-roll. Value: immediately usable; combine raw material. Already clipped, so they skip AI extraction and go straight to the library — but still get scored (for later montage ranking in Nebla).

## 6. Core concepts

**Video** — a source file (Type A or B). **Clip** — a trimmed segment with start/end timestamps; Type A clips are AI-extracted, a Type B file is itself a clip. **Highlight score** — 0–100 from face count, smile, motion, audio energy; drives sorting. **Energy curve** — per-window scores stored on a clip; NakaVid computes it, Nebla consumes it. **Tag** — controlled-vocab or free label. **Combine** — a manual, user-assembled concatenation (deterministic; *not* a montage).

## 7. Feature specification

### 7.1 Ingest
Drag-drop upload, metadata form (class/date/theme/type), structured storage path (encodes the parent so the DB link is recoverable from disk), batch ingest. Large uploads are chunked/resumable client-side; bytes land on the storage box, never a cloud bucket.

### 7.2 AI extraction pipeline (ingest-time, Python worker)
Stages: **Probe** (ffprobe) → **Segment scoring** (Type A: slide a ~3s window at ~1s steps; score face count, smile, motion energy, audio RMS, silence penalty; smooth; find peaks) → **Clip selection** (top non-overlapping peaks, expand ±3s, snap to low-motion cut points) → **Export** (ffmpeg trim + thumbnail) → **Tagging** (inherit parent tags; optional content classifier later).

Scoring also stores a **per-window energy curve per clip**, not just a headline score — this is the artifact Nebla's montage planner needs. Signal weights and thresholds are **tunable parameters with defaults**, never hard-coded; whether the clips are genuinely good highlights is the founder's call on real footage (see AGENTS.md scoring boundary). v1 scoring is heuristic (OpenCV/librosa) — **no LLM**.

### 7.3 Data model (conceptual — ORM + migrations per AGENTS.md)
- **videos** — source files: type (A/B), orientation, class/date/theme, storage path, per-video privacy flag, timestamps.
- **clips** — trimmed segments: FK to source video, start/end, highlight score, **energy_curve (per-window scores)**, thumbnail path, storage path.
- **tags** — slug/label/category; M2M to videos and clips. User-extensible categories are lookup rows.
- **jobs** — the processing queue: type (probe/score/extract), state (`pending·processing·done·error`), stderr on error, timestamps; claimed by the worker via SKIP LOCKED.
- **combines** — a manual assembly: title, ordered clip list, output path, status.

### 7.4 UI views
- **Clips Browser** — grid of clips; filter by tag/class/date/score; inline play. (Django + light interactivity.)
- **Lesson View** — a timeline strip of clips extracted from one Type A recording, scrubbable against the source. **(React island.)**
- **Activity Clips View** — Type B clips grouped by event/tag.
- **Tag Manager** — CRUD over the tag vocabulary. (Largely Django Admin.)
- **Combine Builder** — drag clips into an order, preview, export a concatenation. **(React island.)** Deterministic only — no beat-sync, no AI.
- **Queue status** — pending/processing/done/error per job, with stderr and a re-queue button. (Django template, light polling.)

### 7.5 Processing & render queue
All background jobs (probe, score, extract, thumbnail, combine export) run on the Python worker off the Postgres job queue. Status surfaced in the queue view; errors show stderr; re-queue button.

## 8. Architecture

Stack is authoritative in `AGENTS.md`. In one line for this doc: **Django** (CMS pages, ORM, admin, local auth) + **React islands** on the two interactive surfaces + **Postgres** (metadata + job queue) + a **Python worker** sharing Django models + **Caddy** range-streaming video behind an auth check. On-prem, LAN-only, single box family (storage + processing).

## 9. Privacy & safety

Footage stays on the LAN — full videos, clips, and frames alike; no third-party cloud path exists, enforced at the network/proxy layer, not just in code. Auth required on every video/clip route. Per-video privacy flag designed into the schema for later consent tracking. The single future external exception (opt-in Claude Vision thumbnail-still tagging) is out of scope until explicit founder sign-off. (Full rule in AGENTS.md.)

## 10. Build phases

**Phase 1 — Foundation.** Schema, storage structure, ingest UI, clips browser, playback via the Caddy stream handoff, local auth. **Value:** an organised, browsable library even before AI.

**Phase 2 — AI extraction pipeline.** Python worker: probe, scoring (face/motion/audio), **per-window energy curves**, clip extraction, thumbnails, the job queue, the Lesson View timeline island. **Value:** long recordings auto-yield a scored clip library.

**Phase 3 — Organise & combine.** Tag manager, bulk tagging, the Combine Builder island (manual, deterministic). **Value:** a fast path from library to a usable concatenation without leaving NakaVid.

**Phase 4 — Polish & reach.** Optional content classifier for auto-tagging, opt-in Claude Vision thumbnail tagging (with sign-off), proxy generation, multi-user. **Value:** less manual tagging; more than one operator.

*(The montage engine that used to sit here is Nebla's product — gated behind its own Planner-quality validation. Not in NakaVid's phases.)*

## 11. Open questions

| # | Question | Notes |
|---|---|---|
| 1 | Face/smile detection: OpenCV Haar cascades (CPU, no torch) for v1, or YOLO/torch from the start? | Haar keeps the flake lean and CPU-only; YOLO is better but pulls torch. Depends on whether medina has a GPU. |
| 2 | Job queue: Postgres SKIP LOCKED + management-command worker, or Django-Q/Celery? | Postgres-queue is lowest-infra for one box; revisit if concurrency grows. |
| 3 | Combine preview: client-side stitch of proxies, or a fast worker render? | Affects how tight the combine loop feels. |
| 4 | Chunked/resumable upload library for large Type A files? | Needs to survive a dropped LAN connection mid-upload. |
| 5 | Per-video privacy flag → when does consent tracking actually land? | Schema carries it now; enforcement is a later phase. |

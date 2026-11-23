# Platform architecture

## Phases

1. **Engine** (`engine/`, done): one command from footage to every deliverable, with cached stages and `status.json` progress.
2. **Platform**: client accounts, uploads, job queue, dashboard, downloads.
3. **Editor**: review screen with a transcript-based cut tool (delete words → removed from the video), angle picker, shorts approval/swap, brand kits.
4. **Business**: teams, per-client brand kits, usage limits, billing (Stripe).

## Phase 2 design

```
Browser (Next.js on Netlify)
  │  auth, project setup, resumable uploads (TUS) straight to storage
  ▼
Supabase
  ├─ Auth (email / Google)
  ├─ Postgres: organizations, members, brand_kits, projects, cameras, files, jobs, deliverables
  ├─ Storage: footage bucket (resumable, large files), deliverables bucket (signed download URLs)
  └─ Realtime: job progress pushed to the dashboard
  ▲
  │  poll `jobs` (status = queued) / write progress + deliverables
Worker (Docker image from engine/, GPU cloud e.g. Modal or RunPod)
  1. download footage for the job
  2. `studio run project.yaml` (config generated from the DB rows)
  3. stream status.json → jobs.progress
  4. upload outputs → deliverables bucket, insert deliverables rows
```

### Tables (first cut)
- `organizations(id, name, plan)`, `members(org_id, user_id, role)`
- `brand_kits(id, org_id, show_name, show_subtitle, accent, alert, fonts, logo_path)`
- `projects(id, org_id, brand_kit_id, name, episode_label, guest_line, guest_subline, angles jsonb, removals jsonb, outputs jsonb, status)`
- `people(id, project_id, name, role)`
- `cameras(id, project_id, label, master_audio, order jsonb, frontal jsonb)`
- `files(id, camera_id, storage_path, bytes, duration, position)`
- `jobs(id, project_id, kind['full'|'preview'|'redo'], params jsonb, status, progress, log jsonb, worker, started_at, finished_at)`
- `deliverables(id, project_id, kind, storage_path, bytes, meta jsonb)`

Row-level security: every table keyed to `org_id` through `members`.

### Upload flow
Large 4K files (5–15 GB) go browser → Supabase Storage via TUS resumable uploads (no app server in the path).
After upload the client sets each camera's people order using the `inspect` frame: the worker runs `studio inspect` and the UI shows numbered faces to label.

### Cost model (per 80-minute episode, estimate)
- GPU worker: about 20–40 min of decode, tracking and transcription, roughly $1–4.
- Claude API: three structured calls (shorts, trailer, post captions), a few cents.
- Storage: the source footage dominates, so set footage to expire about 30 days after delivery.

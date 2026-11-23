# Video Editor: notes for contributors

AI multicam podcast editor. `engine/` is the Python package (`studio` CLI) that turns footage into
deliverables; `docs/ARCHITECTURE.md` describes the web platform built around it.

## Engine conventions
- The **master timeline** is the master-audio camera's clock, in seconds; frames are at 30 fps (`media.FPS`).
  Everything (sync, speakers at 10 Hz, EDL frames `k0/k1`) is expressed on it. Removals produce `keep`
  ranges that map master time to the final timeline (`deliver.map_time`).
- Every stage caches to `work_dir`; `pipeline.invalidate(proj, stage)` / `studio redo` clears from a stage on.
  Keep stage outputs deterministic so cached and fresh runs agree.
- Rendering must stay **frame-accurate**: segments are encoded with identical x264 settings
  (`render.ENC`) so they can be stream-copied together, and audio is cut by sample (`SR // FPS` per frame).
  Never mix encoder settings within one concat.
- Framing rules are product requirements from the client, not preferences:
  a close-up never shows part of another head (`framing._bounds`), no more than ~1.75x upscale
  landscape / ~2.1x vertical, cuts land between words, shorts are one continuous take (no jump cuts).
- Claude calls live only in `llm.py`, use structured outputs, `fallbacks="default"`, and always have a
  heuristic fallback so the pipeline runs without credentials (`STUDIO_NO_LLM=1` forces fallbacks).
- Fonts are bundled in `engine/studio/fonts` (SIL OFL); do not depend on system fonts.

## Testing
Use `studio preview project.yaml --start S --end E` on a 2–3 minute window before full runs; full 4K
episodes take hours on CPU. `examples/episode3.yaml` is the reference project.

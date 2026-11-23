# Video Editor: AI multicam podcast studio

Turn raw multicam podcast footage into a finished, professionally edited episode and a full
social package with one command (and, soon, one upload).

Built from the workflow used to produce **The True Village of Pro Athletes, Episode 3**.

## What it produces

| Deliverable | Details |
|---|---|
| **Full episode** (1080p MP4) | Every camera synced to the master audio (drift-corrected); automatic multicam cutting that follows who is talking; framing rules (a close-up never shows half of another person's head); removals by quoting what was said; camera color matching; guests leveled to the host; -16 LUFS |
| **Clean-joining parts** | ~5-minute `.mov` parts that start on camera changes, with sample-exact PCM audio, so they rejoin in CapCut/Premiere with no gaps |
| **Trailer / cold open** | 8-9 lines in a tension → advice → emotion arc, animated keywords (bold sans + italic serif + accent color), push-ins, branded title card, original generated cinematic score that ducks under the voices |
| **Vertical shorts** | AI-picked moments, one continuous take each, speaker-following 9:16 framing, headline box + clean captions (Mel Robbins style), -14 LUFS |
| **Transcript + SRT + post captions** | On the final timeline (removals applied), speaker labeled |

## Quick start (engine)

Requirements: Python 3.10+, ffmpeg. Optional: `ANTHROPIC_API_KEY` for the AI editorial steps
(picking shorts, trailer lines, post captions). Without it the engine uses heuristic fallbacks.

```bash
cd engine
pip install -e .
```

1. Copy `examples/episode3.yaml`, point `files` at your footage, and set the people and brand.
2. Map who is where in each camera:

```bash
studio inspect my_episode.yaml
```

   This writes `out/.../inspect_<camera>.jpg` with faces numbered left to right. Fill in `order`
   and `frontal` for each camera.
3. Check sync, framing and style on a two-minute window first:

```bash
studio preview my_episode.yaml --start 600 --end 720
```

4. Run everything:

```bash
studio run my_episode.yaml
```

Other commands:
- `studio find my_episode.yaml "pop up"` finds where a phrase is said, to set up a removal.
- `studio redo my_episode.yaml edit` re-runs from the edit stage onward, for example after changing angles or removals.

## How the edit works

The pipeline runs these stages, each one cached:
1. **sync**: GCC-PHAT on the audio, plus drift measured every 2 minutes.
2. **transcribe**: faster-whisper with word timestamps.
3. **faces**: MediaPipe detection and lip landmarks.
4. **speakers**: lips, voice and words combined with a Viterbi smoother.
5. **edit**: the edit decision list (EDL) of cuts, with removals applied.
6. **color**: color match between cameras.
7. **audio**: leveling and loudness.
8. **episode / parts / transcript / trailer / shorts / post_captions**: the deliverables.

Shot grammar for `edit.angles`:
- `A:wide` is the full frame of camera A.
- `A:single:al` is a tight shot of one person.
- `A:two:shay+al` is two adjacent people.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the platform plan: web app, uploads, workers and billing.

"""Claude-powered editorial decisions: which moments become shorts, which lines build the trailer,
and the social post copy. Every function has a heuristic fallback so the pipeline still runs
without API credentials (set ANTHROPIC_API_KEY, or log in with `ant auth login`)."""
from __future__ import annotations

import json
import os

import numpy as np

from .config import Project

SYSTEM = ("You are a senior podcast producer and short-form video editor. You pick moments that hook viewers in the "
          "first two seconds, stand on their own without context, and end on a satisfying line. You never invent "
          "quotes or facts: every line you cite must exist in the transcript.")


def _client():
    try:
        import anthropic
        return anthropic.Anthropic()
    except Exception:
        return None


def _ask(proj: Project, prompt: str, schema: dict, max_tokens: int = 16000) -> dict | None:
    """One structured-output request. Returns parsed JSON or None (no credentials / refusal / error)."""
    if os.environ.get("STUDIO_NO_LLM") == "1":
        return None
    client = _client()
    if client is None:
        return None
    import anthropic
    try:
        resp = client.beta.messages.create(
            model=proj.llm_model,
            max_tokens=max_tokens,
            system=SYSTEM,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",                       # re-run on a fallback model if a safety classifier declines
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
    except (anthropic.AuthenticationError, TypeError):      # TypeError: no credentials configured at all
        print("llm: no Anthropic credentials - using heuristic fallback")
        return None
    except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
        print(f"llm: request failed ({type(e).__name__}); using heuristic fallback")
        return None
    if resp.stop_reason == "refusal":
        return None
    text = next((b.text for b in resp.content if b.type == "text"), None)
    return json.loads(text) if text else None


def transcript_lines(proj: Project, segs, path, rate: int = 10) -> str:
    P = len(proj.people); out = []
    for s in segs:
        i0, i1 = int(s["start"] * rate), max(int(s["end"] * rate), int(s["start"] * rate) + 1)
        lab = path[i0:i1]; lab = lab[lab < P]
        who = proj.people[int(np.bincount(lab).argmax())].name if len(lab) else "?"
        out.append(f"[{s['start']:.2f}-{s['end']:.2f}] {who}: {s['text']}")
    return "\n".join(out)


def _str(desc): return {"type": "string", "description": desc}
def _num(desc): return {"type": "number", "description": desc}


def pick_shorts(proj: Project, lines: str, n: int, max_s: float, avoid: list[tuple[float, float]]) -> list[dict] | None:
    schema = {"type": "object", "additionalProperties": False, "required": ["clips"], "properties": {"clips": {
        "type": "array", "items": {"type": "object", "additionalProperties": False,
                                   "required": ["headline", "start", "end", "why"],
                                   "properties": {"headline": _str("on-screen title, max 7 words, title case, true to the clip"),
                                                  "start": _num("start time in seconds: the start of the first sentence"),
                                                  "end": _num("end time in seconds: the end of the last sentence"),
                                                  "why": _str("one sentence on why it will perform")}}}}}
    b = proj.brand
    prompt = (f"Show: {b.show_name}. Guests: {b.guest_line} ({b.guest_subline}).\n"
              f"Pick the {n} best moments for vertical shorts. Each is ONE continuous stretch (no jump cuts), "
              f"35-{int(max_s)} seconds, self-contained, starting on a strong first sentence and ending on a complete thought. "
              f"No overlaps. Prioritize contrarian or eye-opening advice, emotional moments, then relatable or funny ones; "
              f"vary the topics. Never use these ranges (removed from the episode): {avoid}.\n"
              f"Transcript with timestamps in seconds:\n{lines}")
    r = _ask(proj, prompt, schema)
    return r["clips"] if r else None


def pick_trailer(proj: Project, lines: str, avoid: list[tuple[float, float]]) -> list[dict] | None:
    item = {"type": "object", "additionalProperties": False,
            "required": ["start", "end", "lead", "serif", "big"],
            "properties": {"start": _num("start of the line in seconds"), "end": _num("end of the line in seconds"),
                           "lead": _str("2-5 words in small caps that set up the keyword, taken from the line"),
                           "serif": _str("2-6 words in elegant italics from the line, or empty"),
                           "big": _str("the 1-2 word punchline keyword from the line, in caps")}}
    schema = {"type": "object", "additionalProperties": False, "required": ["bites"],
              "properties": {"bites": {"type": "array", "items": item}}}
    prompt = (f"Build a 35-40 second cinematic cold-open trailer for this episode of {proj.brand.show_name} "
              f"(in the style of Diary of a CEO / Mel Robbins trailers). Choose 8-9 short lines (2.5-5.5 s each) and ORDER them "
              f"as an arc: tension/hot take -> advice -> emotional payoff -> one closing line. Each line must be a complete "
              f"phrase spoken by one person. For each line give the on-screen keyword treatment. Avoid ranges {avoid}.\n"
              f"Transcript:\n{lines}")
    r = _ask(proj, prompt, schema)
    return r["bites"] if r else None


def post_captions(proj: Project, clips: list[dict]) -> list[str] | None:
    schema = {"type": "object", "additionalProperties": False, "required": ["captions"],
              "properties": {"captions": {"type": "array", "items": _str("post caption")}}}
    desc = "\n".join(f"{i+1}. {c['headline']}: {c['text'][:1200]}" for i, c in enumerate(clips))
    prompt = (f"Write a professional social caption for each clip below, for {proj.brand.show_name} "
              f"(guests: {proj.brand.guest_line}, {proj.brand.guest_subline}). Format per caption: a strong lead line "
              f"(a quote from the clip when one fits), one or two sentences of context in third person, then "
              f"'Watch the full conversation: [link]', then 4 relevant hashtags including the show's. No emojis. "
              f"Return captions in the same order.\n{desc}")
    r = _ask(proj, prompt, schema)
    return r["captions"] if r else None

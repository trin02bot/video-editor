"""Project configuration (YAML) for one episode.

A project describes the people on camera, the cameras (which one carries the
master audio, who appears left-to-right in each, who faces each camera), the
edit rules (allowed angles, removals) and which deliverables to produce.
See examples/episode.yaml for a complete, commented example.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Person:
    id: str
    name: str
    role: str = "guest"          # "host" or "guest"


@dataclass
class Camera:
    id: str
    files: list[str]                       # one recording, possibly split into chunks (in order)
    master_audio: bool = False             # this camera's audio is the episode soundtrack
    order: list[str] = field(default_factory=list)    # person ids as they appear left -> right
    frontal: list[str] = field(default_factory=list)  # person ids facing this camera (usable for singles / lip activity)


@dataclass
class Brand:
    show_name: str = "My Show"
    show_subtitle: str = ""
    episode_label: str = ""
    guest_line: str = ""                   # e.g. "Al & Shay Finley"
    guest_subline: str = ""                # e.g. "Parents of AJ Finley"
    accent: str = "#6EC1FF"
    alert: str = "#FF4D4D"


@dataclass
class Removal:
    start: float | None = None             # master-audio seconds
    end: float | None = None
    phrase_start: str | None = None        # or: cut from the first word of this phrase...
    phrase_end: str | None = None          # ...through the last word of this phrase
    note: str = ""


@dataclass
class EditRules:
    # Allowed shot types. Grammar:
    #   "<cam>:wide"                 full frame of a camera
    #   "<cam>:single:<person>"      tight shot of one person (only other heads fully excluded)
    #   "<cam>:two:<p1>+<p2>"        two adjacent people, both heads fully in frame
    angles: list[str] = field(default_factory=list)
    min_shot: float = 2.2
    max_shot: float = 12.0
    removals: list[Removal] = field(default_factory=list)


@dataclass
class AudioRules:
    level_guests: bool = True              # raise quieter speakers to match the host
    loudness_lufs: float = -16.0           # podcast standard (-14 for social)
    true_peak: float = -1.5


@dataclass
class Outputs:
    full_episode: bool = True
    parts_max_seconds: float = 300.0       # 0 disables; parts cut on camera changes, PCM audio, sample exact
    trailer: bool = True
    trailer_music: bool = True
    shorts: int = 10
    shorts_max_seconds: float = 85.0
    transcript: bool = True
    srt: bool = True
    post_captions: bool = True


@dataclass
class Project:
    name: str
    work_dir: Path
    out_dir: Path
    people: list[Person]
    cameras: list[Camera]
    brand: Brand = field(default_factory=Brand)
    edit: EditRules = field(default_factory=EditRules)
    audio: AudioRules = field(default_factory=AudioRules)
    outputs: Outputs = field(default_factory=Outputs)
    limit: tuple[float, float] | None = None   # process only this master-time window (previews)
    whisper_model: str = "medium.en"
    llm_model: str = "claude-opus-5-5"

    # ---- helpers
    @property
    def master(self) -> Camera:
        return next(c for c in self.cameras if c.master_audio)

    def person(self, pid: str) -> Person:
        return next(p for p in self.people if p.id == pid)

    @property
    def host_ids(self) -> list[str]:
        return [p.id for p in self.people if p.role == "host"]

    def cam(self, cid: str) -> Camera:
        return next(c for c in self.cameras if c.id == cid)

    def default_angles(self) -> list[str]:
        """Every camera's wide + a single of each frontal person + adjacent two-shots."""
        out = []
        for c in self.cameras:
            out.append(f"{c.id}:wide")
            for p in c.frontal:
                out.append(f"{c.id}:single:{p}")
            fr = [p for p in c.order if p in c.frontal]
            for a, b in zip(fr, fr[1:]):
                out.append(f"{c.id}:two:{a}+{b}")
        return out


def _resolve(base: Path, p: str) -> str:
    q = Path(os.path.expanduser(p))
    return str(q if q.is_absolute() else (base / q).resolve())


def load(path: str | Path) -> Project:
    path = Path(path)
    d = yaml.safe_load(path.read_text())
    base = path.parent
    people = [Person(**p) for p in d["people"]]
    cams = []
    for c in d["cameras"]:
        c = dict(c)
        c["files"] = [_resolve(base, f) for f in c["files"]]
        cams.append(Camera(**c))
    if sum(c.master_audio for c in cams) != 1:
        raise ValueError("exactly one camera must have master_audio: true")
    ids = {p.id for p in people}
    for c in cams:
        bad = (set(c.order) | set(c.frontal)) - ids
        if bad:
            raise ValueError(f"camera {c.id} references unknown people: {sorted(bad)}")
    edit = d.get("edit", {}) or {}
    rem = [Removal(**r) for r in edit.pop("removals", []) or []]
    proj = Project(
        name=d.get("name", path.stem),
        work_dir=Path(_resolve(base, d.get("work_dir", f"work/{path.stem}"))),
        out_dir=Path(_resolve(base, d.get("out_dir", f"out/{path.stem}"))),
        people=people,
        cameras=cams,
        brand=Brand(**(d.get("brand") or {})),
        edit=EditRules(removals=rem, **edit),
        audio=AudioRules(**(d.get("audio") or {})),
        outputs=Outputs(**(d.get("outputs") or {})),
        limit=tuple(d["limit"]) if d.get("limit") else None,
        whisper_model=d.get("whisper_model", "medium.en"),
        llm_model=d.get("llm_model", "claude-opus-5-5"),
    )
    if not proj.edit.angles:
        proj.edit.angles = proj.default_angles()
    proj.work_dir.mkdir(parents=True, exist_ok=True)
    proj.out_dir.mkdir(parents=True, exist_ok=True)
    return proj

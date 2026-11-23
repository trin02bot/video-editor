"use client";
import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useAuth } from "@/components/useAuth";
import type { Camera, Deliverable, Job, Person, Project, Removal } from "@/lib/types";

const KIND_LABEL: Record<string, string> = {
  episode: "Full episode", part: "Full-quality parts", trailer: "Trailer", short: "Shorts",
  transcript: "Transcript", srt: "YouTube captions (.srt)", post_captions: "Post captions", inspect: "Camera check",
};

export default function ProjectPage({ params }: { params: { id: string } }) {
  const uid = useAuth();
  const [p, setP] = useState<Project | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [cams, setCams] = useState<Camera[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [dels, setDels] = useState<Deliverable[]>([]);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState({ start: 10, end: 12 });
  const [saved, setSaved] = useState("");

  async function load() {
    const [a, b, c, d, e] = await Promise.all([
      supabase.from("projects").select("*").eq("id", params.id).single(),
      supabase.from("people").select("*").eq("project_id", params.id),
      supabase.from("cameras").select("*").eq("project_id", params.id).order("label"),
      supabase.from("jobs").select("*").eq("project_id", params.id).order("created_at", { ascending: false }),
      supabase.from("deliverables").select("*").eq("project_id", params.id).order("created_at"),
    ]);
    setP(a.data); setPeople(b.data ?? []); setCams(c.data ?? []); setJobs(d.data ?? []); setDels(e.data ?? []);
  }
  useEffect(() => {
    if (!uid) return;
    load();
    const ch = supabase.channel(`project-${params.id}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "jobs", filter: `project_id=eq.${params.id}` }, load)
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "deliverables", filter: `project_id=eq.${params.id}` }, load)
      .subscribe();
    return () => { supabase.removeChannel(ch); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uid]);

  // signed download links (1 hour)
  useEffect(() => {
    const missing = dels.filter((d) => !urls[d.id]);
    if (!missing.length) return;
    Promise.all(missing.map(async (d) => [d.id, (await supabase.storage.from("deliverables").createSignedUrl(d.storage_path, 3600)).data?.signedUrl ?? ""] as const))
      .then((pairs) => setUrls((u) => ({ ...u, ...Object.fromEntries(pairs) })));
  }, [dels, urls]);

  const angleOptions = useMemo(() => {
    const out: { id: string; label: string }[] = [];
    const nm = (s: string) => people.find((x) => x.slug === s)?.name ?? s;
    for (const c of cams) {
      out.push({ id: `${c.label}:wide`, label: `Camera ${c.label} – wide (everyone in frame)` });
      for (const s of c.frontal) out.push({ id: `${c.label}:single:${s}`, label: `Camera ${c.label} – close-up of ${nm(s)}` });
      const fr = c.person_order.filter((s) => c.frontal.includes(s));
      for (let i = 0; i + 1 < fr.length; i++) out.push({ id: `${c.label}:two:${fr[i]}+${fr[i + 1]}`, label: `Camera ${c.label} – two-shot of ${nm(fr[i])} & ${nm(fr[i + 1])}` });
    }
    return out;
  }, [cams, people]);

  if (!p) return <p className="muted">Loading…</p>;
  const job = jobs[0];
  const inspect = dels.filter((d) => d.kind === "inspect");
  const mapped = cams.length > 0 && cams.every((c) => c.person_order.length > 0);

  async function saveCam(c: Camera, patch: Partial<Camera>) {
    const next = { ...c, ...patch };
    setCams(cams.map((x) => (x.id === c.id ? next : x)));
    await supabase.from("cameras").update({ person_order: next.person_order, frontal: next.frontal }).eq("id", c.id);
  }
  async function saveProject(patch: Partial<Project>) {
    setP({ ...p!, ...patch }); await supabase.from("projects").update(patch).eq("id", p!.id);
    setSaved("Saved"); setTimeout(() => setSaved(""), 1500);
  }
  async function queue(kind: string, params: Record<string, unknown> = {}) {
    await supabase.from("jobs").insert({ project_id: p!.id, kind, params }); load();
  }

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><h1 style={{ marginBottom: 0 }}>{p.name}</h1><div className="muted">{p.episode_label} {p.guest_line && `· ${p.guest_line}`}</div></div>
        <span className="pill">{p.status}</span>
      </div>

      {job && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>{job.kind === "inspect" ? "Detecting faces" : job.kind === "preview" ? "Preview" : "Full edit"}: {job.status}{job.stage ? ` · ${job.stage}` : ""}</strong>
            <span className="muted">{Math.round((job.progress ?? 0) * 100)}%</span>
          </div>
          <div className="bar" style={{ margin: "8px 0" }}><div style={{ width: `${(job.progress ?? 0) * 100}%` }} /></div>
          {job.error && <pre className="log bad">{job.error.split("\n")[0]}</pre>}
          {job.log?.length > 0 && <pre className="log">{job.log.slice(-12).join("\n")}</pre>}
        </div>
      )}

      <h2>1. Who is where</h2>
      <p className="muted">For each camera, match the numbered faces (left to right) to people, and tick who is facing that camera.</p>
      {cams.map((c) => {
        const img = inspect.filter((d) => d.title === `inspect_${c.label}`).slice(-1)[0];
        const slots = Math.max(c.person_order.length, people.length);
        return (
          <div className="card" key={c.id}>
            <strong>Camera {c.label}{c.master_audio ? " (master audio)" : ""}</strong>
            {img && urls[img.id] ? <img className="thumb" src={urls[img.id]} alt={`camera ${c.label}`} style={{ marginTop: 8 }} />
              : <p className="muted">The face check image appears here when processing finishes.</p>}
            <div className="grid">
              {Array.from({ length: slots }).map((_, i) => (
                <div key={i}>
                  <label>Face {i + 1}</label>
                  <select value={c.person_order[i] ?? ""} onChange={(e) => {
                    const order = [...c.person_order]; order[i] = e.target.value;
                    saveCam(c, { person_order: order.filter(Boolean) });
                  }}>
                    <option value="">—</option>
                    {people.map((x) => <option key={x.slug} value={x.slug}>{x.name}</option>)}
                  </select>
                </div>
              ))}
            </div>
            <label>Facing this camera</label>
            <div className="row">
              {people.map((x) => (
                <label key={x.slug} className="row" style={{ margin: 0, color: "var(--text)" }}>
                  <input type="checkbox" style={{ width: "auto" }} checked={c.frontal.includes(x.slug)}
                    onChange={(e) => saveCam(c, { frontal: e.target.checked ? [...c.frontal, x.slug] : c.frontal.filter((s) => s !== x.slug) })} />
                  {x.name}
                </label>
              ))}
            </div>
          </div>
        );
      })}

      <h2>2. Angles to use</h2>
      <div className="card">
        {angleOptions.length === 0 && <p className="muted">Map the cameras first.</p>}
        {angleOptions.map((a) => (
          <label key={a.id} className="row" style={{ margin: "6px 0", color: "var(--text)" }}>
            <input type="checkbox" style={{ width: "auto" }} checked={p.angles.includes(a.id)}
              onChange={(e) => saveProject({ angles: e.target.checked ? [...p.angles, a.id] : p.angles.filter((x) => x !== a.id) })} />
            {a.label}
          </label>
        ))}
        <p className="muted">Leave all unticked to let the studio use every angle.</p>
      </div>

      <h2>3. Cut anything out</h2>
      <div className="card">
        <p className="muted">Quote the first and last words of the part to remove. The cut lands in the natural pauses around them.</p>
        {p.removals.map((r: Removal, i: number) => (
          <div className="row" key={i} style={{ marginBottom: 8 }}>
            <input style={{ flex: 1 }} value={r.phrase_start ?? ""} placeholder="Starts with…" onChange={(e) => setP({ ...p, removals: p.removals.map((x, j) => j === i ? { ...x, phrase_start: e.target.value } : x) })} />
            <input style={{ flex: 1 }} value={r.phrase_end ?? ""} placeholder="…ends with" onChange={(e) => setP({ ...p, removals: p.removals.map((x, j) => j === i ? { ...x, phrase_end: e.target.value } : x) })} />
            <button className="ghost" onClick={() => saveProject({ removals: p.removals.filter((_, j) => j !== i) })}>Remove</button>
          </div>
        ))}
        <div className="row">
          <button className="ghost" onClick={() => setP({ ...p, removals: [...p.removals, { phrase_start: "", phrase_end: "" }] })}>Add a cut</button>
          <button className="ghost" onClick={() => saveProject({ removals: p.removals.filter((r) => r.phrase_start && r.phrase_end) })}>Save cuts</button>
          <span className="ok">{saved}</span>
        </div>
      </div>

      <h2>4. Edit</h2>
      <div className="card">
        <div className="row">
          <label style={{ margin: 0 }}>Preview from minute</label>
          <input type="number" style={{ width: 90 }} value={preview.start} onChange={(e) => setPreview({ ...preview, start: +e.target.value })} />
          <label style={{ margin: 0 }}>to</label>
          <input type="number" style={{ width: 90 }} value={preview.end} onChange={(e) => setPreview({ ...preview, end: +e.target.value })} />
          <button className="ghost" disabled={!mapped} onClick={() => queue("preview", { start: preview.start * 60, end: preview.end * 60, only: ["episode", "trailer"] })}>Make preview</button>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button disabled={!mapped} onClick={() => queue("full")}>Edit full episode</button>
          <button className="ghost" disabled={!mapped} onClick={() => queue("redo", { stage: "edit" })}>Re-edit with new settings</button>
          <button className="ghost" onClick={() => queue("inspect")}>Re-check faces</button>
        </div>
      </div>

      <h2>5. Downloads</h2>
      {Object.entries(KIND_LABEL).filter(([k]) => k !== "inspect").map(([kind, label]) => {
        const items = dels.filter((d) => d.kind === kind);
        if (!items.length) return null;
        return (
          <div className="card" key={kind}>
            <strong>{label}</strong>
            {(kind === "trailer" || kind === "short") && <div className="grid" style={{ marginTop: 8 }}>
              {items.map((d) => urls[d.id] && <video key={d.id} className="thumb" controls preload="metadata" src={urls[d.id]} />)}
            </div>}
            <table><tbody>
              {items.map((d) => (
                <tr key={d.id}><td>{d.title}</td><td className="muted">{d.bytes ? `${(d.bytes / 1e6).toFixed(0)} MB` : ""}</td>
                  <td>{urls[d.id] && <a href={urls[d.id]} download>Download</a>}</td></tr>
              ))}
            </tbody></table>
          </div>
        );
      })}
    </>
  );
}

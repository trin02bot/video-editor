"use client";
import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { uploadResumable } from "@/lib/upload";
import { useAuth } from "@/components/useAuth";
import type { BrandKit } from "@/lib/types";

type P = { name: string; role: "host" | "guest" };
type C = { label: string; master: boolean; files: File[] };
const slug = (s: string) => s.toLowerCase().split(/\s+/)[0].replace(/[^a-z0-9]/g, "") || "p";

export default function NewProject() {
  const uid = useAuth();
  const [kits, setKits] = useState<BrandKit[]>([]);
  const [org, setOrg] = useState<string>("");
  const [f, setF] = useState({ name: "", episode_label: "", guest_line: "", guest_subline: "", kit: "", show_name: "", show_subtitle: "", accent: "#6EC1FF" });
  const [people, setPeople] = useState<P[]>([{ name: "Host", role: "host" }, { name: "", role: "guest" }]);
  const [cams, setCams] = useState<C[]>([{ label: "A", master: true, files: [] }, { label: "B", master: false, files: [] }]);
  const [progress, setProgress] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false); const [err, setErr] = useState("");

  useEffect(() => {
    if (!uid) return;
    supabase.from("members").select("org_id").eq("user_id", uid).limit(1).single().then(({ data }) => data && setOrg(data.org_id));
    supabase.from("brand_kits").select("*").then(({ data }) => setKits(data ?? []));
  }, [uid]);

  async function create() {
    setBusy(true); setErr("");
    try {
      let kitId = f.kit;
      if (!kitId) {
        const { data, error } = await supabase.from("brand_kits").insert({ org_id: org, show_name: f.show_name || f.name, show_subtitle: f.show_subtitle, accent: f.accent }).select().single();
        if (error) throw error; kitId = data.id;
      }
      const { data: proj, error } = await supabase.from("projects").insert({
        org_id: org, brand_kit_id: kitId, name: f.name, episode_label: f.episode_label, guest_line: f.guest_line,
        guest_subline: f.guest_subline, status: "uploading", created_by: uid,
      }).select().single();
      if (error) throw error;
      const used = new Set<string>();
      const rows = people.filter((p) => p.name.trim()).map((p) => {
        let s = slug(p.name); while (used.has(s)) s += "2"; used.add(s);
        return { project_id: proj.id, slug: s, name: p.name.trim(), role: p.role };
      });
      const { error: pe } = await supabase.from("people").insert(rows); if (pe) throw pe;
      for (const c of cams) {
        const { data: cam, error: ce } = await supabase.from("cameras").insert({ project_id: proj.id, label: c.label, master_audio: c.master }).select().single();
        if (ce) throw ce;
        for (const [i, file] of c.files.entries()) {
          const path = `${org}/${proj.id}/${c.label}/${i}_${file.name.replace(/[^A-Za-z0-9._-]/g, "_")}`;
          const { data: fr, error: fe } = await supabase.from("files").insert({ camera_id: cam.id, storage_path: path, original_name: file.name, bytes: file.size, position: i }).select().single();
          if (fe) throw fe;
          await uploadResumable("footage", path, file, (pct) => setProgress((p) => ({ ...p, [path]: pct })));
          await supabase.from("files").update({ uploaded: true }).eq("id", fr.id);
        }
      }
      await supabase.from("projects").update({ status: "ready" }).eq("id", proj.id);
      // first job: detect faces so the people can be mapped to each camera
      await supabase.from("jobs").insert({ project_id: proj.id, kind: "inspect" });
      location.href = `/projects/${proj.id}`;
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e)); setBusy(false);
    }
  }

  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const ready = f.name && cams.every((c) => c.files.length) && cams.filter((c) => c.master).length === 1 && people.some((p) => p.role === "host");
  return (
    <>
      <h1>New episode</h1>
      <div className="card">
        <label>Episode name</label><input value={f.name} onChange={set("name")} placeholder="Episode 4 – Jane & John Doe" />
        <div className="grid">
          <div><label>Episode label</label><input value={f.episode_label} onChange={set("episode_label")} placeholder="Episode 4" /></div>
          <div><label>Guest line (title card)</label><input value={f.guest_line} onChange={set("guest_line")} placeholder="Jane & John Doe" /></div>
          <div><label>Guest subline</label><input value={f.guest_subline} onChange={set("guest_subline")} placeholder="Parents of …" /></div>
        </div>
      </div>
      <h2>Show branding</h2>
      <div className="card">
        <label>Brand kit</label>
        <select value={f.kit} onChange={set("kit")}>
          <option value="">New brand kit…</option>
          {kits.map((k) => <option key={k.id} value={k.id}>{k.show_name}</option>)}
        </select>
        {!f.kit && <div className="grid">
          <div><label>Show name</label><input value={f.show_name} onChange={set("show_name")} placeholder="The True Village" /></div>
          <div><label>Show subtitle</label><input value={f.show_subtitle} onChange={set("show_subtitle")} placeholder="of Pro Athletes" /></div>
          <div><label>Accent color</label><input type="color" value={f.accent} onChange={set("accent")} style={{ height: 40 }} /></div>
        </div>}
      </div>
      <h2>People on camera</h2>
      <div className="card">
        {people.map((p, i) => (
          <div className="row" key={i} style={{ marginBottom: 8 }}>
            <input style={{ flex: 2 }} value={p.name} placeholder="Name" onChange={(e) => setPeople(people.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
            <select style={{ flex: 1 }} value={p.role} onChange={(e) => setPeople(people.map((x, j) => j === i ? { ...x, role: e.target.value as P["role"] } : x))}>
              <option value="host">Host</option><option value="guest">Guest</option>
            </select>
            <button className="ghost" onClick={() => setPeople(people.filter((_, j) => j !== i))}>Remove</button>
          </div>
        ))}
        <button className="ghost" onClick={() => setPeople([...people, { name: "", role: "guest" }])}>Add person</button>
      </div>
      <h2>Cameras</h2>
      <p className="muted">One camera must record the master audio (the mic). If a camera split its recording into several files, add them all in order.</p>
      {cams.map((c, i) => (
        <div className="card" key={c.label}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>Camera {c.label}</strong>
            <label className="row" style={{ margin: 0 }}>
              <input type="radio" style={{ width: "auto" }} checked={c.master} onChange={() => setCams(cams.map((x, j) => ({ ...x, master: j === i })))} />
              Master audio
            </label>
          </div>
          <input type="file" multiple accept="video/*,.mov,.mp4" onChange={(e) => setCams(cams.map((x, j) => j === i ? { ...x, files: Array.from(e.target.files ?? []) } : x))} />
          {c.files.map((file) => {
            const key = Object.keys(progress).find((k) => k.endsWith(file.name.replace(/[^A-Za-z0-9._-]/g, "_")));
            return <div key={file.name} style={{ marginTop: 8 }}>
              <div className="muted">{file.name} · {(file.size / 1e9).toFixed(2)} GB</div>
              {key && <div className="bar"><div style={{ width: `${progress[key]}%` }} /></div>}
            </div>;
          })}
        </div>
      ))}
      <button className="ghost" onClick={() => setCams([...cams, { label: String.fromCharCode(65 + cams.length), master: false, files: [] }])}>Add camera</button>
      <div className="row" style={{ marginTop: 24 }}>
        <button disabled={!ready || busy} onClick={create}>{busy ? "Uploading… keep this tab open" : "Create & upload"}</button>
        {err && <span className="bad">{err}</span>}
      </div>
    </>
  );
}

"use client";
import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useAuth } from "@/components/useAuth";
import type { Project } from "@/lib/types";

export default function Dashboard() {
  const uid = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  useEffect(() => {
    if (!uid) return;
    supabase.from("projects").select("*").order("created_at", { ascending: false }).then(({ data }) => setProjects(data ?? []));
  }, [uid]);
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Episodes</h1>
        <a href="/projects/new"><button>New episode</button></a>
      </div>
      {projects.length === 0 && <div className="card muted">No episodes yet. Create one, upload your camera files, and the studio edits it for you.</div>}
      <div className="grid">
        {projects.map((p) => (
          <a key={p.id} href={`/projects/${p.id}`} className="card" style={{ color: "inherit" }}>
            <div style={{ fontWeight: 700 }}>{p.name}</div>
            <div className="muted">{p.episode_label} {p.guest_line && `· ${p.guest_line}`}</div>
            <div style={{ marginTop: 8 }}><span className="pill">{p.status}</span></div>
          </a>
        ))}
      </div>
    </>
  );
}

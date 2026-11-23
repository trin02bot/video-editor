export type Project = {
  id: string; org_id: string; brand_kit_id: string | null; name: string; episode_label: string;
  guest_line: string; guest_subline: string; angles: string[]; removals: Removal[];
  outputs: Record<string, unknown>; status: string; created_at: string;
};
export type Removal = { phrase_start?: string; phrase_end?: string; start?: number; end?: number; note?: string };
export type Person = { id: string; project_id: string; slug: string; name: string; role: "host" | "guest" };
export type Camera = { id: string; project_id: string; label: string; master_audio: boolean; person_order: string[]; frontal: string[] };
export type FileRow = { id: string; camera_id: string; storage_path: string; original_name: string; bytes: number; position: number; uploaded: boolean };
export type Job = { id: string; project_id: string; kind: string; params: Record<string, unknown>; status: string; stage: string | null; progress: number; log: string[]; error: string | null; created_at: string };
export type Deliverable = { id: string; project_id: string; job_id: string; kind: string; title: string; storage_path: string; bytes: number | null; created_at: string };
export type BrandKit = { id: string; org_id: string; show_name: string; show_subtitle: string; accent: string; alert: string };

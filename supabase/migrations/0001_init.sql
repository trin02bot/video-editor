-- Video Editor platform schema: organizations (clients), brand kits, projects (episodes),
-- people, cameras, uploaded files, processing jobs and deliverables.
-- Every row belongs to an organization; members of that organization can see it (RLS).

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- organizations
create table public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  plan text not null default 'starter',
  created_at timestamptz not null default now()
);

create table public.members (
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'member' check (role in ('owner', 'admin', 'member')),
  created_at timestamptz not null default now(),
  primary key (org_id, user_id)
);

create or replace function public.is_member(o uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.members m where m.org_id = o and m.user_id = auth.uid());
$$;

-- New users get their own organization automatically.
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare o uuid;
begin
  insert into public.organizations (name)
  values (coalesce(new.raw_user_meta_data->>'org_name', split_part(new.email, '@', 1) || '''s studio'))
  returning id into o;
  insert into public.members (org_id, user_id, role) values (o, new.id, 'owner');
  return new;
end $$;

create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------- brand kits
create table public.brand_kits (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  show_name text not null,
  show_subtitle text default '',
  accent text not null default '#6EC1FF',
  alert text not null default '#FF4D4D',
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------- projects (episodes)
create table public.projects (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  brand_kit_id uuid references public.brand_kits(id) on delete set null,
  name text not null,
  episode_label text default '',
  guest_line text default '',
  guest_subline text default '',
  angles jsonb not null default '[]',          -- e.g. ["A:wide","A:two:shay+al","B:wide","B:single:host"]
  removals jsonb not null default '[]',        -- [{phrase_start, phrase_end} | {start, end}]
  outputs jsonb not null default '{"shorts": 10, "trailer": true}',
  status text not null default 'draft' check (status in ('draft','uploading','ready','processing','review','delivered','failed')),
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.people (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  slug text not null,                           -- short id used in angle names, e.g. "al"
  name text not null,
  role text not null default 'guest' check (role in ('host','guest')),
  unique (project_id, slug)
);

create table public.cameras (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  label text not null,                          -- "A", "B", ...
  master_audio boolean not null default false,
  person_order jsonb not null default '[]',     -- people slugs left -> right
  frontal jsonb not null default '[]',          -- people slugs facing this camera
  unique (project_id, label)
);

create table public.files (
  id uuid primary key default gen_random_uuid(),
  camera_id uuid not null references public.cameras(id) on delete cascade,
  storage_path text not null,                   -- object path in the "footage" bucket
  original_name text,
  bytes bigint,
  position int not null default 0,              -- order of chunks within one recording
  uploaded boolean not null default false,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------- jobs (the worker queue)
create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  kind text not null default 'full' check (kind in ('full','preview','inspect','redo')),
  params jsonb not null default '{}',           -- preview window, redo stage, only=[...]
  status text not null default 'queued' check (status in ('queued','running','done','failed','canceled')),
  stage text,
  progress real not null default 0,
  log jsonb not null default '[]',
  error text,
  worker text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);
create index jobs_queue on public.jobs (status, created_at);

-- Atomically hand the oldest queued job to a worker (service role only).
create or replace function public.claim_job(worker_name text) returns public.jobs
language plpgsql security definer set search_path = public as $$
declare j public.jobs;
begin
  select * into j from public.jobs where status = 'queued' order by created_at
    for update skip locked limit 1;
  if not found then return null; end if;
  update public.jobs set status = 'running', worker = worker_name, started_at = now()
    where id = j.id returning * into j;
  update public.projects set status = 'processing', updated_at = now() where id = j.project_id;
  return j;
end $$;
revoke execute on function public.claim_job(text) from public, anon, authenticated;

create table public.deliverables (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  job_id uuid references public.jobs(id) on delete set null,
  kind text not null,                           -- episode | part | trailer | short | transcript | srt | post_captions | inspect
  title text,
  storage_path text not null,                   -- object path in the "deliverables" bucket
  bytes bigint,
  meta jsonb not null default '{}',
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------- row level security
alter table public.organizations enable row level security;
alter table public.members enable row level security;
alter table public.brand_kits enable row level security;
alter table public.projects enable row level security;
alter table public.people enable row level security;
alter table public.cameras enable row level security;
alter table public.files enable row level security;
alter table public.jobs enable row level security;
alter table public.deliverables enable row level security;

create policy org_read on public.organizations for select using (public.is_member(id));
create policy members_read on public.members for select using (public.is_member(org_id));

create policy brand_all on public.brand_kits for all using (public.is_member(org_id)) with check (public.is_member(org_id));
create policy projects_all on public.projects for all using (public.is_member(org_id)) with check (public.is_member(org_id));

create policy people_all on public.people for all
  using (exists (select 1 from public.projects p where p.id = project_id and public.is_member(p.org_id)))
  with check (exists (select 1 from public.projects p where p.id = project_id and public.is_member(p.org_id)));
create policy cameras_all on public.cameras for all
  using (exists (select 1 from public.projects p where p.id = project_id and public.is_member(p.org_id)))
  with check (exists (select 1 from public.projects p where p.id = project_id and public.is_member(p.org_id)));
create policy files_all on public.files for all
  using (exists (select 1 from public.cameras c join public.projects p on p.id = c.project_id where c.id = camera_id and public.is_member(p.org_id)))
  with check (exists (select 1 from public.cameras c join public.projects p on p.id = c.project_id where c.id = camera_id and public.is_member(p.org_id)));

-- Clients can read jobs and request new ones; only the worker (service role) updates them.
create policy jobs_read on public.jobs for select
  using (exists (select 1 from public.projects p where p.id = project_id and public.is_member(p.org_id)));
create policy jobs_create on public.jobs for insert
  with check (status = 'queued' and exists (select 1 from public.projects p where p.id = project_id and public.is_member(p.org_id)));
create policy deliverables_read on public.deliverables for select
  using (exists (select 1 from public.projects p where p.id = project_id and public.is_member(p.org_id)));

-- ---------------------------------------------------------------- storage
-- footage/<org_id>/<project_id>/<camera_label>/<file>   (uploads, resumable)
-- deliverables/<org_id>/<project_id>/<job_id>/<file>    (worker output, signed download URLs)
insert into storage.buckets (id, name, public, file_size_limit)
values ('footage', 'footage', false, 53687091200), ('deliverables', 'deliverables', false, 21474836480)
on conflict (id) do nothing;

create policy footage_rw on storage.objects for all to authenticated
  using (bucket_id = 'footage' and public.is_member(((storage.foldername(name))[1])::uuid))
  with check (bucket_id = 'footage' and public.is_member(((storage.foldername(name))[1])::uuid));
create policy deliverables_read on storage.objects for select to authenticated
  using (bucket_id = 'deliverables' and public.is_member(((storage.foldername(name))[1])::uuid));

-- live progress for the dashboard
alter publication supabase_realtime add table public.jobs;
alter publication supabase_realtime add table public.deliverables;

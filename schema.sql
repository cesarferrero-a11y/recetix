-- Pega esto en Supabase > SQL Editor > New query > Run
create table if not exists public.recetas (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  url text,
  titulo text not null,
  descripcion text default '',
  raciones text default '',
  tiempo text default '',
  ingredientes jsonb not null default '[]',
  pasos jsonb not null default '[]',
  tipo text default 'otro',
  cocina text default 'otra',
  etiquetas text[] not null default '{}',
  favorita boolean not null default false,
  autor text default '',
  transcripcion text default '',
  notas text default '',
  imagen text,
  con_imagen boolean not null default false
);

create index if not exists recetas_url_idx on public.recetas (url);

-- Seguridad: nadie accede directamente desde internet; solo tu servidor con la clave secreta.
alter table public.recetas enable row level security;

-- Si la tabla ya existía (instalación anterior), ejecuta solo esto:
alter table public.recetas add column if not exists imagen text;
alter table public.recetas add column if not exists con_imagen boolean not null default false;

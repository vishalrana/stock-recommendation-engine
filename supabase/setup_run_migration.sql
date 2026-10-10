-- One-time setup (run once in the Supabase SQL editor).
-- Lets scripts/apply_migration.py apply migration files with the engine's existing
-- service-role key, so migrations no longer need the SQL editor. Only service_role may call it;
-- the anon and authenticated roles (the website) cannot. Idempotent.
create or replace function public.run_migration(sql text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  execute sql;
end;
$$;

revoke all on function public.run_migration(text) from public, anon, authenticated;
grant execute on function public.run_migration(text) to service_role;

notify pgrst, 'reload schema';

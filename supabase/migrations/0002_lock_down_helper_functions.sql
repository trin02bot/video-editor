-- handle_new_user only runs from its auth trigger; nobody should call it over the API.
revoke execute on function public.handle_new_user() from public, anon, authenticated;
-- is_member is used by RLS policies for signed-in users; signed-out visitors don't need it.
revoke execute on function public.is_member(uuid) from public, anon;
grant execute on function public.is_member(uuid) to authenticated;

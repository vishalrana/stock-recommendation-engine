-- Reload PostgREST schema cache in Supabase
-- Run this in the Supabase SQL Editor to force the cache to reload
-- after adding the failed_maxrisk_gate column.

NOTIFY pgrst, 'reload schema';

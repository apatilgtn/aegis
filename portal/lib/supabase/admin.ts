import "server-only";
import { createClient } from "@supabase/supabase-js";

/** Used only to record provider-reported telemetry, never to authorize a user. */
export function createAdminClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SECRET_KEY;
  if (!url || !key) throw new Error("Server telemetry credentials are not configured");
  return createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
}

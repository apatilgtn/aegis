import "server-only";
import { AsyncLocalStorage } from "node:async_hooks";
import { createAdminClient } from "@/lib/supabase/admin";
import type { createClient } from "@/lib/supabase/server";

type AiContext = { tenantId: string; feature?: string; supabase: Awaited<ReturnType<typeof createClient>> };
const contexts = new AsyncLocalStorage<AiContext>();
export const withAiContext = <T>(context: AiContext, fn: () => Promise<T>) => contexts.run(context, fn);

export async function reserveAiCall(messages: { content: string }[], maxTokens: number) {
  const context = contexts.getStore();
  if (!context) throw new Error("AI calls require an authenticated budget context");
  // UTF-8 bytes are a deliberately conservative input-token bound, not a bill.
  const bound = messages.reduce((n, m) => n + new TextEncoder().encode(m.content).length + 64, 128) + maxTokens;
  const { data, error } = await context.supabase.rpc("reserve_ai_budget", { p_tenant: context.tenantId, p_tokens: bound });
  if (error || !data) throw new Error(error?.message ?? "Could not reserve AI budget");
  const {data: budget, error: budgetError} = await context.supabase.rpc("ai_budget_status", {p_tenant: context.tenantId});
  if (budgetError) throw new Error("AI routing configuration could not be read");
  return { id: String(data), model: budget?.fallback_model && Number(budget.used) >= Number(budget.budget) * Number(budget.fallback_threshold_pct) / 100 ? String(budget.fallback_model) : null };
}
export async function settleAiCall(reservation: string, model: string, usage: { input: number | null; output: number | null }, duration: number, success: boolean) {
  try {
    const { error } = await createAdminClient().rpc("record_ai_usage", { p_reservation: reservation, p_model: model, p_input: usage.input, p_output: usage.output, p_ms: Math.round(duration), p_success: success, p_feature: contexts.getStore()?.feature ?? "assistant" });
    if (error) throw error;
  } catch { console.error("[aegis.ai] Usage recording failed; reservation remains charged."); }
}

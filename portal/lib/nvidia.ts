// Server-side only: imported from app/actions.ts ("use server"), never from client components.
// NVIDIA-hosted models via their OpenAI-compatible endpoint (build.nvidia.com).
// The key stays on the server. The model only drafts text or form fields;
// it never approves, submits or changes anything.

const ENDPOINT = process.env.NVIDIA_API_URL ?? "https://integrate.api.nvidia.com/v1/chat/completions";
// Tested on build.nvidia.com (Nov 2026): 5/5 correct drafts in 1–3 s with thinking turned off.
const MODEL = process.env.NVIDIA_MODEL ?? "nvidia/nemotron-3.5-lightning-30b-a3b";

export type ChatMessage = { role: "system" | "user" | "assistant"; content: string };

export function nvidiaConfigured() {
  return Boolean(process.env.NVIDIA_API_KEY);
}

export async function chat(messages: ChatMessage[], { json = false, maxTokens = 600 } = {}): Promise<string> {
  const key = process.env.NVIDIA_API_KEY;
  if (!key) throw new Error("NVIDIA_API_KEY is not set");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25_000);
  try {
    const response = await fetch(ENDPOINT, {
      method: "POST",
      signal: controller.signal,
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        model: MODEL,
        messages,
        temperature: 0.1,
        max_tokens: maxTokens,
        // Reasoning models otherwise spend the token budget thinking and return nothing.
        chat_template_kwargs: { enable_thinking: false },
        ...(json ? { response_format: { type: "json_object" } } : {}),
      }),
    });
    if (!response.ok) throw new Error(`NVIDIA API returned ${response.status}`);
    const body = await response.json();
    const content = body?.choices?.[0]?.message?.content;
    if (typeof content !== "string" || !content.trim()) throw new Error("NVIDIA API returned no answer");
    return content.trim();
  } finally {
    clearTimeout(timer);
  }
}

export type RequestDraft =
  | { capability: "access_grant"; tier: "tier1" | "tier2"; ttlHours: number; justification: string }
  | { capability: "incident_triage"; resourceHint: string; timeWindowHours: number; justification: string }
  | { capability: "label_fix"; bucket: string; costCenter: string; justification: string }
  | { capability: "unknown"; reason: string };

const WINDOWS = [1, 4, 24, 72, 168];
const DURATIONS = [1, 4, 8, 24, 72, 168];
const nearest = (value: unknown, allowed: number[], fallback: number) => {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return fallback;
  return allowed.reduce((best, option) => (Math.abs(option - number) < Math.abs(best - number) ? option : best));
};
const text = (value: unknown, limit: number) => (typeof value === "string" ? value.replace(/[\u0000-\u001f]/g, " ").trim().slice(0, limit) : "");

/** Turn free text into a draft for one of the three forms. Every field is re-validated by the form's server action. */
export async function draftRequest(request: string, buckets: string[]): Promise<RequestDraft> {
  const answer = await chat(
    [
      {
        role: "system",
        content:
          "You turn a cloud operations request into JSON for one of three forms. Reply with JSON only.\n" +
          'Access to GCP: {"capability":"access_grant","tier":"tier1"|"tier2","ttl_hours":number,"justification":string}. tier1 = read logs, tier2 = read/write storage objects.\n' +
          'Investigate a problem in logs: {"capability":"incident_triage","resource_hint":string,"time_window_hours":number,"justification":string}.\n' +
          'Add missing labels to a bucket: {"capability":"label_fix","bucket":string,"cost_center":string,"justification":string}. Known buckets: ' +
          JSON.stringify(buckets.slice(0, 50)) +
          '.\nIf it is none of these or is unclear: {"capability":"unknown","reason":string}. Never invent values the user did not imply; leave strings empty instead.',
      },
      { role: "user", content: request.slice(0, 1000) },
    ],
    { json: true, maxTokens: 800 },
  );
  let parsed: Record<string, unknown>;
  try {
    const start = answer.indexOf("{");
    const end = answer.lastIndexOf("}");
    parsed = JSON.parse(start >= 0 && end > start ? answer.slice(start, end + 1) : answer);
  } catch {
    return { capability: "unknown", reason: "The model's answer could not be read." };
  }
  switch (parsed.capability) {
    case "access_grant":
      return {
        capability: "access_grant",
        tier: parsed.tier === "tier2" ? "tier2" : "tier1",
        ttlHours: nearest(parsed.ttl_hours, DURATIONS, 8),
        justification: text(parsed.justification, 2000),
      };
    case "incident_triage":
      return {
        capability: "incident_triage",
        resourceHint: text(parsed.resource_hint, 120).replace(/[^A-Za-z0-9 ._:/@-]/g, ""),
        timeWindowHours: nearest(parsed.time_window_hours, WINDOWS, 24),
        justification: text(parsed.justification, 2000),
      };
    case "label_fix": {
      const bucket = text(parsed.bucket, 222);
      return {
        capability: "label_fix",
        bucket: buckets.includes(bucket) ? bucket : "",
        costCenter: text(parsed.cost_center, 63).toLowerCase().replace(/[^a-z0-9_-]/g, "-"),
        justification: text(parsed.justification, 2000),
      };
    }
    default:
      return { capability: "unknown", reason: text(parsed.reason, 300) || "That doesn't match a request Aegis can make." };
  }
}

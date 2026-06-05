// API client for AIfred web (I.web). Token from localStorage if set.

export type Pending = { token: string; tool: string; args: Record<string, unknown> };
export type Session = { id: number; title: string; created_ts: number; updated_ts: number };
export type ChatMessage = { role: string; content: string; ts?: number };

function headers(): HeadersInit {
  const t = localStorage.getItem("aifred_token") || "";
  return t ? { "Content-Type": "application/json", "X-AIfred-Token": t } : { "Content-Type": "application/json" };
}

// ---- sessions ----
export async function listSessions(): Promise<Session[]> {
  const r = await fetch("/api/sessions", { headers: headers() });
  if (!r.ok) throw new Error(`sessions failed: ${r.status}`);
  return (await r.json()).sessions;
}

export async function newSession(): Promise<{ id: number; title: string }> {
  const r = await fetch("/api/sessions", { method: "POST", headers: headers() });
  if (!r.ok) throw new Error(`new session failed: ${r.status}`);
  return r.json();
}

export async function sessionMessages(id: number): Promise<ChatMessage[]> {
  const r = await fetch(`/api/sessions/${id}/messages`, { headers: headers() });
  if (!r.ok) throw new Error(`messages failed: ${r.status}`);
  return (await r.json()).messages;
}

export async function renameSession(id: number, title: string): Promise<void> {
  await fetch(`/api/sessions/${id}`, { method: "PATCH", headers: headers(), body: JSON.stringify({ title }) });
}

export async function deleteSession(id: number): Promise<void> {
  await fetch(`/api/sessions/${id}`, { method: "DELETE", headers: headers() });
}

// ---- SSE chat ----
export type StreamEvent =
  | { type: "session"; session_id: number }
  | { type: "status"; event: string; detail: string }
  | { type: "reply"; reply: string; session_id: number; pending: Pending[] }
  | { type: "error"; error: string };

/**
 * Stream a chat turn over SSE. Calls onEvent for each event. Works with POST
 * (manual SSE parse over fetch ReadableStream) so the message goes in the body.
 */
export async function streamChat(
  message: string,
  sessionId: number | null,
  onEvent: (e: StreamEvent) => void,
): Promise<void> {
  const r = await fetch("/api/chat/stream", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!r.ok || !r.body) throw new Error(`chat stream failed: ${r.status}`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() || "";
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue; // heartbeat comment
      try {
        onEvent(JSON.parse(line.slice(6)) as StreamEvent);
      } catch {
        /* ignore malformed */
      }
    }
  }
}

export async function confirm(token: string, approve: boolean): Promise<void> {
  const r = await fetch("/api/confirm", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ token, approve }),
  });
  if (!r.ok) throw new Error(`confirm failed: ${r.status}`);
}

export type WaStatus = { state: string; qr: string | null; paired: boolean; error?: string | null };

export async function waStatus(): Promise<WaStatus> {
  const r = await fetch("/api/whatsapp/status", { headers: headers() });
  if (!r.ok) throw new Error(`wa status failed: ${r.status}`);
  return r.json();
}

export async function waStart(): Promise<WaStatus> {
  const r = await fetch("/api/whatsapp/start", { method: "POST", headers: headers() });
  if (!r.ok) throw new Error(`wa start failed: ${r.status}`);
  return r.json();
}

// ---- attention feed + triage settings ----
export type AttentionItem = { id: number; importance: string; text: string; ts: number };

export async function getAttention(): Promise<AttentionItem[]> {
  const r = await fetch("/api/attention", { headers: headers() });
  if (!r.ok) return [];
  return (await r.json()).items;
}

export async function attentionDone(id: number): Promise<void> {
  await fetch(`/api/attention/${id}/done`, { method: "POST", headers: headers() });
}

export async function getTriageInterval(): Promise<number> {
  const r = await fetch("/api/settings", { headers: headers() });
  if (!r.ok) return 0;
  return (await r.json()).triage_interval_min;
}

export async function setTriageInterval(min: number): Promise<void> {
  await fetch("/api/settings", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ triage_interval_min: min }),
  });
}

export async function runTriage(): Promise<{ new: number; high: string[]; scanned: number }> {
  const r = await fetch("/api/triage/run", { method: "POST", headers: headers() });
  if (!r.ok) throw new Error(`triage failed: ${r.status}`);
  return r.json();
}

// ---- triage rules ----
export type Rule = { id: number; scope: string; pattern: string; action: string };

export async function listRules(): Promise<Rule[]> {
  const r = await fetch("/api/rules", { headers: headers() });
  if (!r.ok) return [];
  return (await r.json()).rules;
}

export async function addRule(scope: string, action: string, pattern: string): Promise<void> {
  await fetch("/api/rules", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ scope, action, pattern }),
  });
}

export async function deleteRule(id: number): Promise<void> {
  await fetch(`/api/rules/${id}`, { method: "DELETE", headers: headers() });
}

// ---- review queue (active learning) ----
export type ReviewItem = { id: number; text: string; suggest: string; ts: number; person: string; is_group: boolean };
export type ReviewDecision = "important" | "not_important" | "mute";

export async function getReview(): Promise<ReviewItem[]> {
  const r = await fetch("/api/review", { headers: headers() });
  if (!r.ok) return [];
  return (await r.json()).items;
}

export async function decideReview(id: number, decision: ReviewDecision): Promise<void> {
  await fetch(`/api/review/${id}`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ decision }),
  });
}

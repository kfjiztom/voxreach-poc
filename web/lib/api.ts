// Thin REST client over the Next.js rewrite proxy → sidecar.

const BASE = "/api/sidecar";

async function post(path: string, body?: unknown): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
}

export async function startCall(): Promise<{ call_id: string }> {
  const r = await post("/call/start");
  if (!r.ok) throw new Error(`startCall failed: ${r.status}`);
  return r.json();
}

export async function endCall(): Promise<unknown> {
  const r = await post("/call/end");
  if (!r.ok) throw new Error(`endCall failed: ${r.status}`);
  return r.json();
}

export async function playMockScenario(
  scenario: "order" | "info" | "escalate" | "modify",
): Promise<void> {
  const r = await post(`/mock/play?scenario=${scenario}`);
  if (!r.ok) throw new Error(`playMockScenario failed: ${r.status}`);
}

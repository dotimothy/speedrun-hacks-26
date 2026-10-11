import type { ProcessResponse } from "../types";

export function parseResult(raw: unknown): ProcessResponse {
  if (!raw || typeof raw !== "object")
    throw new Error("Backend returned an invalid response.");
  const r = raw as ProcessResponse;
  if (
    typeof r.session_id !== "string" ||
    typeof r.transcript_original !== "string" ||
    typeof r.transcript_english !== "string" ||
    !Array.isArray(r.predictions) ||
    !r.latency_ms ||
    typeof r.latency_ms !== "object" ||
    !Number.isFinite(r.latency_ms.server_total) ||
    (r.detected_language !== null && typeof r.detected_language !== "string")
  )
    throw new Error(
      "Backend response is missing transcript or prediction fields.",
    );
  for (const p of r.predictions) {
    if (
      !p ||
      typeof p.label !== "string" ||
      !["unverified", "confirmed", "corrected"].includes(p.verification_state)
    )
      throw new Error("Backend returned an invalid prediction.");
    if (
      p.evidence &&
      (typeof p.evidence.text !== "string" ||
        !["original", "english"].includes(p.evidence.source))
    )
      throw new Error("Backend returned invalid evidence.");
  }
  return r;
}
export async function requestSnapshot(
  sessionId: string,
  input: Blob | string,
  signal: AbortSignal,
): Promise<ProcessResponse> {
  const started = performance.now();
  let body: BodyInit;
  let headers: HeadersInit | undefined;
  if (typeof input === "string") {
    body = JSON.stringify({ session_id: sessionId, transcript: input });
    headers = { "Content-Type": "application/json" };
  } else {
    const form = new FormData();
    form.append(
      "payload",
      JSON.stringify({ session_id: sessionId, whisper_size: "small" }),
    );
    form.append("audio", input, "call.wav");
    body = form;
  }
  const response = await fetch("/api/process", {
    method: "POST",
    headers,
    body,
    signal,
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(
      `Processing failed (${response.status}). ${message.slice(0, 180)}`,
    );
  }
  const result = parseResult(await response.json());
  if (result.session_id !== sessionId)
    throw new Error("Response belongs to a different session; it was ignored.");
  return {
    ...result,
    latency_ms: {
      ...result.latency_ms,
      end_to_end: Math.round(performance.now() - started),
    },
  };
}
export function clock(seconds: number) {
  const n = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`;
}

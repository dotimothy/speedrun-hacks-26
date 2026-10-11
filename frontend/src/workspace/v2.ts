import type { Action, Field, Mode, Session, Suggestion } from "./session";

export const wireFields: Record<Field, string> = {
  location: "location_text",
  callback: "callback_number",
  incident: "incident_type",
  people: "persons_involved",
  access: "access_notes",
  injuries: "injuries",
  hazards: "hazards",
  weapons: "weapons_present",
  safety: "caller_safe",
  caller_name: "caller_name",
  chief_complaint: "chief_complaint",
  awake: "awake",
  breathing: "breathing",
  occupancy: "occupancy",
  fire_extent: "fire_extent",
  incident_timing: "incident_timing",
  patient_age: "patient_age",
  medical_history: "medical_history",
  vehicle_details: "vehicle_details",
  road_conditions: "road_conditions",
  people_accounted_for: "people_accounted_for",
  suspect_description: "suspect_description",
  suspect_location: "suspect_location",
  gas_source: "gas_source",
  water_conditions: "water_conditions",
  last_seen: "last_seen",
  fire_occupants: "fire_occupants",
  medical_course: "medical_course",
  collision_entrapment: "collision_entrapment",
  collision_road_blockage: "collision_road_blockage",
  crime_presence: "crime_presence",
  crime_activity: "crime_activity",
  gas_location: "gas_location",
  gas_exposure: "gas_exposure",
  water_visibility: "water_visibility",
  water_motion: "water_motion",
  symptom_details: "symptom_details",
  fire_observations: "fire_observations",
  scene_hazards: "scene_hazards",
  location_context: "location_context",
  people_details: "people_details",

  occupant_last_seen: "occupant_last_seen",
  symptom_changes: "symptom_changes",
  trapped_vehicle_location: "trapped_vehicle_location",
  affected_lanes: "affected_lanes",
  departure_direction: "departure_direction",
  person_whereabouts: "person_whereabouts",
  reported_symptoms: "reported_symptoms",



};
const fieldFor = (name: string) =>
  (Object.keys(wireFields) as Field[]).find((f) => wireFields[f] === name);
export type Speaker = "caller" | "operator" | "interpreter" | "unknown";
interface Evidence {
  segment_id: string;
  text: string;
  source: "original" | "translated";
  method: string;
}
interface WireSuggestion {
  id: string;
  field: string;
  kind: "addition" | "correction" | "conflict" | "confirmation";
  proposed_value: unknown;
  resolved_value: unknown;
  status: Suggestion["status"];
  evidence: Evidence[];
  conflict_options: {
    option_id: string;
    value: unknown;
    statement: string;
    segment_id: string;
  }[];
}
export interface Snapshot {
  session_id: string;
  decision_engine?: string | null;
  assistant?: {
    status: "ready" | "unavailable" | "error";
    summary: string;
    follow_up_question: string | null;
    target_field: string | null;
    source_segment_ids: string[];
    source_revision: number;
    source_segment_count: number;
    model: string;
    provider: string;
    message: string | null;
  } | null;
  revision: number;
  event_seq: number;
  detected_language: string | null;
  segments: {
    id: string;
    speaker: Speaker;
    original_text: string;
    translation: string | null;
    language: string | null;
    start_ms: number | null;
    received_at: string;
  }[];
  accepted_record: Record<
    string,
    {
      value: unknown;
      presence: string;
      history: { revision: number; action: string; value: unknown }[];
    }
  >;
  suggestions: WireSuggestion[];
  relevant_fields?: string[];
  decision_focus?: {
    scenario: string;
    title: string;
    purpose: string;
    decisions: { field: string; label: string; value: unknown; status: string; suggestion_id: string | null }[];
  } | null;
  follow_up_questions?: { field: string; phrase_id: string; text: string; reason: string }[];
  narrative: { text: string; edited: boolean; editor_text: string | null };
  published: {
    id: string;
    revision: number;
    record: Record<string, unknown>;
    narrative: string;
    unresolved: string[];
    open_flags: string[];
    changed_fields: string[];
  }[];
  unresolved: { text: string }[];
  review_flags: { id: string; text: string; status: string }[];
  diagnostics: {
    last_total_ms: number;
    engine: Record<string, string>;
    notes: string[];
  };
}
export function validateSnapshot(raw: unknown): Snapshot {
  const r = raw as Snapshot;
  if (
    !r ||
    typeof r.session_id !== "string" ||
    !Number.isInteger(r.event_seq) ||
    !Number.isInteger(r.revision) ||
    !Array.isArray(r.segments) ||
    !Array.isArray(r.suggestions) ||
    !r.accepted_record ||
    !Array.isArray(r.published) ||
    !r.narrative ||
    !Array.isArray(r.unresolved) ||
    !Array.isArray(r.review_flags)
  )
    throw new Error("The API did not return a v2 session snapshot.");
  return r;
}
const observationFields = ["symptom_details", "fire_observations", "scene_hazards", "location_context", "people_details"];
function display(value: unknown, field?: string): string {
  if (value == null) return "Not stated";
  if (Array.isArray(value))
    return value.length
      ? value.map(String).join(field && observationFields.includes(field) ? "\n" : ", ").replace(/_/g, " ")
      : "None stated";
  return String(value).replace(/_/g, " ");
}
export function adaptSnapshot(raw: Snapshot, mode: Mode): Session {
  const record: Session["record"] = {};
  const history: { revision: number; text: string }[] = [];
  for (const [name, item] of Object.entries(raw.accepted_record)) {
    const field = fieldFor(name);
    if (field && item.presence !== "unknown")
      record[field] =
        item.presence === "stated_absent"
          ? "Stated absent"
          : display(item.value, name);
    for (const entry of item.history ?? [])
      history.push({
        revision: entry.revision,
        text: `${name.replace(/_/g, " ")} · ${entry.action}: ${display(entry.value)}`,
      });
  }
  const sourceIndex = (id: string) =>
    raw.segments.findIndex((s) => s.id === id);
  const suggestions: Suggestion[] = raw.suggestions.flatMap((item) => {
    const field = fieldFor(item.field);
    if (!field) return [];
    const e = item.evidence[0];
    return [
      {
        id: item.id,
        field,
        value:
          item.kind === "conflict"
            ? "Statements disagree"
            : display(item.proposed_value, item.field),
        source: e ? sourceIndex(e.segment_id) : -1,
        kind: item.kind === "confirmation" ? "addition" : item.kind,
        status: item.status,
        rawValue: item.proposed_value,
        options: item.conflict_options.map((o) => ({
          id: o.option_id,
          value: display(o.value, item.field),
          statement: o.statement,
          source: sourceIndex(o.segment_id),
        })),
        evidence: e
          ? {
              text: e.text,
              source: e.source === "translated" ? "english" : "original",
              method:
                e.method === "keyword_context"
                  ? "Keyword context; not a model explanation"
                  : e.method.replace(/_/g, " "),
            }
          : null,
      },
    ];
  });
  const latest = raw.published[raw.published.length - 1];
  const publishedRecord: Session["record"] = {};
  if (latest)
    for (const [name, value] of Object.entries(latest.record)) {
      const field = fieldFor(name);
      if (field && value != null) publishedRecord[field] = display(value, name);
    }
  return {
    id: raw.session_id,
    mode,
    record,
    suggestions,
    revision: raw.revision,
    lastSequence: raw.event_seq,
    observed: {},
    latency: raw.diagnostics?.last_total_ms ?? null,
    language: raw.detected_language,
    step: raw.segments.length - 1,
    segments: raw.segments.map((s, i) => ({
      id: i,
      at: `Utterance ${i + 1}`,
      speaker:
        s.speaker === "caller"
          ? "Caller"
          : s.speaker === "operator"
            ? "Call taker"
            : s.speaker === "interpreter"
              ? "Interpreter"
              : "Unattributed audio",
      language: s.language || undefined,
      original: s.original_text,
      english: s.translation ?? s.original_text,
    })),
    history: history.sort((a, b) => b.revision - a.revision).map((h) => h.text),
    published: latest
      ? {
          version: raw.published.length,
          record: publishedRecord,
          note: latest.narrative,
          unresolved: [...latest.unresolved, ...latest.open_flags],
        }
      : null,
    server: raw,
  };
}
export class ApiConflict extends Error {
  constructor(
    public snapshot: Snapshot,
    message: string,
  ) {
    super(message);
  }
}
async function request(
  path: string,
  body?: unknown,
  method = "POST",
  signal?: AbortSignal,
): Promise<any> {
  const response = await fetch(`/api/v2${path}`, {
    method,
    signal,
    headers:
      body instanceof FormData || body === undefined
        ? undefined
        : { "Content-Type": "application/json" },
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  const raw = await response.json().catch(() => null);
  if (response.status === 409 && raw?.snapshot)
    throw new ApiConflict(
      validateSnapshot(raw.snapshot),
      raw.reason ||
        "Record changed. Review the current state before trying again.",
    );
  if (!response.ok)
    throw new Error(
      `API ${response.status}: ${typeof raw?.detail === "string" ? raw.detail : "Request failed"}`,
    );
  return raw;
}
// Serialize writes per call. Each operation keeps its ID when retried.
const queues = new Map<string, Promise<unknown>>();
function serialize<T>(id: string, task: () => Promise<T>): Promise<T> {
  const next = (queues.get(id) ?? Promise.resolve())
    .catch(() => undefined)
    .then(task);
  queues.set(id, next);
  void next
    .finally(() => {
      if (queues.get(id) === next) queues.delete(id);
    })
    .catch(() => undefined);
  return next;
}
/** Read a session, or null if it is gone. Never creates one. */
export async function readSession(
  id: string,
  signal: AbortSignal,
): Promise<Snapshot | null> {
  const response = await fetch(`/api/v2/sessions/${encodeURIComponent(id)}`, {
    signal,
  });
  if (response.status === 404) return null;
  if (!response.ok)
    throw new Error(`Could not load session (${response.status}).`);
  return validateSnapshot(await response.json());
}
export async function ensureSession(
  id: string,
  signal: AbortSignal,
): Promise<Snapshot> {
  const response = await fetch(`/api/v2/sessions/${encodeURIComponent(id)}`, {
    signal,
  });
  if (response.status === 404)
    return validateSnapshot(
      await request("/sessions", { session_id: id }, "POST", signal),
    );
  if (!response.ok)
    throw new Error(`Could not load session (${response.status}).`);
  return validateSnapshot(await response.json());
}
export type Utterance = {
  input: Blob | string;
  clientId: string;
  speaker: Speaker;
  startMs?: number;
  endMs?: number;
  decisionEngine?: string;
  whisperSize?: string;
};
export function setSessionEngine(id: string, engine: string, signal: AbortSignal): Promise<Snapshot> {
  return serialize(id, async () => {
    await ensureSession(id, signal);
    const result = await request(`/sessions/${encodeURIComponent(id)}/settings`,
      { decision_engine: engine || null }, "POST", signal);
    return validateSnapshot(result.snapshot);
  });
}
export async function generateAssistance(id: string, signal: AbortSignal): Promise<Snapshot> {
  const result = await request(`/sessions/${encodeURIComponent(id)}/assist`, {}, "POST", signal);
  return validateSnapshot(result.snapshot);
}
export function appendUtterance(
  id: string,
  utterance: Utterance,
  signal: AbortSignal,
): Promise<Snapshot> {
  return serialize(id, async () => {
    await ensureSession(id, signal);
    const payload = {
      client_segment_id: utterance.clientId,
      speaker: utterance.speaker,
      start_ms: utterance.startMs,
      end_ms: utterance.endMs,
      whisper_size: utterance.whisperSize ?? "small",
      decision_engine: utterance.decisionEngine || undefined,
      ...(typeof utterance.input === "string" ? { text: utterance.input } : {}),
    };
    let body: unknown = payload;
    if (utterance.input instanceof Blob) {
      const form = new FormData();
      form.append("payload", JSON.stringify(payload));
      form.append("audio", utterance.input, "utterance.wav");
      body = form;
    }
    const result = await request(
      `/sessions/${encodeURIComponent(id)}/segments`,
      body,
      "POST",
      signal,
    );
    return validateSnapshot(result.snapshot);
  });
}
export function reviewOperation(
  session: Session,
  action: Action | { type: "resolve"; id: string; option: string },
) {
  const operation = crypto.randomUUID();
  let publicationSent = false;
  return () =>
    serialize(session.id, async () => {
      const signal = AbortSignal.timeout(90000);
      const base = `/sessions/${encodeURIComponent(session.id)}`;
      let raw: any;
      if (action.type === "review" || action.type === "resolve") {
        const suffix = action.type === "resolve" ? "resolve" : action.decision;
        raw = await request(
          `${base}/suggestions/${encodeURIComponent(action.id)}/${suffix}`,
          {
            operation_id: operation,
            base_revision: session.revision,
            ...(action.type === "resolve"
              ? { reason: action.option }
              : action.value === undefined
                ? {}
                : {
                    value: typedValue(
                      session.suggestions.find((s) => s.id === action.id)!
                        .field,
                      action.value,
                    ),
                  }),
          },
          "POST",
          signal,
        );
      } else if (action.type === "manual") {
        await ensureSession(session.id, signal);
        raw = await request(
          `${base}/fields/${wireFields[action.field]}`,
          {
            operation_id: operation,
            value: action.value.trim()
              ? typedValue(action.field, action.value)
              : null,
            presence: action.value.trim() ? "stated" : "unknown",
          },
          "POST",
          signal,
        );
      } else if (action.type === "publish") {
        const current = await ensureSession(session.id, signal);
        if (!publicationSent && current.revision !== session.revision) {
          throw new ApiConflict(
            current,
            "The record changed. Review the current values before publishing.",
          );
        }
        if (!publicationSent)
          await request(
            `${base}/narrative`,
            { text: action.note, operation_id: `${operation}-note` },
            "PUT",
            signal,
          );
        publicationSent = true;
        raw = await request(
          `${base}/publish`,
          { operation_id: operation, base_revision: session.revision },
          "POST",
          signal,
        );
        const inbox = await request(
          `/dispatch/inbox/${encodeURIComponent(session.id)}`,
          undefined,
          "GET",
          signal,
        );
        raw.snapshot.published = inbox.publications;
      } else throw new Error("Unsupported server action");
      return validateSnapshot(raw.snapshot);
    });
}
function typedValue(field: Field, value: string): unknown {
  if (field === "people") {
    const count = Number(value);
    if (!Number.isInteger(count) || count < 0 || !value.trim())
      throw new Error("People mentioned must be a non-negative whole number.");
    return count;
  }
  if (observationFields.includes(field))
    return value.split("\n").map(s => s.trim()).filter(Boolean);
  if (field === "hazards" || field === "access")
    return value
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  if (["incident", "injuries", "weapons", "safety", "chief_complaint", "awake", "breathing", "occupancy", "fire_extent", "fire_occupants", "medical_course", "collision_entrapment", "collision_road_blockage", "crime_presence", "crime_activity", "gas_location", "gas_exposure", "water_visibility", "water_motion"].includes(field))
    return value.trim().replace(/ /g, "_");
  return value.trim();
}

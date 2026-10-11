import type { Snapshot } from "./v2";
import type { ProcessResponse } from "../types";
// Fixture adapter: UI consumes these session transitions independently of transport.
// Values are synthetic; accepted means operator-reviewed, not translation-verified.
export const fields = [
  "location",
  "callback",
  "incident",
  "people",
  "access",
  "injuries",
  "hazards",
  "weapons",
  "safety",
] as const;
export type Field = (typeof fields)[number];
export const labels: Record<Field, string> = {
  location: "Incident location",
  callback: "Callback number",
  incident: "Caller-reported incident",
  people: "People mentioned",
  access: "Access & whereabouts",
  injuries: "Injuries reported",
  hazards: "Hazards mentioned",
  weapons: "Weapon mentioned",
  safety: "Caller safety",
};
export type Segment = {
  id: number;
  at: string;
  speaker:
    | "Caller"
    | "Call taker"
    | "Unattributed audio"
    | "Transcript input"
    | "Interpreter";
  snapshot?: boolean;
  language?: string;
  original: string;
  english: string;
};
export type Suggestion = {
  id: string;
  field: Field;
  value: string;
  source: number;
  kind: "addition" | "correction" | "change" | "conflict";
  rawValue?: unknown;
  options?: { id: string; value: string; statement: string; source: number }[];
  evidence?: {
    text: string;
    source: "original" | "english";
    method: string;
  } | null;
  status: "pending" | "accepted" | "edited" | "rejected" | "superseded";
};
export type Publication = {
  version: number;
  record: Partial<Record<Field, string>>;
  note: string;
  unresolved: string[];
};
export type Mode = "demo" | "live" | "transcript";
export type Session = {
  server?: Snapshot;
  id: string;
  mode: Mode;
  segments: Segment[];
  language: string | null;
  lastSequence: number;
  observed: Partial<Record<Field, string>>;
  latency: number | null;
  step: number;
  revision: number;
  record: Partial<Record<Field, string>>;
  suggestions: Suggestion[];
  history: string[];
  published: Publication | null;
};
export const fixtureSegments: Segment[] = [
  {
    id: 0,
    at: "00:00",
    speaker: "Call taker",
    original: "911. What is the address of the emergency?",
    english: "911. What is the address of the emergency?",
  },
  {
    id: 1,
    at: "00:07",
    speaker: "Caller",
    original: "Hay humo en la cocina. Estoy en el 124 de Oak Street.",
    english: "There is smoke in the kitchen. I’m at 124 Oak Street.",
  },
  {
    id: 2,
    at: "00:16",
    speaker: "Caller",
    original: "Mi número es 555-0147. Estoy afuera con mi hermano.",
    english: "My number is 555-0147. I’m outside with my brother.",
  },
  {
    id: 3,
    at: "00:28",
    speaker: "Caller",
    original: "Perdón, es el 142, no el 124 de Oak Street.",
    english: "Sorry, it’s 142, not 124 Oak Street.",
  },
  {
    id: 4,
    at: "00:39",
    speaker: "Caller",
    original: "Estamos detrás del edificio, junto al callejón.",
    english: "We’re behind the building, beside the alley.",
  },
];
const updates: Omit<Suggestion, "status">[][] = [
  [],
  [
    {
      id: "location-1",
      field: "location",
      value: "124 Oak Street",
      source: 1,
      kind: "addition",
    },
    {
      id: "incident-1",
      field: "incident",
      value: "Smoke in the kitchen",
      source: 1,
      kind: "addition",
    },
  ],
  [
    {
      id: "callback-1",
      field: "callback",
      value: "555-0147",
      source: 2,
      kind: "addition",
    },
    {
      id: "people-1",
      field: "people",
      value: "Caller and brother, outside",
      source: 2,
      kind: "addition",
    },
  ],
  [
    {
      id: "location-2",
      field: "location",
      value: "142 Oak Street",
      source: 3,
      kind: "correction",
    },
  ],
  [
    {
      id: "access-1",
      field: "access",
      value: "Behind the building, beside the alley",
      source: 4,
      kind: "addition",
    },
  ],
];
export function initialSession(mode: Mode = "demo", id = "DQ–1024"): Session {
  return {
    id,
    mode,
    segments: mode === "demo" ? fixtureSegments.slice(0, 2) : [],
    language: mode === "demo" ? "es" : null,
    lastSequence: 0,
    observed: {},
    latency: null,
    step: mode === "demo" ? 1 : -1,
    revision: 0,
    record: {},
    suggestions:
      mode === "demo"
        ? updates[1].map((s) => ({ ...s, status: "pending" }))
        : [],
    history: [],
    published: null,
  };
}
export type Action =
  | { type: "server"; snapshot: Session }
  | { type: "manual"; field: Field; value: string }
  | { type: "next" }
  | { type: "reset"; mode?: Mode; id?: string }
  | {
      type: "result";
      id: string;
      sequence: number;
      at: string;
      response: ProcessResponse;
    }
  | {
      type: "review";
      id: string;
      decision: "accept" | "reject";
      value?: string;
    }
  | { type: "publish"; note: string };
export function transition(state: Session, action: Action): Session {
  if (action.type === "server")
    return action.snapshot.id === state.id &&
      action.snapshot.lastSequence >= state.lastSequence
      ? action.snapshot
      : state;
  if (action.type === "reset") return initialSession(action.mode, action.id);
  if (action.type === "result") return ingestResult(state, action);
  if (action.type === "manual") {
    const value = action.value.trim();
    const record = { ...state.record };
    if (value) record[action.field] = value;
    else delete record[action.field];
    return {
      ...state,
      revision: state.revision + 1,
      record,
      history: [
        `${labels[action.field]} manually ${value ? "edited: " + value : "cleared"}`,
        ...state.history,
      ],
    };
  }
  if (action.type === "next") {
    if (state.mode !== "demo" || state.step >= fixtureSegments.length - 1)
      return state;
    const step = state.step + 1;
    const incoming = updates[step];
    return {
      ...state,
      step,
      segments: fixtureSegments.slice(0, step + 1),
      suggestions: [
        ...state.suggestions.map((s) =>
          s.status === "pending" && incoming.some((n) => n.field === s.field)
            ? { ...s, status: "superseded" as const }
            : s,
        ),
        ...incoming.map((s) => ({ ...s, status: "pending" as const })),
      ],
    };
  }
  if (action.type === "review") {
    const item = state.suggestions.find(
      (s) => s.id === action.id && s.status === "pending",
    );
    if (!item) return state;
    const value = (action.value ?? item.value).trim();
    if (action.decision === "accept" && !value) return state;
    const status =
      action.decision === "reject"
        ? "rejected"
        : value === item.value
          ? "accepted"
          : "edited";
    return {
      ...state,
      revision: state.revision + 1,
      record:
        action.decision === "accept"
          ? { ...state.record, [item.field]: value }
          : state.record,
      suggestions: state.suggestions.map((s) =>
        s.id === item.id ? { ...s, status } : s,
      ),
      history: [
        `${labels[item.field]} ${status}${status === "rejected" ? "" : `: ${value}`}`,
        ...state.history,
      ],
    };
  }
  if (!Object.keys(state.record).length) return state;
  return {
    ...state,
    published: {
      version: (state.published?.version ?? 0) + 1,
      record: { ...state.record },
      note: action.note.trim(),
      unresolved: unresolvedItems(state),
    },
    history: [
      "Published reviewed record to local receiving preview",
      ...state.history,
    ],
  };
}

// Current API returns full transcript snapshots, not speaker-tagged utterances.
// Keep immutable snapshots so later ASR revisions never change earlier evidence.
const mapping: Record<string, Field> = {
  location_text: "location",
  callback_number: "callback",
  incident_type: "incident",
  persons_involved: "people",
  injuries: "injuries",
  hazards: "hazards",
  weapons_present: "weapons",
  caller_safe: "safety",
};
function displayValue(value: unknown): string | undefined {
  if (value == null || value === "" || value === "unknown") return undefined;
  if (Array.isArray(value))
    return value.length
      ? value.map(String).join(", ").replace(/_/g, " ")
      : undefined;
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  )
    return String(value).replace(/_/g, " ");
  return undefined;
}
export function ingestResult(
  state: Session,
  action: Extract<Action, { type: "result" }>,
): Session {
  if (
    state.mode === "demo" ||
    state.id !== action.id ||
    action.response.session_id !== state.id ||
    action.sequence <= state.lastSequence
  )
    return state;
  const r = action.response;
  const last = state.segments[state.segments.length - 1];
  const changed =
    !last ||
    last.original !== r.transcript_original ||
    last.english !== r.transcript_english;
  const source = changed ? state.segments.length : last.id;
  const snapshot: Segment = {
    id: source,
    at: action.at,
    speaker:
      state.mode === "transcript" ? "Transcript input" : "Unattributed audio",
    original: r.transcript_original,
    english: r.transcript_english,
    snapshot: true,
    language: r.detected_language || undefined,
  };
  let suggestions = [...state.suggestions];
  const observed: Session["observed"] = {};
  for (const prediction of r.predictions) {
    const field = mapping[prediction.label];
    if (!field) continue;
    // This adapter never sends human confirmations back into extraction. If the
    // server supplies a merged result, its alternative is the new model opinion.
    const raw =
      prediction.verification_state !== "unverified" && prediction.conflict
        ? prediction.alternative
        : prediction.value;
    const value = displayValue(raw);
    observed[field] = value;
    if (value === state.observed[field]) continue;
    suggestions = suggestions.map((s) =>
      s.field === field && s.status === "pending"
        ? { ...s, status: "superseded" }
        : s,
    );
    if (!value || value === state.record[field]) continue;
    suggestions.push({
      id: `${state.id}:${action.sequence}:${field}`,
      field,
      value,
      source,
      kind: state.record[field] ? "change" : "addition",
      status: "pending",
      evidence: prediction.evidence
        ? {
            text: prediction.evidence.text,
            source: prediction.evidence.source,
            method:
              prediction.decided_on === "rules"
                ? "Pattern-matched context"
                : "Keyword-located context; not a model explanation",
          }
        : null,
    });
  }
  // A missing prediction is also a withdrawal, never an instruction to clear
  // the operator record. Do not leave obsolete model proposals active.
  suggestions = suggestions.map((s) =>
    s.status === "pending" && mappingHasField(s.field) && !observed[s.field]
      ? { ...s, status: "superseded" }
      : s,
  );
  return {
    ...state,
    lastSequence: action.sequence,
    language: r.detected_language,
    latency: r.latency_ms.end_to_end ?? r.latency_ms.server_total,
    observed,
    suggestions,
    segments: changed ? [...state.segments, snapshot] : state.segments,
    step: changed ? source : state.step,
  };
}
function mappingHasField(field: Field) {
  return Object.values(mapping).includes(field);
}
export function unresolvedItems(state: Session): string[] {
  return [
    ...(state.mode === "demo" ? fields.slice(0, 5) : fields)
      .filter((f) => !state.record[f])
      .map((f) => `${labels[f]} not recorded`),
    ...state.suggestions
      .filter((s) => s.status === "pending")
      .map((s) => `${labels[s.field]}: ${s.kind} awaiting review`),
  ];
}

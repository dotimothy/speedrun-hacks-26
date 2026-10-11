// Mirrors backend/app/schemas.py. That file is the source of truth; where the
// two drift, the backend is right.

export type Tristate = 'yes' | 'no' | 'unknown'
export type VerificationState = 'unverified' | 'confirmed' | 'corrected'

export interface EvidenceSpan {
  text: string
  start: number
  end: number
  source: 'original' | 'english'
  match: 'exact' | 'normalized' | 'fuzzy'
}

export interface Prediction {
  label: string
  value: unknown
  score: number
  evidence: EvidenceSpan | null
  verification_state: VerificationState
  conflict: boolean
  evidence_unverified: boolean
  probabilities: Record<string, number> | null
  raw_probability: number | null
  decided_on: 'original' | 'english' | 'both' | 'rules'
  alternative: unknown
  breakdown: Record<string, unknown> | null
}

export interface IncidentForm {
  incident_type: string
  location_text: string | null
  callback_number: string | null
  injuries: string
  persons_involved: number | null
  hazards: string[]
  weapons_present: Tristate
  caller_safe: Tristate
}

export interface LatencyBreakdown {
  asr: number
  translate: number
  decision: number
  tts: number
  server_total: number
  network: number | null
  end_to_end: number | null
}

export interface ProcessResponse {
  session_id: string
  detected_language: string | null
  language_probability: number | null
  transcript_original: string
  transcript_english: string
  predictions: Prediction[]
  suggested_form: IncidentForm
  suggested_action: string
  missing_fields: string[]
  requires_review: boolean
  latency_ms: LatencyBreakdown
  engine: Record<string, string>
  notes: string[]
  calibration_warning: string | null
  auto_reply: AutoReplySuggestion | null
}

export interface AutoReplySuggestion {
  phrase_id: string | null
  language: string | null
  text: string | null
  reason: string
  target: string | null
  allowed: boolean
}

export interface ModelInfo {
  whisper_sizes: string[]
  whisper_default: string
  whisper_loaded: string[]
  decision_engines: string[]
  decision_default: string
  thresholds: Record<string, number>
}

export interface ApprovedPhrase {
  id: string
  language: string
  text: string
}

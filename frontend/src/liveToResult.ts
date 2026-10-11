import type { Prediction, ProcessResponse } from './types'

/**
 * Project a live session onto the shape the main dashboard already renders.
 *
 * The transcript pane, the prediction list, the incident form and the latency
 * panel were all built against `/api/process`, and they are good. Rewriting
 * them to speak v2 would mean two implementations of the same views drifting
 * apart; mapping once here means a phone call lights up the whole dashboard
 * rather than only the review strip, and anything added to those panes later
 * works for both paths with no extra wiring.
 *
 * What the mapping must not do is lose the distinction the v2 model exists to
 * draw. An accepted value and a suggestion are both rendered, but the
 * accepted one carries `verification_state: confirmed` and a score of 1.0 --
 * a human put it there -- while a suggestion stays `unverified` with its
 * derived score. The form shows accepted values where they exist and proposals
 * where they do not, which is exactly the draft an operator is reviewing.
 */

interface LiveSegment {
  id: string
  speaker: string
  original_text: string
  translation: string | null
}

export function snapshotToResult(snapshot: any): ProcessResponse {
  const segments: LiveSegment[] = snapshot.segments ?? []
  const caller = segments.filter((s) => s.speaker === 'caller')

  // Join the caller's words, remembering where each segment landed so a span
  // anchored to a segment can be shifted into the joined text. Without this
  // every highlight after the first segment would point at the wrong words.
  const offsets = new Map<string, number>()
  let original = ''
  for (const segment of caller) {
    if (original) original += ' '
    offsets.set(segment.id, original.length)
    original += segment.original_text
  }

  const englishOffsets = new Map<string, number>()
  let english = ''
  for (const segment of caller) {
    const text = segment.translation ?? segment.original_text
    if (english) english += ' '
    englishOffsets.set(segment.id, english.length)
    english += text
  }

  const accepted: Record<string, any> = snapshot.accepted_record ?? {}
  const pending = (snapshot.suggestions ?? []).filter((s: any) => s.status === 'pending')

  const toEvidence = (reference: any) => {
    if (!reference) return null
    const map = reference.source === 'translated' ? englishOffsets : offsets
    const base = map.get(reference.segment_id)
    if (base === undefined || reference.start == null || reference.end == null) {
      // The quote is real but we cannot place it in the joined text. Keep the
      // words and drop the offsets rather than highlight the wrong ones.
      return {
        text: reference.text,
        start: 0,
        end: 0,
        source: reference.source === 'translated' ? 'english' : 'original',
        match: 'exact',
      }
    }
    return {
      text: reference.text,
      start: base + reference.start,
      end: base + reference.end,
      source: reference.source === 'translated' ? 'english' : 'original',
      match: 'exact',
    }
  }

  const predictions: Prediction[] = []

  for (const [field, value] of Object.entries(accepted)) {
    predictions.push({
      label: field,
      value: (value as any).value,
      // A human put this here. The number is no longer a model's claim.
      score: 1.0,
      evidence: null,
      verification_state: (value as any).edited ? 'corrected' : 'confirmed',
      conflict: false,
      evidence_unverified: false,
      probabilities: null,
      raw_probability: null,
      decided_on: 'original',
      alternative: null,
      breakdown: { source: 'accepted by the operator', revision: (value as any).revision },
    })
  }

  for (const suggestion of pending) {
    if (accepted[suggestion.field]) continue
    predictions.push({
      label: suggestion.field,
      value: suggestion.proposed_value,
      score: suggestion.support?.derived_score ?? 0,
      evidence: toEvidence(suggestion.evidence?.[0]) as any,
      verification_state: 'unverified',
      conflict: suggestion.kind === 'conflict',
      evidence_unverified: false,
      probabilities: suggestion.raw_model?.probabilities ?? null,
      raw_probability: suggestion.raw_model?.probability ?? null,
      decided_on: 'original',
      alternative: suggestion.previous_value ?? null,
      breakdown: { ...(suggestion.support ?? {}), kind: suggestion.kind },
    })
  }

  const form: Record<string, unknown> = {
    incident_type: 'unknown',
    location_text: null,
    callback_number: null,
    injuries: 'unknown',
    persons_involved: null,
    hazards: [],
    weapons_present: 'unknown',
    caller_safe: 'unknown',
  }
  for (const prediction of predictions) {
    if (prediction.label in form) form[prediction.label] = prediction.value
  }

  const flags = (snapshot.review_flags ?? []).filter((f: any) => f.status === 'open')
  const missing = ['incident_type', 'location_text', 'callback_number'].filter(
    (field) => !accepted[field],
  )

  const diagnostics = snapshot.diagnostics ?? {}

  return {
    session_id: snapshot.session_id,
    detected_language: snapshot.detected_language ?? null,
    language_probability: snapshot.language_probability ?? null,
    transcript_original: original,
    transcript_english: english,
    predictions,
    suggested_form: form as any,
    // v2 has no action enum on purpose -- the operator owns urgency. This is
    // only so the existing banner has something to say, and it describes the
    // documentation state rather than recommending an action.
    suggested_action: missing.length
      ? 'request_missing_critical_field'
      : 'request_human_confirmation',
    missing_fields: missing,
    requires_review: true,
    latency_ms: {
      asr: diagnostics.last_asr_ms ?? 0,
      translate: 0,
      decision: diagnostics.last_decision_ms ?? 0,
      tts: 0,
      server_total: diagnostics.last_total_ms ?? 0,
      network: null,
      end_to_end: null,
    },
    engine: diagnostics.engine ?? {},
    notes: [
      ...flags.map((f: any) => f.text),
      ...(snapshot.unresolved ?? []).map((u: any) => u.text),
      ...(diagnostics.notes ?? []),
    ],
    calibration_warning: diagnostics.calibration_warning ?? null,
    auto_reply: null,
  }
}

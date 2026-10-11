import { useState } from 'react'
import { speak } from '../api'
import type { ApprovedPhrase, LatencyBreakdown } from '../types'

// ---------------------------------------------------------------- latency

export function LatencyPanel({
  latency,
  engine,
  cold,
}: {
  latency: LatencyBreakdown
  engine: Record<string, string>
  cold: boolean
}) {
  const rows: Array<[string, number | null, string]> = [
    ['speech recognition', latency.asr, 'Whisper, both passes'],
    ['decision', latency.decision, 'Laya plus the rule extractor'],
    ['server total', latency.server_total, 'measured inside the handler'],
    ['network', latency.network, 'client clock minus server total'],
    ['end to end', latency.end_to_end, 'what the dispatcher waited'],
  ]
  return (
    <section className="pane">
      <header className="pane-head">
        <h3>Measured latency</h3>
        {cold && <span className="badge badge-conflict">first call — includes model load</span>}
      </header>
      <table className="latency">
        <tbody>
          {rows.map(([label, value, note]) => (
            <tr key={label}>
              <th>{label}</th>
              <td>{value == null ? '—' : Math.round(value) + ' ms'}</td>
              <td className="muted">{note}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="pane-foot muted">
        {engine.decision_model && (
          <>
            {engine.decision} / {engine.decision_model} · whisper {engine.whisper_size} on{' '}
            {engine.whisper_device} · ASR confidence {engine.asr_confidence}
          </>
        )}
      </p>
    </section>
  )
}

// ------------------------------------------------------------------ reply

export function ReplyBar({
  phrases,
  language,
  sessionId,
}: {
  phrases: ApprovedPhrase[]
  language: string | null
  sessionId: string
}) {
  const [error, setError] = useState<string | null>(null)
  const [sent, setSent] = useState<string | null>(null)

  // Offer the caller's language first, but keep every phrase reachable.
  const preferred = (language ?? 'en').slice(0, 2)
  const ordered = [...phrases].sort(
    (a, b) => Number(b.language === preferred) - Number(a.language === preferred),
  )

  const say = async (phrase: ApprovedPhrase) => {
    setError(null)
    try {
      await speak(phrase.id, sessionId)
      setSent(phrase.id)
    } catch (exception) {
      setError(String(exception instanceof Error ? exception.message : exception))
    }
  }

  return (
    <section className="pane">
      <header className="pane-head">
        <h3>Approved replies</h3>
        <span className="muted">fixed phrases only — no generated speech</span>
      </header>
      <div className="phrases">
        {ordered.map((phrase) => (
          <button
            key={phrase.id}
            className={'phrase' + (sent === phrase.id ? ' phrase-sent' : '')}
            onClick={() => say(phrase)}
            title={phrase.id}
          >
            <span className="chip">{phrase.language}</span> {phrase.text}
          </button>
        ))}
      </div>
      {error && <p className="pane-foot warn">{error}</p>}
      <p className="pane-foot muted">
        None of these promises help is on the way. The prototype has no authority to say that.
      </p>
    </section>
  )
}

// ------------------------------------------------------------- next action

const ACTION_COPY: Record<string, string> = {
  await_more_audio: 'No speech yet. Keep listening — nothing has been inferred.',
  request_language_support: 'The language is uncertain. Consider an interpreter.',
  request_missing_critical_field: 'Ask the caller for the fields marked below.',
  request_human_confirmation: 'Read the draft, correct anything wrong, then confirm.',
  escalate_to_human_now: 'A weapon or a trapped caller was indicated. Escalate now.',
  ready_for_human_submission: 'Every critical field is confirmed. Ready for you to submit.',
}

export function ActionBanner({
  action,
  prompts,
  requiresReview,
  warning,
}: {
  action: string
  prompts: string[]
  requiresReview: boolean
  warning: string | null
}) {
  const urgent = action === 'escalate_to_human_now'
  return (
    <section className={'banner' + (urgent ? ' banner-urgent' : '')}>
      <div className="banner-row">
        <strong>{action.replace(/_/g, ' ')}</strong>
        {requiresReview && <span className="badge badge-human">human review required</span>}
      </div>
      <p>{ACTION_COPY[action] ?? ''}</p>
      {prompts.length > 0 && (
        <ul className="prompts">
          {prompts.slice(0, 4).map((prompt) => (
            <li key={prompt}>{prompt}</li>
          ))}
        </ul>
      )}
      {warning && <p className="calib muted">{warning}</p>}
    </section>
  )
}

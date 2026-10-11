import { useEffect, useRef, useState } from 'react'
import { speak } from '../api'
import type { AutoReplySuggestion } from '../types'

interface Props {
  suggestion: AutoReplySuggestion | null
  sessionId: string
  action: string
}

// A deliberate pause before anything is said. It is the operator's chance to
// stop the machine talking over them, and it is long enough to actually press.
const COUNTDOWN_SECONDS = 4

/**
 * Autonomous mode: the one place the system speaks to a caller unprompted.
 *
 * Off by default, because the rest of this product is built on nothing
 * reaching the caller without a person. Switching it on narrows to exactly
 * one capability -- asking a scripted question that a dispatcher would ask
 * anyway -- and the server decides which question; this component only counts
 * down and lets you stop it.
 *
 * What the panel shows when it is NOT speaking matters as much as what it
 * says. The reason is always on screen, so "it went quiet" is never a mystery:
 * during an escalation it says so, and stays quiet.
 */
export default function AutoReplyPanel({ suggestion, sessionId, action }: Props) {
  const [enabled, setEnabled] = useState(false)
  const [countdown, setCountdown] = useState<number | null>(null)
  const [log, setLog] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  const timer = useRef<number | undefined>(undefined)
  // Which suggestion the current countdown belongs to, so a new result
  // replaces the pending question instead of racing it.
  const pending = useRef<string | null>(null)

  const phraseId = suggestion?.phrase_id ?? null
  const blocked = suggestion ? !suggestion.allowed : false

  useEffect(() => {
    window.clearInterval(timer.current)
    setCountdown(null)

    if (!enabled || !phraseId || blocked) {
      pending.current = null
      return
    }

    pending.current = phraseId
    setCountdown(COUNTDOWN_SECONDS)
    timer.current = window.setInterval(() => {
      setCountdown((current) => {
        if (current === null) return null
        if (current > 1) return current - 1

        window.clearInterval(timer.current)
        // Fire only if this is still the question that started the countdown.
        if (pending.current === phraseId) {
          speak(phraseId, sessionId)
            .then((text) => setLog((entries) => [...entries, text]))
            .catch((exception) =>
              setError(exception instanceof Error ? exception.message : String(exception)),
            )
        }
        return null
      })
    }, 1000)

    return () => window.clearInterval(timer.current)
  }, [enabled, phraseId, blocked, sessionId])

  const cancel = () => {
    window.clearInterval(timer.current)
    pending.current = null
    setCountdown(null)
  }

  return (
    <section className={'pane' + (countdown !== null ? ' pane-armed' : '')}>
      <header className="pane-head">
        <h3>Autonomous replies</h3>
        <label className="live-toggle">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => {
              setEnabled(event.target.checked)
              setError(null)
              if (!event.target.checked) cancel()
            }}
          />
          speak without asking me
        </label>
      </header>

      {!enabled && (
        <p className="muted">
          Off. Nothing is said to the caller unless you press a phrase yourself.
          {suggestion?.phrase_id && (
            <>
              {' '}
              If it were on, it would ask: <q>{suggestion.text}</q>
            </>
          )}
        </p>
      )}

      {enabled && blocked && (
        <p className="warn">
          Holding. {suggestion?.reason} — <strong>{action.replace(/_/g, ' ')}</strong>.
        </p>
      )}

      {enabled && !blocked && countdown !== null && (
        <div className="armed-row">
          <span className="countdown">{countdown}</span>
          <div>
            <p className="armed-text">
              About to say: <q>{suggestion?.text}</q>
            </p>
            <p className="muted">
              {suggestion?.reason}
              {suggestion?.target && ' · filling ' + suggestion.target.replace(/_/g, ' ')}
            </p>
          </div>
          <button className="primary" onClick={cancel}>
            Stop
          </button>
        </div>
      )}

      {enabled && !blocked && countdown === null && !phraseId && (
        <p className="muted">Nothing to ask — {suggestion?.reason}</p>
      )}

      {log.length > 0 && (
        <div className="pane-foot">
          <details open>
            <summary className="muted">Said to the caller ({log.length})</summary>
            <ol className="spoken-log">
              {log.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ol>
          </details>
        </div>
      )}

      {error && <p className="pane-foot warn">{error}</p>}

      <p className="pane-foot muted">
        Only the approved question phrases, never during an escalation, never twice,
        and never a promise that help is coming. It cannot change the form.
      </p>
    </section>
  )
}

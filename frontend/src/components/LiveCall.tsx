import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchLiveSession, listLiveSessions, reviewSuggestion } from '../api'
import type { LiveSessionSummary } from '../api'

const POLL_MS = 2000

function render(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'none found'
  return String(value).replace(/_/g, ' ')
}

/**
 * A call as it happens: what has been said, and what is waiting for review.
 *
 * Polling rather than a socket. The append response already carries the whole
 * snapshot and a two-second poll is indistinguishable from live at
 * conversational pace, so a second transport would be a second thing to debug
 * during a demo for no visible gain.
 *
 * Accepting and rejecting from here is the point. Suggestions arriving
 * mid-call that cannot be acted on until the caller hangs up would just be a
 * progress bar; the operator reviews while the conversation continues, which
 * is the thing the whole design is for.
 */
export default function LiveCall() {
  const [sessions, setSessions] = useState<LiveSessionSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [snapshot, setSnapshot] = useState<any | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)

  const refresh = useCallback(async () => {
    const listed = await listLiveSessions()
    setSessions(listed)
    // Follow the newest call unless the operator has picked one.
    const target = selected ?? listed[0]?.session_id ?? null
    if (target !== selected) setSelected(target)
    if (target) setSnapshot(await fetchLiveSession(target))
  }, [selected])

  useEffect(() => {
    refresh()
    timer.current = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(timer.current)
  }, [refresh])

  const act = async (
    suggestionId: string,
    action: 'accept' | 'reject',
  ) => {
    if (!selected || !snapshot) return
    setBusy(suggestionId)
    setNotice(null)
    const result = await reviewSuggestion(
      selected,
      suggestionId,
      action,
      snapshot.revision,
    )
    if (result.snapshot) setSnapshot(result.snapshot)
    if (!result.ok) {
      // Not an error to hide: something moved under this decision and the
      // operator should see the state it actually landed in.
      setNotice(result.conflict ?? 'that decision was out of date — re-check the queue')
    }
    setBusy(null)
  }

  if (sessions.length === 0) {
    return (
      <section className="pane">
        <header className="pane-head">
          <h3>Live call</h3>
        </header>
        <p className="muted">
          No call in progress. Ring the number with{' '}
          <code>python scripts/dial_demo.py</code> running, or append segments to a
          session on <code>/api/v2</code>, and the conversation appears here as it is
          spoken.
        </p>
      </section>
    )
  }

  const pending = (snapshot?.suggestions ?? []).filter(
    (s: any) => s.status === 'pending',
  )
  const accepted: Record<string, any> = snapshot?.accepted_record ?? {}
  const flags = (snapshot?.review_flags ?? []).filter((f: any) => f.status === 'open')

  return (
    <section className="pane">
      <header className="pane-head">
        <h3>
          <span className="live-dot" aria-hidden="true" /> Live call
        </h3>
        <select value={selected ?? ''} onChange={(e) => setSelected(e.target.value)}>
          {sessions.map((s) => (
            <option key={s.session_id} value={s.session_id}>
              {s.session_id} · {s.segments} segments
            </option>
          ))}
        </select>
      </header>

      {flags.length > 0 && (
        <div className="flag-strip">
          {flags.map((flag: any) => (
            <p key={flag.id} className="warn">
              {flag.text}
            </p>
          ))}
        </div>
      )}

      <div className="live-grid">
        <div>
          <h4 className="live-head">Conversation</h4>
          <ul className="conversation">
            {(snapshot?.segments ?? []).map((segment: any) => (
              <li key={segment.id}>
                <span className="chip">{segment.speaker}</span>
                <span>{segment.original_text}</span>
                {segment.translation && (
                  <span className="muted translation">{segment.translation}</span>
                )}
              </li>
            ))}
          </ul>
          {(snapshot?.segments ?? []).length === 0 && (
            <p className="muted">waiting for the first words…</p>
          )}
        </div>

        <div>
          <h4 className="live-head">
            To review{pending.length > 0 && ` (${pending.length})`}
          </h4>
          {pending.length === 0 && <p className="muted">nothing waiting</p>}
          <ul className="tags">
            {pending.map((suggestion: any) => (
              <li key={suggestion.id} className="tag">
                <div className="tag-row">
                  <span className="tag-label">
                    {suggestion.field.replace(/_/g, ' ')}
                  </span>
                  <span className="tag-value">{render(suggestion.proposed_value)}</span>
                  <span className={'badge band-' + kindBand(suggestion.kind)}>
                    {suggestion.kind}
                  </span>
                </div>

                {suggestion.previous_value != null && (
                  <div className="tag-alt">
                    replaces <strong>{render(suggestion.previous_value)}</strong>
                  </div>
                )}

                {suggestion.kind === 'conflict' && (
                  <div className="tag-alt">
                    {suggestion.conflict_options
                      .map((o: any) => render(o.value))
                      .join('  vs  ')}
                  </div>
                )}

                {suggestion.evidence?.[0] && (
                  <div className="tag-ev">
                    <q>{suggestion.evidence[0].text}</q>
                  </div>
                )}

                {suggestion.corroborating_segment_ids?.length > 0 && (
                  <div className="tag-ev muted">
                    said again in {suggestion.corroborating_segment_ids.length} later
                    segment(s)
                  </div>
                )}

                <div className="tag-row">
                  {suggestion.kind === 'conflict' ? (
                    <span className="muted">
                      contradiction — resolve it in the full record
                    </span>
                  ) : (
                    <button
                      className="link"
                      disabled={busy === suggestion.id}
                      onClick={() => act(suggestion.id, 'accept')}
                    >
                      accept
                    </button>
                  )}
                  <button
                    className="link"
                    disabled={busy === suggestion.id}
                    onClick={() => act(suggestion.id, 'reject')}
                  >
                    reject
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {notice && <p className="pane-foot warn">{notice}</p>}

      <div className="pane-foot">
        <h4 className="live-head">Accepted so far (revision {snapshot?.revision ?? 0})</h4>
        {Object.keys(accepted).length === 0 ? (
          <p className="muted">
            nothing accepted yet — the record stays empty until you approve something
          </p>
        ) : (
          <table className="latency">
            <tbody>
              {Object.entries(accepted).map(([name, value]: [string, any]) => (
                <tr key={name}>
                  <th>{name.replace(/_/g, ' ')}</th>
                  <td>{render(value.value)}</td>
                  <td className="muted">rev {value.revision}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}

function kindBand(kind: string): string {
  if (kind === 'conflict') return 'low'
  if (kind === 'correction') return 'mid'
  return 'high'
}

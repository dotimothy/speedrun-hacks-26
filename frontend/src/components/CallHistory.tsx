import { useEffect, useState } from 'react'
import type { ProcessResponse } from '../types'

export interface CallRecord {
  id: string
  at: string
  label: string
  language: string | null
  incidentType: string | null
  location: string | null
  action: string
  latencyMs: number | null
  engine: string
  isLocal: boolean
  // The whole response, so reopening a call does not re-run inference.
  response: ProcessResponse
}

const STORAGE_KEY = 'dispatchiq.callHistory.v1'
const LIMIT = 25

/**
 * Calls handled in this browser, newest first.
 *
 * Stored locally rather than on the server, deliberately. The v1 endpoint
 * holds no transcript after it answers, and inventing a server-side archive
 * of emergency-call content -- even synthetic -- is a bigger decision than a
 * convenience feature should make on its own. This keeps the history where
 * the operator is, and it disappears when they clear it.
 *
 * Reopening an entry restores the stored response rather than re-processing,
 * so looking back at a call costs nothing and cannot produce a different
 * answer than the one the operator actually saw.
 */
export function loadHistory(): CallRecord[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as CallRecord[]) : []
  } catch {
    return []
  }
}

export function recordCall(label: string, response: ProcessResponse): CallRecord[] {
  const find = (field: string) =>
    response.predictions.find((p) => p.label === field)?.value ?? null

  const entry: CallRecord = {
    id: response.session_id + '-' + Date.now(),
    at: new Date().toISOString(),
    label,
    language: response.detected_language,
    incidentType: (find('incident_type') as string) ?? null,
    location: (find('location_text') as string) ?? null,
    action: response.suggested_action,
    latencyMs: response.latency_ms.end_to_end ?? response.latency_ms.server_total ?? null,
    engine: response.engine?.decision ?? 'unknown',
    isLocal: response.engine?.is_local !== 'False',
    response,
  }

  const next = [entry, ...loadHistory()].slice(0, LIMIT)
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // A full quota is not worth failing a call over.
  }
  return next
}

function clockOf(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

interface Props {
  history: CallRecord[]
  onOpen: (entry: CallRecord) => void
  onClear: () => void
  activeId: string | null
}

export default function CallHistory({ history, onOpen, onClear, activeId }: Props) {
  const [serverSessions, setServerSessions] = useState<any[]>([])

  useEffect(() => {
    // Live sessions from the v2 API, when any exist. They are a different
    // kind of thing -- an ongoing call rather than a finished one -- so they
    // are listed separately rather than merged into the same list.
    fetch('/api/v2/sessions')
      .then((r) => (r.ok ? r.json() : { sessions: [] }))
      .then((d) => setServerSessions(d.sessions ?? []))
      .catch(() => undefined)
  }, [history.length])

  return (
    <section className="pane">
      <header className="pane-head">
        <h3>Call history</h3>
        {history.length > 0 && (
          <button className="link" onClick={onClear}>
            clear
          </button>
        )}
      </header>

      {history.length === 0 && serverSessions.length === 0 && (
        <p className="muted">
          No calls yet. Run a scenario or record from the microphone and it will appear
          here.
        </p>
      )}

      {history.length > 0 && (
        <ul className="history">
          {history.map((entry) => (
            <li key={entry.id}>
              <button
                className={'history-item' + (activeId === entry.id ? ' history-active' : '')}
                onClick={() => onOpen(entry)}
              >
                <span className="history-top">
                  <strong>{entry.label}</strong>
                  <span className="clock">{clockOf(entry.at)}</span>
                </span>
                <span className="history-meta">
                  {entry.incidentType && (
                    <span className="badge band-high">{entry.incidentType.replace(/_/g, ' ')}</span>
                  )}
                  {entry.language && <span className="chip">{entry.language}</span>}
                  {!entry.isLocal && <span className="badge band-mid">cloud</span>}
                  {entry.latencyMs != null && (
                    <span className="clock">{Math.round(entry.latencyMs)} ms</span>
                  )}
                </span>
                <span className="history-sub muted">
                  {entry.location || entry.action.replace(/_/g, ' ')}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {serverSessions.length > 0 && (
        <>
          <p className="pane-foot muted">Live sessions on the server</p>
          <ul className="history">
            {serverSessions.map((session) => (
              <li key={session.session_id}>
                <span className="history-item history-static">
                  <span className="history-top">
                    <strong>{session.session_id}</strong>
                    <span className="clock">{clockOf(session.created_at)}</span>
                  </span>
                  <span className="history-meta">
                    <span className="chip">{session.segments} segments</span>
                    {session.pending > 0 && (
                      <span className="badge band-mid">{session.pending} to review</span>
                    )}
                    {session.published > 0 && (
                      <span className="badge band-high">{session.published} published</span>
                    )}
                  </span>
                  {session.opening_line && (
                    <span className="history-sub muted">“{session.opening_line}”</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      <p className="pane-foot muted">
        Stored in this browser only. Reopening a call shows the saved result; it does
        not run the models again.
      </p>
    </section>
  )
}

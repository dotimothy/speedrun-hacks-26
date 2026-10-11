import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  confirmFields,
  fetchHealth,
  fetchModels,
  fetchPhrases,
  processAudio,
  processTranscript,
} from './api'
import AutoReplyPanel from './components/AutoReplyPanel'
import CallAudio from './components/CallAudio'
import CallHistory, { loadHistory, recordCall } from './components/CallHistory'
import LiveCall from './components/LiveCall'
import type { CallRecord } from './components/CallHistory'
import SettingsMenu from './components/SettingsMenu'
import IncidentFormPane from './components/IncidentFormPane'
import MicPanel from './components/MicPanel'
import PredictionList from './components/PredictionList'
import TranscriptPane from './components/TranscriptPane'
import { ActionBanner, LatencyPanel, ReplyBar } from './components/SidePanels'
import type { ApprovedPhrase, ModelInfo, Prediction, ProcessResponse } from './types'

// The committed fixtures are the demo script, the offline fallback and the
// test inputs all at once, so the console drives them directly.
const FIXTURES = [
  { id: 'kitchen_fire_en', label: 'Kitchen fire (EN)' },
  { id: 'gas_smell_no_address_en', label: 'Gas smell, no address (EN)' },
  { id: 'traffic_collision_es', label: 'Traffic collision (ES)' },
  { id: 'weapon_crime_es', label: 'Intruder with a knife (ES)' },
  { id: 'ambiguous_noise_en', label: 'Barely anything said (EN)' },
]

export default function App() {
  const [result, setResult] = useState<ProcessResponse | null>(null)
  const [models, setModels] = useState<ModelInfo | null>(null)
  const [phrases, setPhrases] = useState<ApprovedPhrase[]>([])
  const [health, setHealth] = useState<Record<string, any> | null>(null)
  const [whisperSize, setWhisperSize] = useState('small')
  const [sessionId, setSessionId] = useState('kitchen_fire_en')
  const [hovered, setHovered] = useState<Prediction | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [transcriptDraft, setTranscriptDraft] = useState('')
  const [callCount, setCallCount] = useState(0)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [engine, setEngine] = useState('laya')
  const [history, setHistory] = useState<CallRecord[]>(() => loadHistory())
  const [activeCall, setActiveCall] = useState<string | null>(null)

  // Whatever audio the current result came from, so it can be played back.
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null)
  const [audioLabel, setAudioLabel] = useState('')
  const objectUrl = useRef<string | null>(null)

  // Keep the latest whisper size available to the live callback without
  // making the callback identity change on every keystroke elsewhere.
  const sizeRef = useRef(whisperSize)
  sizeRef.current = whisperSize

  useEffect(() => {
    fetchModels()
      .then((info) => {
        setModels(info)
        setWhisperSize(info.whisper_default)
      })
      .catch(() => setError('backend not reachable — is uvicorn running on 8000?'))
    fetchPhrases().then(setPhrases).catch(() => undefined)
    fetchHealth().then(setHealth).catch(() => undefined)
    return () => {
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
    }
  }, [])

  const attachAudio = (blob: Blob, label: string) => {
    // Revoke the previous one; a long demo would otherwise leak a WAV per call.
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
    objectUrl.current = URL.createObjectURL(blob)
    setAudioUrl(objectUrl.current)
    setAudioBlob(blob)
    setAudioLabel(label)
  }

  const run = async (work: () => Promise<ProcessResponse>, label?: string) => {
    setBusy(true)
    setError(null)
    try {
      const response = await work()
      setResult(response)
      setCallCount((n) => n + 1)
      if (label) {
        const updated = recordCall(label, response)
        setHistory(updated)
        setActiveCall(updated[0]?.id ?? null)
      }
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : String(exception))
    } finally {
      setBusy(false)
    }
  }

  const playFixture = async (id: string, label: string) => {
    setSessionId(id)
    const response = await fetch('/fixtures/audio/' + id + '.wav')
    if (!response.ok) {
      setError('fixture audio missing — run: python scripts/make_fixtures.py')
      return
    }
    const wav = await response.blob()
    attachAudio(wav, label)
    await run(() => processAudio(id, wav, whisperSize), label)
  }

  // Live interim passes must not flip the UI into its busy state or the
  // dispatcher would watch the panel blink every five seconds while they are
  // still talking. Only the final pass does.
  const onMicAudio = useCallback(async (wav: Blob, final: boolean) => {
    const id = final ? 'mic-' + Date.now() : 'mic-live'
    if (final) {
      setSessionId(id)
      attachAudio(wav, 'microphone recording')
      await run(() => processAudio(id, wav, sizeRef.current), 'Microphone recording')
      return
    }
    const interim = await processAudio(id, wav, sizeRef.current)
    setResult(interim)
    setCallCount((n) => n + 1)
  }, [])

  const onConfirm = async (fields: Record<string, unknown>) => {
    const cleaned = Object.fromEntries(
      Object.entries(fields).filter(([, value]) => value !== undefined && value !== ''),
    )
    await confirmFields(sessionId, cleaned)
    // Re-run so the protection is visible: the confirmed value must survive a
    // fresh model pass that may disagree with it.
    if (result?.transcript_original) {
      await run(() => processTranscript(sessionId, result.transcript_original, cleaned))
    }
  }

  const banner = useMemo(() => {
    if (!result) return null
    const prompts = result.notes.filter(
      (note) => note.startsWith('Ask') || note.startsWith('Escalate'),
    )
    return { action: result.suggested_action, prompts }
  }, [result])

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>DispatchIQ</h1>
          <p className="muted">
            Decision support for call documentation. Synthetic scenarios only — this routes
            no calls and dispatches nothing.
          </p>
        </div>
        {health && (
          <div className="health">
            <span className="chip">
              whisper {health.whisper?.device}/{health.whisper?.compute_type}
            </span>
            <span className="chip">laya {health.laya?.ready ? health.laya?.device : 'down'}</span>
            <span className="chip">tts {health.tts?.rendered ?? 0} phrases</span>
            <span className={'chip' + (health.inference_is_local === false ? ' chip-warn' : '')}>
              {health.inference_is_local === false ? 'cloud inference on' : 'local only'}
            </span>
            <button onClick={() => setSettingsOpen(true)}>Settings</button>
          </div>
        )}
      </header>

      <section className="controls">
        <div className="control-group">
          <label>Scenario</label>
          <div className="fixture-buttons">
            {FIXTURES.map((fixture) => (
              <button
                key={fixture.id}
                disabled={busy}
                className={sessionId === fixture.id ? 'active' : ''}
                onClick={() => playFixture(fixture.id, fixture.label)}
              >
                {fixture.label}
              </button>
            ))}
          </div>
        </div>

        <div className="control-group grow">
          <label>Or paste a transcript (skips speech recognition)</label>
          <div className="row">
            <input
              type="text"
              value={transcriptDraft}
              placeholder="There is smoke coming from my kitchen..."
              onChange={(event) => setTranscriptDraft(event.target.value)}
            />
            <button
              disabled={busy || !transcriptDraft.trim()}
              onClick={() => {
                const id = 'typed-' + Date.now()
                setSessionId(id)
                setAudioUrl(null)
                setAudioBlob(null)
                return run(
                  () => processTranscript(id, transcriptDraft),
                  transcriptDraft.slice(0, 40),
                )
              }}
            >
              Process
            </button>
          </div>
        </div>
      </section>

      <div className="grid grid-intake">
        <MicPanel onAudio={onMicAudio} disabled={busy} />
        <CallAudio src={audioUrl} blob={audioBlob} label={audioLabel || 'call'} />
      </div>

      <LiveCall />

      <div className="grid grid-intake">
        <CallHistory
          history={history}
          activeId={activeCall}
          onOpen={(entry) => {
            // Restore what the operator actually saw. No model runs.
            setResult(entry.response)
            setActiveCall(entry.id)
            setAudioUrl(null)
            setAudioBlob(null)
          }}
          onClear={() => {
            localStorage.removeItem('dispatchiq.callHistory.v1')
            setHistory([])
            setActiveCall(null)
          }}
        />
      </div>

      <SettingsMenu
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        models={models}
        health={health}
        whisperSize={whisperSize}
        onWhisperSize={setWhisperSize}
        engine={engine}
        onEngine={setEngine}
      />

      {busy && <p className="working">working…</p>}
      {error && <p className="warn">{error}</p>}

      {result && banner && (
        <>
          <ActionBanner
            action={banner.action}
            prompts={banner.prompts}
            requiresReview={result.requires_review}
            warning={result.calibration_warning}
          />

          <div className="grid">
            <div className="column">
              <TranscriptPane
                title="What the caller said"
                text={result.transcript_original}
                source="original"
                highlight={hovered?.evidence ?? null}
                language={result.detected_language}
                languageProbability={result.language_probability}
              />
              {result.transcript_english !== result.transcript_original && (
                <TranscriptPane
                  title="English translation"
                  text={result.transcript_english}
                  source="english"
                  highlight={hovered?.evidence ?? null}
                />
              )}
              <PredictionList predictions={result.predictions} onHover={setHovered} />
            </div>

            <div className="column">
              <IncidentFormPane
                form={result.suggested_form}
                predictions={result.predictions}
                missing={result.missing_fields}
                onConfirm={onConfirm}
                busy={busy}
              />
              <LatencyPanel
                latency={result.latency_ms}
                engine={result.engine}
                cold={callCount <= 1}
              />
              <AutoReplyPanel
                suggestion={result.auto_reply}
                sessionId={result.session_id}
                action={result.suggested_action}
              />
              <ReplyBar
                phrases={phrases}
                language={result.detected_language}
                sessionId={result.session_id}
              />
            </div>
          </div>

          <details className="notes">
            <summary>Pipeline notes ({result.notes.length})</summary>
            <ul>
              {result.notes.map((note, index) => (
                <li key={index}>{note}</li>
              ))}
            </ul>
          </details>
        </>
      )}

      {!result && !busy && (
        <p className="muted empty">
          Pick a scenario above, or record from the microphone, to run the pipeline.
        </p>
      )}
    </div>
  )
}

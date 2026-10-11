import { useEffect, useMemo, useRef, useState } from 'react'
import {
  confirmFields,
  fetchHealth,
  fetchModels,
  fetchPhrases,
  processAudio,
  processTranscript,
} from './api'
import { Recorder } from './audio/recorder'
import IncidentFormPane from './components/IncidentFormPane'
import PredictionList from './components/PredictionList'
import TranscriptPane from './components/TranscriptPane'
import { ActionBanner, LatencyPanel, ReplyBar } from './components/SidePanels'
import type { ApprovedPhrase, ModelInfo, Prediction, ProcessResponse } from './types'

// The committed fixtures are the demo script, the offline fallback and the
// test inputs all at once, so the console drives them directly.
const FIXTURES = [
  { id: 'medical_chest_pain_en', label: 'Chest pain', desk: 'Medical' },
  { id: 'medical_not_breathing_en', label: 'Not breathing', desk: 'Medical' },
  { id: 'kitchen_fire_en', label: 'Residence fire', desk: 'Fire' },
  { id: 'gas_smell_no_address_en', label: 'Gas odor', desk: 'Fire' },
  { id: 'commercial_fire_en', label: 'Restaurant fire', desk: 'Fire' },
  { id: 'apartment_fire_es', label: 'Apartment fire', desk: 'Fire' },
  { id: 'traffic_collision_es', label: 'Traffic collision', desk: 'Other' },
  { id: 'weapon_crime_es', label: 'Intruder', desk: 'Other' },
  { id: 'ambiguous_noise_en', label: 'Unclear caller', desk: 'Other' },
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
  const [recording, setRecording] = useState(false)
  const [transcriptDraft, setTranscriptDraft] = useState('')
  const [callCount, setCallCount] = useState(0)

  const recorder = useRef(new Recorder())

  useEffect(() => {
    fetchModels()
      .then((info) => {
        setModels(info)
        setWhisperSize(info.whisper_default)
      })
      .catch(() => setError('backend not reachable — is uvicorn running on 8000?'))
    fetchPhrases().then(setPhrases).catch(() => undefined)
    fetchHealth().then(setHealth).catch(() => undefined)
  }, [])

  const run = async (work: () => Promise<ProcessResponse>) => {
    setBusy(true)
    setError(null)
    try {
      setResult(await work())
      setCallCount((n) => n + 1)
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : String(exception))
    } finally {
      setBusy(false)
    }
  }

  const playFixture = async (id: string) => {
    setSessionId(id)
    // Fetch the fixture WAV through Vite's static serving and post it, so the
    // demo exercises the real audio path rather than a transcript shortcut.
    const response = await fetch('/fixtures/audio/' + id + '.wav')
    if (!response.ok) {
      setError('fixture audio missing — run: python scripts/make_fixtures.py')
      return
    }
    const wav = await response.blob()
    await run(() => processAudio(id, wav, whisperSize))
  }

  const toggleRecording = async () => {
    if (!recording) {
      try {
        await recorder.current.start()
        setRecording(true)
      } catch {
        setError('microphone refused — getUserMedia needs localhost, not a LAN IP')
      }
      return
    }
    setRecording(false)
    const wav = await recorder.current.stop()
    const id = 'mic-' + Date.now()
    setSessionId(id)
    await run(() => processAudio(id, wav, whisperSize))
  }

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
    const prompts = result.notes.filter((note) => note.startsWith('Ask') || note.startsWith('Escalate'))
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
            <span className="chip">laya {health.laya?.ready ? 'ready' : 'down'}</span>
            <span className="chip">tts {health.tts?.rendered ?? 0} phrases</span>
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
                onClick={() => playFixture(fixture.id)}
              >
                {fixture.label}
              </button>
            ))}
          </div>
        </div>

        <div className="control-group">
          <label>Whisper model</label>
          <select value={whisperSize} onChange={(event) => setWhisperSize(event.target.value)}>
            {(models?.whisper_sizes ?? ['small']).map((size) => (
              <option key={size} value={size}>
                {size}
                {models?.whisper_loaded.includes(size) ? ' (loaded)' : ''}
              </option>
            ))}
          </select>
        </div>

        <div className="control-group">
          <label>Live</label>
          <button className={recording ? 'recording' : ''} disabled={busy} onClick={toggleRecording}>
            {recording ? 'Stop and process' : 'Record from microphone'}
          </button>
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
                return run(() => processTranscript(id, transcriptDraft))
              }}
            >
              Process
            </button>
          </div>
        </div>
      </section>

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
              <ReplyBar phrases={phrases} language={result.detected_language} sessionId={result.session_id} />
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
        <p className="muted empty">Pick a scenario above to run the pipeline.</p>
      )}
    </div>
  )
}

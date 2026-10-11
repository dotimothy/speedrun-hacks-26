import { useEffect, useRef, useState } from 'react'
import { Recorder } from '../audio/recorder'

interface Props {
  // Called with the captured WAV. `final` is false for a live interim pass.
  onAudio: (wav: Blob, final: boolean) => Promise<void>
  disabled: boolean
}

const LIVE_INTERVAL_MS = 5000
// Below this there is not enough speech for Whisper to do anything useful,
// and a sub-second clip mostly produces hallucinated filler.
const MIN_LIVE_SECONDS = 3

export default function MicPanel({ onAudio, disabled }: Props) {
  const recorder = useRef(new Recorder())
  const inFlight = useRef(false)
  const frame = useRef<number>(0)
  const timer = useRef<number | undefined>(undefined)

  const [recording, setRecording] = useState(false)
  const [live, setLive] = useState(true)
  const [level, setLevel] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const [passes, setPasses] = useState(0)
  const [error, setError] = useState<string | null>(null)

  // Animate the meter from the analyser rather than from React state updates,
  // so it stays smooth while a transcription request is in flight.
  useEffect(() => {
    if (!recording) return
    const tick = () => {
      setLevel(recorder.current.level())
      setElapsed(recorder.current.elapsed)
      frame.current = requestAnimationFrame(tick)
    }
    frame.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame.current)
  }, [recording])

  // Live mode re-sends the whole call every few seconds, so the form fills in
  // while the caller is still talking. Overlapping passes are skipped rather
  // than queued: a backlog would show the dispatcher older answers than the
  // ones already on screen.
  useEffect(() => {
    if (!recording || !live) return
    timer.current = window.setInterval(async () => {
      if (inFlight.current) return
      if (recorder.current.elapsed < MIN_LIVE_SECONDS) return
      const wav = recorder.current.snapshot()
      if (!wav) return
      inFlight.current = true
      try {
        await onAudio(wav, false)
        setPasses((n) => n + 1)
      } catch (exception) {
        setError(exception instanceof Error ? exception.message : String(exception))
      } finally {
        inFlight.current = false
      }
    }, LIVE_INTERVAL_MS)
    return () => window.clearInterval(timer.current)
  }, [recording, live, onAudio])

  const toggle = async () => {
    setError(null)
    if (!recording) {
      try {
        await recorder.current.start()
        setPasses(0)
        setRecording(true)
      } catch {
        setError(
          'Microphone refused. getUserMedia needs localhost (not a LAN IP) and permission.',
        )
      }
      return
    }

    setRecording(false)
    setLevel(0)
    const wav = await recorder.current.stop()
    inFlight.current = true
    try {
      await onAudio(wav, true)
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : String(exception))
    } finally {
      inFlight.current = false
    }
  }

  return (
    <section className="pane">
      <header className="pane-head">
        <h3>Live microphone</h3>
        <label className="live-toggle">
          <input
            type="checkbox"
            checked={live}
            disabled={recording}
            onChange={(event) => setLive(event.target.checked)}
          />
          update while speaking
        </label>
      </header>

      <div className="mic-row">
        <button
          className={'mic-button' + (recording ? ' recording' : '')}
          onClick={toggle}
          disabled={disabled && !recording}
        >
          {recording ? 'Stop and finish' : 'Start recording'}
        </button>

        <div className="meter" aria-hidden="true">
          <div className="meter-fill" style={{ width: Math.round(level * 100) + '%' }} />
        </div>

        <span className="clock">{elapsed.toFixed(1)}s</span>
      </div>

      {recording && (
        <p className="pane-foot muted">
          {live
            ? passes > 0
              ? `listening — ${passes} interim pass${passes === 1 ? '' : 'es'} so far, the form updates as you talk`
              : `listening — first pass after ${MIN_LIVE_SECONDS}s of speech`
            : 'listening — the call is processed once you stop'}
        </p>
      )}

      {!recording && (
        <p className="pane-foot muted">
          Speak a scenario yourself. Nothing is recorded to disk, and the audio is
          sent only to the local backend.
        </p>
      )}

      {error && <p className="pane-foot warn">{error}</p>}
    </section>
  )
}

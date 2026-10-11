import { useEffect, useRef, useState } from "react";
import { Recorder } from "../audio/recorder";
import { clock } from "./backend";
import {
  adaptSnapshot,
  appendUtterance,
  type Speaker,
  type Utterance,
} from "./v2";
import type { Action, Mode } from "./session";

type Props = {
  id: string;
  mode: Mode;
  onResult: (action: Action) => void;
  onActivity: (active: boolean) => void;
  onAudio: (url: string | null) => void;
};
export default function Intake({
  id,
  mode,
  onResult,
  onActivity,
  onAudio,
}: Props) {
  const recorder = useRef(new Recorder());
  const alive = useRef(true);
  const lock = useRef(false);
  const processing = useRef(false);
  const queue = useRef<Utterance[]>([]);
  const controller = useRef<AbortController | null>(null);
  const audio = useRef<string | null>(null);
  const callbacks = useRef({ onResult, onActivity, onAudio });
  callbacks.current = { onResult, onActivity, onAudio };
  const [connection, setConnection] = useState("Checking session API…");
  const [recording, setRecording] = useState(false);
  const [starting, setStarting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [speaker, setSpeaker] = useState<Speaker>("caller");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState(0);
  const [received, setReceived] = useState(0);
  const [lastResult, setLastResult] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  useEffect(() => {
    let disposed = false;
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 6000);
    fetch("/api/health", { signal: abort.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (!disposed)
          setConnection(
            data.decision_default === "openai_decisions"
              ? "OpenAI Decisions configured · Hosted inference"
              : data.mock_mode
                ? "Development API · Model/ASR readiness not verified"
                : "API connected · Configured decision engine",
          );
      })
      .catch(() => {
        if (!disposed)
          setConnection("API unavailable · Check the configured backend");
      })
      .finally(() => clearTimeout(timer));
    return () => {
      disposed = true;
      abort.abort();
      clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      controller.current?.abort();
      recorder.current.dispose();
      if (audio.current) URL.revokeObjectURL(audio.current);
      callbacks.current.onAudio(null);
      callbacks.current.onActivity(false);
    };
  }, []);
  useEffect(() => {
    onActivity(recording || starting || busy);
  }, [recording, starting, busy, onActivity]);
  useEffect(() => {
    if (!recording) return;
    const timer = setInterval(() => {
      setElapsed(recorder.current.elapsed);
      setLevel(recorder.current.level());
    }, 150);
    return () => clearInterval(timer);
  }, [recording]);
  function attach(blob: Blob) {
    if (audio.current) URL.revokeObjectURL(audio.current);
    audio.current = URL.createObjectURL(blob);
    callbacks.current.onAudio(audio.current);
  }
  async function flush() {
    if (processing.current || !alive.current) return;
    processing.current = true;
    setBusy(true);
    setError(null);
    try {
      while (queue.current.length && alive.current) {
        const item = queue.current[0];
        const abort = new AbortController();
        controller.current = abort;
        const timer = setTimeout(() => abort.abort(), 90000);
        try {
          const snapshot = await appendUtterance(id, item, abort.signal);
          if (!alive.current) return;
          callbacks.current.onResult({
            type: "server",
            snapshot: adaptSnapshot(snapshot, mode),
          });
          const pending = snapshot.suggestions.filter(
            (s) => s.status === "pending",
          ).length;
          setLastResult(
            pending
              ? `Utterance received · ${pending} proposal(s) awaiting review.`
              : "Utterance received · No pending proposals returned. You can enter missing details manually.",
          );
          queue.current.shift();
          setQueued(queue.current.length);
          setReceived((n) => n + 1);
        } finally {
          clearTimeout(timer);
        }
      }
    } catch (e) {
      if (alive.current)
        setError(
          e instanceof Error && e.name !== "AbortError"
            ? e.message
            : "Processing timed out. Input is retained; retry safely.",
        );
    } finally {
      processing.current = false;
      if (alive.current) setBusy(false);
    }
  }
  function enqueue(input: Blob | string) {
    queue.current.push({ input, clientId: crypto.randomUUID(), speaker });
    setQueued(queue.current.length);
    void flush();
  }
  async function start() {
    if (lock.current) return;
    lock.current = true;
    setStarting(true);
    setError(null);
    try {
      await recorder.current.start();
      if (alive.current) setRecording(true);
      else recorder.current.dispose();
    } catch (e) {
      if (alive.current)
        setError(e instanceof Error ? e.message : "Microphone unavailable");
    } finally {
      lock.current = false;
      if (alive.current) setStarting(false);
    }
  }
  async function stop() {
    if (lock.current) return;
    lock.current = true;
    const last = recorder.current.takeUtterance();
    const full = await recorder.current.stop();
    lock.current = false;
    if (!alive.current) return;
    setRecording(false);
    setCompleted(true);
    setLevel(0);
    attach(full);
    if (last) enqueue(last);
  }
  async function upload(file?: File) {
    if (!file || lock.current) return;
    lock.current = true;
    setStarting(true);
    setError(null);
    try {
      if (file.size > 30 * 1024 * 1024)
        throw new Error("Choose a WAV under 30 MB.");
      const bytes = new DataView(await file.slice(0, 12).arrayBuffer());
      if (
        bytes.byteLength < 12 ||
        bytes.getUint32(0) !== 0x52494646 ||
        bytes.getUint32(8) !== 0x57415645
      )
        throw new Error("Choose a RIFF/WAVE recording.");
      if (!alive.current) return;
      attach(file);
      setCompleted(true);
      enqueue(file);
    } catch (e) {
      if (alive.current)
        setError(e instanceof Error ? e.message : "Could not read recording");
    } finally {
      lock.current = false;
      if (alive.current) setStarting(false);
    }
  }
  return (
    <section className="intake" aria-label="Call input">
      <div className="intake-top">
        <span>{connection}</span>
        <span role="status">
          {busy
            ? `Processing · ${queued} queued`
            : `${received} utterances received`}
        </span>
      </div>
      <div className="speaker-control">
        <label htmlFor="speaker">This input contains</label>
        <select
          id="speaker"
          value={speaker}
          disabled={recording || starting || busy}
          onChange={(e) => setSpeaker(e.target.value as Speaker)}
        >
          <option value="caller">Caller speech</option>
          <option value="operator">Call taker speech</option>
          <option value="interpreter">Interpreter speech</option>
          <option value="unknown">Unattributed / mixed speech</option>
        </select>
        <span>
          Attribution is manual. Only caller speech drives extraction.
        </span>
      </div>
      {mode === "live" ? (
        <>
          <div className="capture-controls">
            <button
              className={recording ? "stop-button" : "primary-button"}
              disabled={starting || completed || (busy && !recording)}
              onClick={() => (recording ? void stop() : void start())}
            >
              {starting
                ? "Opening input…"
                : recording
                  ? "Stop recording"
                  : completed
                    ? "Recording complete"
                    : "Start microphone"}
            </button>
            {recording && (
              <button
                disabled={busy || !!error}
                onClick={() => {
                  const wav = recorder.current.takeUtterance();
                  if (wav) enqueue(wav);
                }}
              >
                Send utterance
              </button>
            )}
            <meter
              min={0}
              max={1}
              value={level}
              aria-label="Microphone input level"
            />
            <span className="capture-time">{clock(elapsed)}</span>
            {!recording && !completed && (
              <label className="file-input">
                Upload WAV
                <input
                  type="file"
                  accept=".wav,audio/wav"
                  disabled={starting || busy}
                  onChange={(e) => void upload(e.target.files?.[0])}
                />
              </label>
            )}
          </div>
          <p>
            Send each completed utterance while recording continues. Stop sends
            any remaining audio. Use a single-speaker synthetic recording; mixed
            speech cannot be attributed automatically.
          </p>
        </>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!draft.trim() || busy || queue.current.length) return;
            enqueue(draft.trim());
            setDraft("");
          }}
        >
          <label htmlFor="transcript-input">New utterance</label>
          <textarea
            id="transcript-input"
            rows={3}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Enter only the next statement, not the full conversation."
          />
          <div className="transcript-submit">
            <p>
              The server retains earlier utterances. Text input bypasses speech
              recognition and translation.
            </p>
            <button
              className="primary-button"
              disabled={busy || !!queued || !draft.trim()}
            >
              Send utterance
            </button>
          </div>
        </form>
      )}
      {lastResult && <p role="status">{lastResult}</p>}
      {error && (
        <div className="intake-error" role="alert">
          <p>
            {error} {queued > 0 && `${queued} unsent utterance(s) retained.`}
          </p>
          {queued > 0 && (
            <button disabled={busy} onClick={() => void flush()}>
              Retry retained input
            </button>
          )}
        </div>
      )}
    </section>
  );
}

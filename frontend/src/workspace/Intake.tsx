import { useEffect, useRef, useState } from "react";
import { Recorder } from "../audio/recorder";
import { clock, requestSnapshot } from "./backend";
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
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const inFlight = useRef<Promise<void> | null>(null);
  const objectURL = useRef<string | null>(null);
  const lastInput = useRef<Blob | string | null>(null);
  const callbacks = useRef({ onResult, onActivity, onAudio });
  callbacks.current = { onResult, onActivity, onAudio };
  const [recording, setRecording] = useState(false);
  const [starting, setStarting] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState("Checking backend…");
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [draft, setDraft] = useState("");
  const [updates, setUpdates] = useState(0);
  const [fileName, setFileName] = useState("");
  const [readingFile, setReadingFile] = useState(false);
  const captureLock = useRef(false);
  const busyRef = useRef(false);

  useEffect(() => {
    alive.current = true;
    let disposed = false;
    const healthController = new AbortController();
    const timeout = setTimeout(() => healthController.abort(), 6000);
    fetch("/api/health", { signal: healthController.signal })
      .then(async (r) => {
        if (!r.ok) throw new Error();
        const data = await r.json();
        if (!disposed)
          setHealth(
            data.mock_mode
              ? "Backend in mock mode"
              : data.laya?.ready
                ? "Backend connected"
                : "Backend connected · extraction model unavailable",
          );
      })
      .catch(() => {
        if (!disposed)
          setHealth("Backend unavailable · check the configured API");
      })
      .finally(() => clearTimeout(timeout));
    return () => {
      disposed = true;
      alive.current = false;
      healthController.abort();
      clearTimeout(timeout);
      controller.current?.abort();
      recorder.current.dispose();
      if (objectURL.current) URL.revokeObjectURL(objectURL.current);
      callbacks.current.onActivity(false);
      callbacks.current.onAudio(null);
    };
  }, []);

  function attachAudio(blob: Blob) {
    if (objectURL.current) URL.revokeObjectURL(objectURL.current);
    objectURL.current = URL.createObjectURL(blob);
    callbacks.current.onAudio(objectURL.current);
  }
  async function perform(input: Blob | string, at: string) {
    lastInput.current = input;
    const request = ++sequence.current;
    const abort = new AbortController();
    controller.current = abort;
    const timeout = setTimeout(() => abort.abort(), 90000);
    setBusy(true);
    setError(null);
    try {
      const result = await requestSnapshot(id, input, abort.signal);
      if (alive.current) {
        callbacks.current.onResult({
          type: "result",
          id,
          sequence: request,
          at,
          response: result,
        });
        setUpdates((n) => n + 1);
        setHealth("Backend connected");
      }
    } catch (e) {
      if (alive.current)
        setError(
          abort.signal.aborted
            ? "Processing timed out. Retry the captured input."
            : e instanceof Error
              ? e.message
              : "Processing failed. Your accepted record is unchanged.",
        );
    } finally {
      clearTimeout(timeout);
      if (alive.current) setBusy(false);
    }
  }
  async function send(input: Blob | string, at: string) {
    if (busyRef.current || !alive.current) return;
    busyRef.current = true;
    const task = perform(input, at);
    inFlight.current = task;
    try {
      await task;
    } finally {
      busyRef.current = false;
      inFlight.current = null;
    }
  }
  useEffect(() => {
    if (!recording) return;
    const meter = setInterval(() => {
      setElapsed(recorder.current.elapsed);
      setLevel(recorder.current.level());
    }, 150);
    const poll = setInterval(() => {
      if (busyRef.current) return;
      const wav = recorder.current.snapshot();
      if (wav) void send(wav, clock(recorder.current.elapsed));
    }, 5000);
    return () => {
      clearInterval(meter);
      clearInterval(poll);
    };
  }, [recording]);
  async function start() {
    if (captureLock.current || busyRef.current) return;
    captureLock.current = true;
    setStarting(true);
    setError(null);
    callbacks.current.onActivity(true);
    try {
      await recorder.current.start();
      if (!alive.current) {
        recorder.current.dispose();
        return;
      }
      setRecording(true);
    } catch (e) {
      if (alive.current) {
        setError(e instanceof Error ? e.message : "Microphone unavailable.");
        callbacks.current.onActivity(false);
      }
    } finally {
      captureLock.current = false;
      if (alive.current) setStarting(false);
    }
  }
  async function stop() {
    if (captureLock.current) return;
    captureLock.current = true;
    const at = clock(recorder.current.elapsed);
    setRecording(false);
    setFinishing(true);
    setLevel(0);
    try {
      const wav = await recorder.current.stop();
      if (!alive.current) return;
      attachAudio(wav);
      // Wait for the existing pass. A final response can never race an older pass.
      await inFlight.current;
      if (!alive.current) return;
      await send(wav, at);
    } finally {
      captureLock.current = false;
      if (alive.current) {
        setFinishing(false);
        setCompleted(true);
        callbacks.current.onActivity(false);
      }
    }
  }
  async function upload(file: File | undefined) {
    if (!file || captureLock.current || busyRef.current) return;
    captureLock.current = true;
    setReadingFile(true);
    setError(null);
    callbacks.current.onActivity(true);
    try {
      if (file.size > 30 * 1024 * 1024)
        throw new Error("Choose a WAV under 30 MB for this prototype.");
      const signature = new DataView(await file.slice(0, 12).arrayBuffer());
      if (
        signature.byteLength < 12 ||
        signature.getUint32(0) !== 0x52494646 ||
        signature.getUint32(8) !== 0x57415645
      )
        throw new Error("This file is not a RIFF/WAVE recording.");
      if (!alive.current) return;
      setFileName(file.name);
      attachAudio(file);
      setCompleted(true);
      await send(file, "Full recording");
    } catch (error) {
      if (alive.current)
        setError(
          error instanceof Error
            ? error.message
            : "Could not read this recording.",
        );
    } finally {
      captureLock.current = false;
      if (alive.current) {
        setReadingFile(false);
        callbacks.current.onActivity(false);
      }
    }
  }
  return (
    <section className="intake" aria-label="Call input">
      <div className="intake-top">
        <span className="backend-status">{health}</span>
        <span>
          {busy ? "Processing snapshot…" : `${updates} snapshots received`}
        </span>
      </div>
      {mode === "live" ? (
        <>
          <div className="capture-controls">
            <button
              className={recording ? "stop-button" : "primary-button"}
              onClick={() => (recording ? void stop() : void start())}
              disabled={
                starting ||
                readingFile ||
                finishing ||
                completed ||
                (busy && !recording)
              }
            >
              {starting
                ? "Requesting microphone…"
                : finishing
                  ? "Finishing call…"
                  : recording
                    ? "Stop recording"
                    : completed
                      ? "Recording complete"
                      : "Start microphone"}
            </button>
            <meter
              min={0}
              max={1}
              value={level}
              aria-label="Microphone input level"
            />
            <span className="capture-time">{clock(elapsed)}</span>
            {!recording && !starting && !finishing && !completed && (
              <label className="file-input">
                Upload WAV
                <input
                  type="file"
                  accept=".wav,audio/wav"
                  disabled={busy || readingFile}
                  onChange={(e) => void upload(e.target.files?.[0])}
                />
              </label>
            )}
            {fileName && <span className="file-name">{fileName}</span>}
          </div>
          <p>
            {completed
              ? "Recording stopped. Review the record or start a new session."
              : "Microphone audio is sent to the configured backend every five seconds. Use a synthetic call."}{" "}
            <span>
              Browser recording stays in memory; backend processing uses a
              temporary audio file.
            </span>
          </p>
        </>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (busyRef.current || !draft.trim()) return;
            callbacks.current.onActivity(true);
            void send(draft, `Update ${sequence.current + 1}`).finally(
              () => alive.current && callbacks.current.onActivity(false),
            );
          }}
        >
          <label htmlFor="transcript-input">Full conversation so far</label>
          <textarea
            id="transcript-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Paste a synthetic transcript. Append new statements or correct the text, then process again."
            rows={3}
          />
          <div className="transcript-submit">
            <p>
              This input bypasses speech recognition and translation. Keep the
              full conversation for context.
            </p>
            <button className="primary-button" disabled={busy || !draft.trim()}>
              Process transcript
            </button>
          </div>
        </form>
      )}
      {error && (
        <div className="intake-error" role="alert">
          <p>{error}</p>
          {lastInput.current && (
            <button
              disabled={
                busy || readingFile || starting || recording || finishing
              }
              onClick={() => {
                if (busyRef.current) return;
                callbacks.current.onActivity(true);
                void send(
                  lastInput.current!,
                  mode === "live"
                    ? clock(elapsed)
                    : `Retry ${sequence.current + 1}`,
                ).finally(() => {
                  if (alive.current) callbacks.current.onActivity(false);
                });
              }}
            >
              Retry last input
            </button>
          )}
        </div>
      )}
    </section>
  );
}

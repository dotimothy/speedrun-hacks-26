import { useEffect, useReducer, useRef, useState } from "react";
import {
  fields,
  initialSession,
  labels,
  fixtureSegments,
  unresolvedItems,
  type Mode,
  type Action,
  transition,
  type Suggestion,
} from "./workspace/session";
import Intake from "./workspace/Intake";
import ScenarioDecisions from "./components/ScenarioDecisions";
import {
  adaptSnapshot,
  wireFields,
  ApiConflict,
  ensureSession,
  readSession,
  reviewOperation,
  type Snapshot,
} from "./workspace/v2";
import { useTranscriptNavigation } from "./workspace/useTranscriptNavigation";
import { useFeedback } from "./workspace/useFeedback";

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, string> = {
    call: "M7 3H4a1 1 0 0 0-1 1c0 9 8 17 17 17a1 1 0 0 0 1-1v-3l-5-2-2 2a15 15 0 0 1-7-7l2-2-2-5Z",
    arrow: "M4 12h15m-6-6 6 6-6 6",
    check: "m5 12 4 4L19 6",
    pin: "M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0ZM9 10a3 3 0 1 0 6 0 3 3 0 0 0-6 0",
    wave: "M3 9v6m4-10v14m5-17v20m5-17v14m4-10v6",
    back: "M19 12H4m6-6-6 6 6 6",
    reset: "M4 9a8 8 0 1 1 0 7M4 3v6h6",
    file: "M6 3h8l4 4v14H6ZM14 3v5h4M9 12h6m-6 4h6",
    link: "M9 15l6-6m-5-3 2-2a5 5 0 0 1 7 7l-2 2M7 11l-2 2a5 5 0 0 0 7 7l2-2",
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name] ?? paths.file} />
    </svg>
  );
}
export default function App() {
  const [session, localDispatch] = useReducer(transition, undefined, () =>
    initialSession(
      new URLSearchParams(location.search).get("input") === "transcript"
        ? "transcript"
        : "live",
      `call-${crypto.randomUUID()}`,
    ),
  );
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const remoteLock = useRef(false);
  const remoteRetry = useRef<(() => Promise<Snapshot>) | null>(null);
  const currentSession = useRef(session);
  currentSession.current = session;
  async function runRemote(task: () => Promise<Snapshot>): Promise<boolean> {
    if (remoteLock.current) return false;
    const id = session.id;
    remoteLock.current = true;
    remoteRetry.current = task;
    setRemoteBusy(true);
    setRemoteError(null);
    try {
      const snapshot = await task();
      if (currentSession.current.id !== id) return false;
      localDispatch({
        type: "server",
        snapshot: adaptSnapshot(snapshot, session.mode),
      });
      remoteRetry.current = null;
      return true;
    } catch (e) {
      if (currentSession.current.id !== id) return false;
      if (e instanceof ApiConflict) {
        localDispatch({
          type: "server",
          snapshot: adaptSnapshot(e.snapshot, session.mode),
        });
        remoteRetry.current = null;
      }
      setRemoteError(
        e instanceof Error
          ? e.message
          : "Could not save the action. Your draft is preserved.",
      );
      return false;
    } finally {
      remoteLock.current = false;
      setRemoteBusy(false);
    }
  }
  async function dispatch(action: Action): Promise<boolean> {
    if (
      session.mode === "demo" ||
      ["reset", "server", "next"].includes(action.type)
    ) {
      localDispatch(action);
      return true;
    }
    return runRemote(reviewOperation(session, action));
  }
  const [selected, setSelected] = useState<number | null>(null);
  const [sourceOpen, setSourceOpen] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [translation, setTranslation] = useState(true);
  const [view, setView] = useState<"operator" | "receiving">("operator");
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  // --- inbound calls -------------------------------------------------
  //
  // A session this workspace did not create is a call that arrived some
  // other way -- today that means the phone. Without this the workspace sits
  // on the empty session it made at mount while a real call fills a
  // different one on the server, and the operator watches a blank screen
  // through the whole conversation.
  const ownSessions = useRef<Set<string>>(new Set());
  const attached = useRef(false);
  const [inbound, setInbound] = useState<string | null>(null);

  async function adopt(id: string) {
    ownSessions.current.add(id);
    attached.current = true;
    setInbound(null);
    localDispatch({ type: "reset", mode: currentSession.current.mode, id });
    try {
      const snapshot = await ensureSession(id, new AbortController().signal);
      localDispatch({
        type: "server",
        snapshot: adaptSnapshot(snapshot, currentSession.current.mode),
      });
      setNotice(`Call ${id} is live. Updates appear as the caller speaks.`);
    } catch {
      // The poll comes round again in two seconds.
    }
  }

  useEffect(() => {
    ownSessions.current.add(session.id);
    let cancelled = false;
    const controller = new AbortController();

    const tick = async () => {
      // Never fight an operator's own action for the same session.
      if (remoteLock.current) return;
      try {
        if (attached.current) {
          // readSession, not ensureSession: if the call has been cleared on
          // the server, this desk lets go of it rather than recreating an
          // empty session somebody deliberately deleted.
          const snapshot = await readSession(
            currentSession.current.id,
            controller.signal,
          );
          if (cancelled) return;
          if (snapshot === null) attached.current = false;
          else
            localDispatch({
              type: "server",
              snapshot: adaptSnapshot(snapshot, currentSession.current.mode),
            });
        }

        const response = await fetch("/api/v2/sessions", {
          signal: controller.signal,
        });
        if (!response.ok || cancelled) return;
        const listed = (await response.json()).sessions ?? [];
        // A connected phone call counts even before its first word -- that is
        // the moment an operator wants to be looking at it. A session with no
        // segments and no live call is a leftover, and taking one of those
        // over would be worse than showing nothing.
        const call = listed.find(
          (entry: { session_id: string; segments: number; live?: boolean }) =>
            (entry.live || entry.segments > 0) &&
            !ownSessions.current.has(entry.session_id),
        );
        if (cancelled) return;
        if (!call) {
          setInbound(null);
          return;
        }
        // Take the call outright when there is nothing to lose. Offer it
        // when there is: a ringing phone should not wipe a record somebody
        // is part-way through reviewing.
        const idle =
          currentSession.current.segments.length === 0 &&
          !currentSession.current.published;
        if (idle) await adopt(call.session_id);
        else setInbound(call.session_id);
      } catch {
        // A failed poll is not worth putting on screen; it retries.
      }
    };

    tick();
    const timer = window.setInterval(tick, 2000);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [notice, setNotice] = useState(
    "Ready for a new call. Accepted information is saved only after your review.",
  );
  const transcript = useRef<HTMLDivElement>(null);
  const desk = useRef<HTMLDivElement>(null);
  const editTrigger = useRef<string | null>(null);
  const restoreEditFocus = useRef(false);
  const navigation = useTranscriptNavigation(
    transcript,
    session.id,
    session.segments.length,
    selected,
    view,
    translation,
  );
  function cancelEdit() {
    restoreEditFocus.current = true;
    setEditing(null);
  }
  useEffect(() => {
    if (editing !== null || !restoreEditFocus.current) return;
    restoreEditFocus.current = false;
    const trigger = editTrigger.current
      ? document.getElementById(editTrigger.current)
      : null;
    (trigger ?? document.getElementById("review-heading"))?.focus();
  }, [editing]);
  const reviewFocus = useRef<string | null>(null);
  const [copied, setCopied] = useState(false);
  useFeedback(desk, session, view, sourceOpen);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(timer);
  }, [copied]);
  useEffect(() => {
    setCopied(false);
  }, [session.id, session.published]);
  useEffect(() => {
    if (!reviewFocus.current) return;
    const target = document.getElementById(reviewFocus.current);
    target?.focus({ preventScroll: true });
    reviewFocus.current = null;
  }, [session.suggestions]);
  const [activeCapture, setActiveCapture] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const audioPlayer = useRef<HTMLAudioElement>(null);
  const segments = session.segments;
  const demo = session.mode === "demo";
  const visibleFields = demo
    ? fields.filter((field, index) => index < 5 || session.record[field] || session.suggestions.some(s => s.field === field))
    : fields.filter(field => !session.server?.relevant_fields || session.server.relevant_fields.includes(wireFields[field]));
  const languageName = (() => {
    if (!session.language) return "Not identified";
    try {
      return (
        new Intl.DisplayNames(["en"], { type: "language" }).of(
          session.language,
        ) || session.language
      );
    } catch {
      return session.language;
    }
  })();
  const currentUnresolved = unresolvedItems(session);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (
        activeCapture ||
        remoteBusy ||
        Object.keys(session.record).length ||
        note.trim()
      ) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [activeCapture, remoteBusy, session.record, note]);
  const pending = session.suggestions.filter((s) => s.status === "pending");
  const retainedDraft = session.suggestions.find(
    (s) => s.id === editing && s.status !== "pending",
  );
  const reviewItems = retainedDraft ? [retainedDraft, ...pending] : pending;
  const done = !demo || session.step === fixtureSegments.length - 1;
  const unpublished =
    !session.published ||
    fields.some(
      (field) => session.record[field] !== session.published?.record[field],
    ) ||
    (demo
      ? note.trim() !== session.published.note ||
        JSON.stringify(currentUnresolved) !==
          JSON.stringify(session.published.unresolved)
      : (!!note.trim() && note.trim() !== session.published.note) ||
        (!!session.server &&
          (session.revision !==
            session.server.published[session.server.published.length - 1]
              ?.revision ||
            JSON.stringify([
              ...session.server.unresolved.map((x) => x.text),
              ...session.server.review_flags
                .filter((f) => f.status === "open")
                .map((f) => f.text),
            ]) !== JSON.stringify(session.published.unresolved))));
  const acceptedCount = Object.keys(session.record).length;
  useEffect(() => {
    if (!playing || done) return;
    const timer = setTimeout(() => dispatch({ type: "next" }), 6500);
    return () => clearTimeout(timer);
  }, [playing, done, session.step]);
  useEffect(() => {
    if (done) setPlaying(false);
  }, [done]);
  function evidence(source: number, id: string) {
    const opening = sourceOpen !== id;
    setSourceOpen(opening ? id : null);
    setSelected(opening ? source : null);
  }
  async function review(
    s: Suggestion,
    decision: "accept" | "reject",
    value?: string,
  ) {
    if (desk.current?.dataset.input === "keyboard") {
      const index = pending.findIndex((item) => item.id === s.id);
      reviewFocus.current =
        pending[index + 1]?.id ?? pending[index - 1]?.id ?? "review-empty";
    }
    if (s.status !== "pending" && decision === "accept") {
      if (!value?.trim()) return;
      if (!(await dispatch({ type: "manual", field: s.field, value }))) return;
    } else if (!(await dispatch({ type: "review", id: s.id, decision, value })))
      return;
    setEditing(null);
    setSourceOpen(null);
    setSelected(null);
    setNotice(
      `${labels[s.field]} ${decision === "accept" ? "accepted into the record" : "dismissed; record unchanged"}.`,
    );
  }
  function reset(mode: Mode = session.mode) {
    if (activeCapture || remoteLock.current) return;
    if (
      (session.history.length ||
        note.trim() ||
        (session.mode !== "demo" && segments.length)) &&
      !window.confirm(
        demo
          ? "Start a new session? This clears the current rehearsal."
          : "Start a new session? This leaves the current call and clears its browser recording. Saved server records remain until the backend restarts.",
      )
    )
      return;
    dispatch({
      type: "reset",
      mode,
      id: mode === "demo" ? "DQ–1024" : `call-${crypto.randomUUID()}`,
    });
    setRemoteError(null);
    remoteRetry.current = null;
    setPlaying(false);
    setView("operator");
    setSelected(null);
    setSourceOpen(null);
    setNote("");
    setAudioUrl(null);
    setEditing(null);
    setNotice(
      mode === "demo"
        ? "Synthetic scenario loaded."
        : "New session ready. Reviews and publication will be saved to the v2 API.",
    );
  }
  async function copyPublication() {
    if (!session.published) return;
    const publication = session.published;
    const text = [
      `Incident ${session.id} · published v${publication.version}`,
      ...fields.map(
        (f) => `${labels[f]}: ${publication.record[f] || "Not recorded"}`,
      ),
      publication.note && `Operator note: ${publication.note}`,
      "Unresolved:",
      ...publication.unresolved,
    ]
      .filter(Boolean)
      .join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setNotice("Published brief copied.");
    } catch {
      setNotice("Clipboard unavailable. Use Download record instead.");
    }
  }
  function downloadPublication() {
    if (!session.published) return;
    const blob = new Blob(
      [
        JSON.stringify(
          { session_id: session.id, ...session.published },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${session.id}-v${session.published.version}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function publish() {
    if (!(await dispatch({ type: "publish", note }))) return;
    setNotice(
      demo
        ? "Reviewed record published to the local receiving preview."
        : "Reviewed record published to the server receiving inbox. No units were dispatched.",
    );
  }
  return (
    <div
      className="desk"
      ref={desk}
      data-input="keyboard"
      onPointerDownCapture={() => {
        if (desk.current) desk.current.dataset.input = "pointer";
      }}
      onKeyDownCapture={() => {
        if (desk.current) desk.current.dataset.input = "keyboard";
      }}
    >
      <header className="masthead">
        <a className="brand" href="/" aria-label="DispatchIQ home">
          <span className="brand-mark">
            <Icon name="wave" size={20} />
          </span>
          dispatch<span>iq</span>
          <span className="brand-divider" />
        </a>
        <span className="workspace-name">Call-taking workspace</span>
        <div className="masthead-right">
          <span className="demo-label">SIMULATED CAD</span>
          <span>
            Operator desk <b>01</b>
          </span>
        </div>
      </header>
      {inbound && (
        <div className="inbound-strip" role="alert">
          <span>
            <span className="inbound-dot" />
            Incoming call <strong>{inbound.slice(-8).toUpperCase()}</strong> is
            being transcribed. This desk has an open record.
          </span>
          <div>
            <button onClick={() => void adopt(inbound)}>Take the call</button>
            <button
              className="ghost"
              onClick={() => {
                ownSessions.current.add(inbound);
                setInbound(null);
              }}
            >
              Leave for another desk
            </button>
          </div>
        </div>
      )}
      <div className="session-toolbar">
        <label htmlFor="input-mode">Input</label>
        <select
          id="input-mode"
          value={session.mode}
          disabled={activeCapture || remoteBusy}
          onChange={(e) => reset(e.target.value as Mode)}
        >
          <option value="demo">Scripted rehearsal</option>
          <option value="live">Microphone / WAV</option>
          <option value="transcript">Transcript</option>
        </select>
        <span>
          {demo
            ? "Rehearsal state stays in this browser"
            : "Reviews saved to the session API"}
        </span>
        <button disabled={activeCapture || remoteBusy} onClick={() => reset()}>
          New session
        </button>
      </div>
      {demo && (
        <div className="demo-strip">
          <span>
            <span className="demo-dot" />
            Synthetic scenario <strong>Kitchen smoke · Spanish</strong>
          </span>
          <div>
            <span className="scenario-progress">
              {session.step + 1} / {fixtureSegments.length} segments
            </span>
            <button onClick={() => setPlaying((p) => !p)} disabled={done}>
              {playing ? "Pause playback" : "Play scenario"}
            </button>
            <button onClick={() => dispatch({ type: "next" })} disabled={done}>
              Next segment <Icon name="arrow" size={14} />
            </button>
            <button
              className="icon-button"
              onClick={() => reset()}
              aria-label="Reset demo"
            >
              <Icon name="reset" size={15} />
            </button>
          </div>
        </div>
      )}
      <main>
        <section className="call-heading">
          <div className="call-title">
            <div>
              <h1>
                {demo
                  ? "Call DQ–1024"
                  : `Call ${session.id.slice(-8).toUpperCase()}`}
                <span className="draft-status">Incident workspace</span>
              </h1>
            </div>
          </div>
          <div className="call-meta">
            <span className="status-dot" />
            <span>
              {demo
                ? playing
                  ? "Scenario playing"
                  : done
                    ? "Scenario complete"
                    : "Scenario paused"
                : activeCapture
                  ? "Call input active"
                  : segments.length
                    ? "Reviewing call"
                    : "Awaiting input"}
            </span>
            <span className="elapsed">
              {segments[session.step]?.at || "00:00"}
            </span>
          </div>
        </section>
        <div className="incident-context">
          <div>
            <span>Accepted location</span>
            <strong>{session.record.location || "Awaiting review"}</strong>
          </div>
          <div>
            <span>Caller language</span>
            <strong>
              {languageName}
              <span className="context-secondary">
                {demo
                  ? " / illustrative translation"
                  : session.mode === "transcript"
                    ? " / text input"
                    : " / machine translation"}
              </span>
            </strong>
          </div>
          <div>
            <span>Record status</span>
            <strong>
              {session.published
                ? unpublished
                  ? "Unpublished changes"
                  : `Version ${session.published.version} shared`
                : "Not published"}
            </strong>
          </div>
        </div>
        {!demo && (
          <Intake
            key={session.id}
            id={session.id}
            mode={session.mode}
            onResult={dispatch}
            onActivity={setActiveCapture}
            onAudio={setAudioUrl}
          />
        )}
        {remoteError && (
          <div className="intake-error" role="alert">
            <p>{remoteError}</p>
            {remoteRetry.current && (
              <button
                disabled={remoteBusy}
                onClick={async () => {
                  if (
                    remoteRetry.current &&
                    (await runRemote(remoteRetry.current))
                  ) {
                    setEditing(null);
                    setNotice("Action saved to the session.");
                  }
                }}
              >
                Retry save
              </button>
            )}
          </div>
        )}
        {remoteBusy && (
          <p className="server-status" role="status">
            Saving to session…
          </p>
        )}
        {session.server && (
          <div className="server-status">
            Server revision {session.revision} · Engine:{" "}
            {session.server.diagnostics.engine.decision || "No inference yet"}
            <details>
              <summary>Processing details</summary>
              <p>{session.server.diagnostics.engine.decision_model}</p>
              {session.server.diagnostics.notes.map((n, i) => (
                <p key={i}>{n}</p>
              ))}
            </details>
            {session.server.review_flags.filter((f) => f.status === "open")
              .length > 0 && (
              <details>
                <summary>Review flags</summary>
                <ul>
                  {session.server.review_flags
                    .filter((f) => f.status === "open")
                    .map((f) => (
                      <li key={f.id}>{f.text}</li>
                    ))}
                </ul>
              </details>
            )}
          </div>
        )}
        <nav className="workspace-tabs" aria-label="Workspace views">
          <button
            aria-current={view === "operator" ? "page" : undefined}
            onClick={() => setView("operator")}
          >
            Call record
          </button>
          <button
            aria-current={view === "receiving" ? "page" : undefined}
            onClick={() => setView("receiving")}
          >
            Receiving desk{" "}
            {session.published && (
              <span className="tab-count">v{session.published.version}</span>
            )}
          </button>
        </nav>
        {view === "operator" ? (
          <div className="workspace-grid">
            <section className="conversation">
              <header className="panel-heading">
                <div>
                  <h2>Conversation</h2>
                </div>
              </header>
              <div className="language-bar">
                <span>
                  {languageName}{" "}
                  {session.mode !== "transcript" && (
                    <>
                      <Icon name="arrow" size={13} />
                      English
                    </>
                  )}
                </span>
                <button
                  aria-pressed={translation}
                  onClick={() => setTranslation((t) => !t)}
                >
                  {translation ? "Hide translation" : "Show translation"}
                </button>
              </div>
              <div className="transcript-navigation">
                <span role="status">
                  {selected !== null
                    ? "Inspecting source"
                    : navigation.following
                      ? "Following latest"
                      : "Reading earlier information"}
                </span>
                <button
                  disabled={navigation.following}
                  onClick={() => {
                    setSelected(null);
                    setSourceOpen(null);
                    navigation.followLatest();
                  }}
                >
                  {navigation.unread > 0
                    ? `${navigation.unread} new · Jump to latest`
                    : "Jump to latest"}
                </button>
              </div>
              <div
                className="transcript-scroll"
                ref={transcript}
                onScroll={navigation.onScroll}
                tabIndex={0}
                role="region"
                aria-label="Conversation transcript"
              >
                {!segments.length && (
                  <div className="transcript-empty">
                    <h3>No transcript yet</h3>
                    <p>
                      {session.mode === "live"
                        ? "Start the microphone or upload a WAV. New utterances will appear here."
                        : "Process a transcript above to begin reviewing suggestions."}
                    </p>
                  </div>
                )}
                {segments.map((s) => (
                  <article
                    key={s.id}
                    data-segment={s.id}
                    className={`utterance ${s.speaker === "Call taker" ? "operator" : "caller"} ${selected === s.id ? "selected" : ""}`}
                  >
                    <div className="utterance-meta">
                      <span>
                        {s.snapshot ? `Snapshot ${s.id + 1}` : s.speaker}
                      </span>
                      <time>{s.at}</time>
                    </div>
                    {s.snapshot && (
                      <div className="snapshot-context">
                        {s.speaker} · Full conversation so far
                      </div>
                    )}
                    <p
                      lang={
                        s.language ||
                        (demo && s.speaker === "Caller" ? "es" : undefined)
                      }
                    >
                      {s.original}
                    </p>
                    {translation && s.original !== s.english && (
                      <div className="translation">
                        <span>EN</span>
                        <p>{s.english}</p>
                      </div>
                    )}
                    {selected === s.id && s.speaker !== "Call taker" && (
                      <span className="source-indicator">
                        <Icon name="link" size={12} />
                        Source for selected suggestion
                      </span>
                    )}
                  </article>
                ))}
                {demo && !done && (
                  <div className="next-line">
                    <span>•••</span>
                    {playing
                      ? "Next statement arriving…"
                      : "Continue the scenario to reveal new details."}
                  </div>
                )}
              </div>
              <footer className="conversation-foot">
                <span className="small-dot" />
                {demo
                  ? "Scripted text playback · No live audio"
                  : session.mode === "transcript"
                    ? "Text input · No audio translation"
                    : "Utterances · Speaker assigned at input"}
                <div>
                  {demo
                    ? "Translation is illustrative and not interpreter-verified."
                    : "Review source accuracy before accepting. Audio timestamps are not provided by this API."}
                </div>
                {audioUrl && (
                  <div className="recording-playback">
                    <label>Full recording playback</label>
                    <audio
                      ref={audioPlayer}
                      controls
                      src={audioUrl}
                      preload="metadata"
                    />
                  </div>
                )}
              </footer>
            </section>
            <section className="record-panel">
              <header className="panel-heading">
                <div>
                  <h2>Accepted record</h2>
                </div>
                <span className="quiet-label">
                  {acceptedCount}/{visibleFields.length} recorded
                </span>
              </header>

              <ScenarioDecisions focus={session.server?.decision_focus} />
              {!!session.server?.follow_up_questions?.length && (
                <details className="follow-up-plan" open>
                  <summary>Suggested follow-up questions ({session.server.follow_up_questions.length})</summary>
                  <p>Based on what has been heard so far. Ask one at a time; review conflicting answers.</p>
                  <ol>
                    {session.server.follow_up_questions.map(question => (
                      <li key={question.field}>
                        <span>{question.text}</span>
                        <small>{question.reason}</small>
                      </li>
                    ))}
                  </ol>
                </details>
              )}
              <div className="record-fields">
                {visibleFields.map((field, index) => {
                  const change = pending.find((s) => s.field === field);
                  return (
                    <div
                      className={`record-field ${index === 0 ? "location-field" : ""}`}
                      key={field}
                      data-record-field={field}
                    >
                      <div className="field-label">
                        <span>{labels[field]}</span>
                        {session.record[field] && (
                          <span className="accepted-label">
                            <Icon name="check" size={12} />
                            Accepted
                          </span>
                        )}
                      </div>
                      {editing === `record-${field}` ? (
                        <form
                          className="manual-edit"
                          onKeyDown={(e) => {
                            if (e.key === "Escape") {
                              e.preventDefault();
                              cancelEdit();
                            }
                          }}
                          onSubmit={async (e) => {
                            e.preventDefault();
                            if (
                              !(await dispatch({
                                type: "manual",
                                field,
                                value: editValue,
                              }))
                            )
                              return;
                            cancelEdit();
                            setNotice(`${labels[field]} updated by operator.`);
                          }}
                        >
                          <label
                            className="sr-only"
                            htmlFor={`manual-${field}`}
                          >
                            {labels[field]}
                          </label>
                          <textarea
                            id={`manual-${field}`}
                            rows={editValue.includes("\n") ? 4 : 2}
                            autoFocus
                            value={editValue}
                            disabled={remoteBusy}
                            onChange={(e) => setEditValue(e.target.value)}
                          />
                          <button type="submit" disabled={remoteBusy}>
                            Save
                          </button>
                          <button type="button" onClick={cancelEdit}>
                            Cancel
                          </button>
                        </form>
                      ) : (
                        <div className="editable-value">
                          <div
                            className={
                              session.record[field]
                                ? "field-value"
                                : "field-value unset"
                            }
                          >
                            {session.record[field] || "Not yet recorded"}
                          </div>
                          <button
                            id={`record-edit-${field}`}
                            disabled={remoteBusy}
                            aria-label={`Edit record: ${labels[field]}`}
                            onClick={(e) => {
                              editTrigger.current = e.currentTarget.id;
                              setEditing(`record-${field}`);
                              setEditValue(session.record[field] || "");
                            }}
                          >
                            Edit
                          </button>
                        </div>
                      )}
                      {change && (
                        <button
                          className={`field-hint ${change.kind !== "addition" ? "correction-hint" : ""}`}
                          onClick={() =>
                            document.getElementById(change.id)?.focus()
                          }
                        >
                          {change.kind !== "addition"
                            ? "Changed value needs review"
                            : "Suggestion ready to review"}
                          <Icon name="arrow" size={12} />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
              <div className="record-note">
                {session.server && (
                  <details className="narrative-preview">
                    <summary>Detailed incident report ? draft</summary>
                    <p>
                      {session.server.narrative.text ||
                        "Accept information to draft a narrative."}
                    </p>
                  </details>
                )}
                <label htmlFor="handoff-note">
                  {demo ? "Operator note" : "Narrative override"}{" "}
                  <span>Optional</span>
                </label>
                <textarea
                  id="handoff-note"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder={
                    demo
                      ? "Add context for the receiving dispatcher…"
                      : "Leave blank to use the server narrative from accepted fields."
                  }
                  rows={2}
                />
              </div>
              <div className="publish-area">
                <div>
                  <strong>
                    {session.published
                      ? unpublished
                        ? "Unpublished edits"
                        : `Version ${session.published.version} shared`
                      : "Not yet shared"}
                  </strong>
                  <span>
                    {pending.length
                      ? `${pending.length} pending · Partial sharing allowed`
                      : demo
                        ? "Local preview only"
                        : "Server receiving inbox"}
                  </span>
                </div>
                <button
                  className="primary-button publication-feedback"
                  disabled={
                    remoteBusy ||
                    !Object.keys(session.record).length ||
                    !unpublished
                  }
                  onClick={publish}
                >
                  {session.published && !unpublished ? (
                    <>
                      <Icon name="check" size={14} />
                      Published v{session.published.version}
                    </>
                  ) : (
                    "Publish update"
                  )}
                </button>
              </div>
            </section>
            <aside className="review-panel">
              <header className="panel-heading">
                <div>
                  <h2 id="review-heading" tabIndex={-1}>
                    Proposed updates
                  </h2>
                </div>
                <span className="review-count">{pending.length} pending</span>
              </header>
              <p className="review-intro">
                Review before adding to the record.
              </p>
              <div className="suggestion-list">
                {reviewItems.length === 0 ? (
                  <div className="review-empty" id="review-empty" tabIndex={-1}>
                    <h3>No pending changes</h3>
                    <p>
                      {done
                        ? demo
                          ? "All suggestions have been reviewed."
                          : "No current proposals. Missing fields remain unknown; enter details manually if needed."
                        : "New details will appear as the call continues."}
                    </p>
                    {!done && (
                      <button onClick={() => dispatch({ type: "next" })}>
                        Continue scenario <Icon name="arrow" size={14} />
                      </button>
                    )}
                  </div>
                ) : (
                  reviewItems.map((s) => (
                    <article
                      className={`suggestion ${s.kind !== "addition" ? "is-correction" : ""}`}
                      key={s.id}
                      id={s.id}
                      tabIndex={-1}
                    >
                      <div className="suggestion-top">
                        <span>
                          {s.kind === "conflict"
                            ? "Conflicting statements"
                            : s.kind === "correction"
                              ? "Caller correction"
                              : s.kind === "change"
                                ? "Model proposes a change"
                                : "New detail"}
                        </span>
                        <time>{segments[s.source]?.at || ""}</time>
                      </div>
                      <h3>{labels[s.field]}</h3>
                      {session.record[s.field] && (
                        <div className="previous-value">
                          <span>Current record</span>
                          <p>{session.record[s.field]}</p>
                        </div>
                      )}
                      {editing === s.id ? (
                        <form
                          onKeyDown={(e) => {
                            if (e.key === "Escape") {
                              e.preventDefault();
                              cancelEdit();
                            }
                          }}
                          onSubmit={(e) => {
                            e.preventDefault();
                            review(s, "accept", editValue);
                          }}
                        >
                          {s.status !== "pending" && (
                            <p className="draft-warning" role="status">
                              The proposal changed while you were editing. Your
                              draft is preserved. Save it as a manual record
                              entry, or cancel to review the latest proposal.
                            </p>
                          )}
                          <label className="sr-only" htmlFor={`edit-${s.id}`}>
                            Edit {labels[s.field]}
                          </label>
                          <textarea
                            id={`edit-${s.id}`}
                            rows={editValue.includes("\n") ? 4 : 2}
                            autoFocus
                            value={editValue}
                            disabled={remoteBusy}
                            onChange={(e) => setEditValue(e.target.value)}
                          />
                          <div className="suggestion-actions">
                            <button
                              className="accept-button"
                              disabled={remoteBusy || !editValue.trim()}
                            >
                              {s.status === "pending"
                                ? "Save & accept"
                                : "Save to record"}
                            </button>
                            <button type="button" onClick={cancelEdit}>
                              Cancel
                            </button>
                          </div>
                        </form>
                      ) : (
                        <>
                          <div className="proposed-value">
                            {s.kind !== "addition" && (
                              <span>Proposed replacement</span>
                            )}
                            <p className="suggested-value">{s.value}</p>
                          </div>
                          <button
                            className="evidence-button"
                            aria-expanded={sourceOpen === s.id}
                            aria-controls={`source-${s.id}`}
                            onClick={() => evidence(s.source, s.id)}
                          >
                            <span className="source-time">
                              {segments[s.source]?.at || ""}
                            </span>
                            {sourceOpen === s.id
                              ? "Close source"
                              : "Inspect source"}
                          </button>
                          {sourceOpen === s.id && (
                            <div
                              className="source-excerpt"
                              id={`source-${s.id}`}
                            >
                              {s.evidence ? (
                                <>
                                  <span>
                                    {s.evidence.source === "original"
                                      ? "Original transcript context"
                                      : "English transcript context"}
                                  </span>
                                  <blockquote>{s.evidence.text}</blockquote>
                                  <span>{s.evidence.method}</span>
                                </>
                              ) : demo ? (
                                <>
                                  <span>Caller · Original Spanish</span>
                                  <blockquote lang="es">
                                    {segments[s.source]?.original}
                                  </blockquote>
                                  <span>Illustrative English translation</span>
                                  <p>{segments[s.source]?.english}</p>
                                </>
                              ) : (
                                <>
                                  <span>No supporting span returned</span>
                                  <p>
                                    Inspect the source utterance in the
                                    conversation pane. This proposal has no
                                    linked evidence.
                                  </p>
                                </>
                              )}
                              {audioUrl && (
                                <button
                                  onClick={() => audioPlayer.current?.focus()}
                                >
                                  Locate full recording player
                                </button>
                              )}
                            </div>
                          )}
                          {s.kind === "conflict" && (
                            <div className="conflict-options">
                              <p>
                                Choose the statement established with the
                                caller.
                              </p>
                              {s.options?.map((option) => (
                                <div key={option.id}>
                                  <blockquote>{option.statement}</blockquote>
                                  <button
                                    disabled={remoteBusy}
                                    onClick={() =>
                                      void runRemote(
                                        reviewOperation(session, {
                                          type: "resolve",
                                          id: s.id,
                                          option: option.id,
                                        }),
                                      )
                                    }
                                  >
                                    Use {option.value}
                                  </button>
                                </div>
                              ))}
                            </div>
                          )}
                          <div className="suggestion-actions">
                            <button
                              disabled={remoteBusy || s.kind === "conflict"}
                              className="accept-button"
                              onClick={() => review(s, "accept")}
                            >
                              {s.kind === "correction"
                                ? "Accept correction"
                                : "Accept"}
                            </button>
                            <button
                              id={`suggestion-edit-${s.id}`}
                              disabled={remoteBusy || s.kind === "conflict"}
                              onClick={(e) => {
                                editTrigger.current = e.currentTarget.id;
                                setEditing(s.id);
                                setEditValue(s.value);
                              }}
                            >
                              Edit
                            </button>
                            <button
                              className="dismiss-button"
                              disabled={remoteBusy}
                              onClick={() => review(s, "reject")}
                            >
                              Dismiss
                            </button>
                          </div>
                        </>
                      )}
                    </article>
                  ))
                )}
              </div>
            </aside>
          </div>
        ) : (
          <section className="receiving-panel">
            <header>
              <h2>
                {session.published
                  ? `Call ${demo ? session.id : session.id.slice(-8).toUpperCase()}`
                  : "No record published"}
              </h2>
              <p>
                {demo
                  ? "This view receives only explicitly published information."
                  : "Published record from the server receiving inbox. Working edits remain private until published."}
              </p>
            </header>
            {session.published ? (
              <>
                <div className="publication-meta">
                  {session.id}
                  <span>Published version {session.published.version}</span>
                </div>
                <div className="export-actions">
                  <button onClick={() => void copyPublication()}>
                    {copied ? (
                      <>
                        <Icon name="check" size={14} />
                        Copied
                      </>
                    ) : (
                      "Copy brief"
                    )}
                  </button>
                  <button onClick={downloadPublication}>Download record</button>
                  <span>
                    {unpublished
                      ? "Working record has unpublished changes"
                      : "Matches the current working record"}
                  </span>
                </div>
                <dl>
                  {fields.map((f) => (
                    <div key={f}>
                      <dt>{labels[f]}</dt>
                      <dd>{session.published?.record[f] || "Not recorded"}</dd>
                    </div>
                  ))}
                </dl>
                {session.published.note && (
                  <div className="published-note">
                    <h3>Operator note</h3>
                    <p>{session.published.note}</p>
                  </div>
                )}
                <div className="unresolved">
                  <h3>Unresolved at publication</h3>
                  {session.published.unresolved.length ? (
                    <ul>
                      {session.published.unresolved.map((x) => (
                        <li key={x}>{x}</li>
                      ))}
                    </ul>
                  ) : (
                    <p>
                      No pending suggestions or missing fields at publication.
                      This is not a dispatch clearance.
                    </p>
                  )}
                </div>
              </>
            ) : (
              <div className="handoff-empty">
                <p>
                  Accept details in the call record, then publish an update.
                </p>
              </div>
            )}
            <button onClick={() => setView("operator")}>
              <Icon name="back" size={15} />
              Back to call record
            </button>
          </section>
        )}
        <div className="bottom-line">
          <span role="status">{notice}</span>
          <span className="demo-only">
            {demo ? "Synthetic rehearsal" : "Operator review required"} · No
            dispatch actions
          </span>
        </div>
        <details className="review-history">
          <summary>
            Suggestion history{" "}
            <span>
              {session.suggestions.filter((s) => s.status !== "pending").length}
            </span>
          </summary>
          {session.suggestions
            .filter((s) => s.status !== "pending")
            .map((s) => (
              <div className="history-row" key={s.id}>
                <span>{labels[s.field]}</span>
                <strong>{s.value}</strong>
                <span>{s.status}</span>
                <button
                  onClick={() => {
                    setView("operator");
                    setSelected(s.source);
                    setSourceOpen(null);
                  }}
                >
                  Inspect source
                </button>
              </div>
            ))}
        </details>
        <details className="audit">
          <summary>
            Record activity <span>{session.history.length}</span>
          </summary>
          {session.history.length ? (
            <ol>
              {session.history.map((item, i) => (
                <li key={`${i}-${item}`}>{item}</li>
              ))}
            </ol>
          ) : (
            <p>Operator actions will appear here.</p>
          )}
        </details>
      </main>
      <footer className="app-footer">
        <span>DispatchIQ · Call-taking prototype</span>
        <a href="?view=pipeline">
          Open backend pipeline prototype <Icon name="arrow" size={13} />
        </a>
      </footer>
    </div>
  );
}

import type { Snapshot } from "../workspace/v2";

export default function CallAssistant({ snapshot, enabled, busy, error, onRefresh, onSource }: {
  snapshot?: Snapshot; enabled: boolean; busy: boolean; error: string | null;
  onRefresh: () => void; onSource: (index: number) => void;
}) {
  const draft = snapshot?.assistant;
  const callers = snapshot?.segments.filter(s => s.speaker === "caller") ?? [];
  const stale = !!draft && (draft.source_revision !== snapshot?.revision || draft.source_segment_count !== callers.length);
  return <section className="call-assistant" aria-label="LLM summary and follow-up">
    <header><h3>Call summary &amp; next question</h3>
      <button type="button" disabled={!enabled || !callers.length || busy} onClick={onRefresh}>
        {busy ? "Generating…" : draft ? "Refresh summary" : "Generate summary"}
      </button>
    </header>
    <p className="assistant-caption">LLM draft for operator review · Uses OpenAI after decision extraction.</p>
    {snapshot?.phone_demo_mode && <p>Phone demo mode speaks short fixed questions. The spoken question appears in the transcript.</p>}
    {busy && <p role="status">Summarizing caller statements and choosing the next question…</p>}
    {error && <p role="alert">{error}</p>}
    {!enabled && <p>LLM assistance is unavailable for this deployment. The scenario questions below remain available.</p>}
    {stale && <p className="draft-warning">New information is available. Refresh before using this draft.</p>}
    {draft?.status === "ready" ? <>
      <p className="assistant-summary">{draft.summary}</p>
      {draft.follow_up_question && <blockquote>{draft.follow_up_question}</blockquote>}
      {!draft.follow_up_question && <p>No follow-up question suggested.</p>}
      <div className="assistant-sources">Source statements: {draft.source_segment_ids.map(id => {
        const index = snapshot?.segments.findIndex(s => s.id === id) ?? -1;
        return index < 0 ? null : <button key={id} type="button" onClick={() => onSource(index)}>Utterance {index + 1}</button>;
      })}</div>
    </> : draft?.message ? <p role="status">{draft.message}</p> : !busy && enabled && <p>A summary and follow-up will appear after a caller statement is processed.</p>}
  </section>;
}

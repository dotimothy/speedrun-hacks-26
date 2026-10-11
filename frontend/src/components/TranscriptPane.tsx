import type { EvidenceSpan } from '../types'

interface Props {
  title: string
  text: string
  source: 'original' | 'english'
  // The span belonging to whichever prediction the dispatcher is pointing at.
  highlight: EvidenceSpan | null
  language?: string | null
  languageProbability?: number | null
}

export default function TranscriptPane({
  title,
  text,
  source,
  highlight,
  language,
  languageProbability,
}: Props) {
  const active = highlight && highlight.source === source ? highlight : null

  // Offsets come from the server already validated against this exact string,
  // so slicing is safe -- but clamp anyway rather than render garbage if the
  // transcript and the span ever disagree.
  const start = active ? Math.max(0, Math.min(active.start, text.length)) : 0
  const end = active ? Math.max(start, Math.min(active.end, text.length)) : 0

  return (
    <section className="pane">
      <header className="pane-head">
        <h3>{title}</h3>
        {language && (
          <span className="chip" title="Whisper's language identification">
            {language}
            {languageProbability != null && ' · ' + (languageProbability * 100).toFixed(0) + '%'}
          </span>
        )}
      </header>
      <p className="transcript">
        {active ? (
          <>
            {text.slice(0, start)}
            <mark className={'ev ev-' + active.match}>{text.slice(start, end)}</mark>
            {text.slice(end)}
          </>
        ) : (
          text || <span className="muted">nothing transcribed</span>
        )}
      </p>
      {active && (
        <p className="pane-foot muted">
          highlighted span matched {active.match} at [{active.start}, {active.end})
        </p>
      )}
    </section>
  )
}

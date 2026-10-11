import { useState } from 'react'
import type { Prediction } from '../types'

interface Props {
  predictions: Prediction[]
  onHover: (prediction: Prediction | null) => void
}

// Three bands, not two decimal places. The scores are derived from
// probabilities the model's own documentation calls over-confident, so
// rendering "0.87" implies a precision that is not there. A band says what the
// number is actually good for: whether a human should look harder.
function band(score: number): { label: string; className: string } {
  if (score >= 0.75) return { label: 'well supported', className: 'band-high' }
  if (score >= 0.55) return { label: 'needs a look', className: 'band-mid' }
  if (score > 0) return { label: 'weak', className: 'band-low' }
  return { label: 'no basis', className: 'band-none' }
}

function render(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'none found'
  return String(value)
}

export default function PredictionList({ predictions, onHover }: Props) {
  const [open, setOpen] = useState<string | null>(null)

  return (
    <section className="pane">
      <header className="pane-head">
        <h3>Extracted fields</h3>
        <span className="muted">hover a row to see where it came from</span>
      </header>

      <ul className="tags">
        {predictions.map((prediction) => {
          const quality = band(prediction.score)
          const unknown = prediction.value === 'unknown' || prediction.value === null
          const expanded = open === prediction.label
          return (
            <li
              key={prediction.label}
              className={'tag' + (unknown ? ' tag-unknown' : '')}
              onMouseEnter={() => onHover(prediction)}
              onMouseLeave={() => onHover(null)}
            >
              <div className="tag-row">
                <span className="tag-label">{prediction.label.replace(/_/g, ' ')}</span>
                <span className="tag-value">{render(prediction.value)}</span>
                <span className={'badge ' + quality.className}>{quality.label}</span>
                {prediction.verification_state !== 'unverified' && (
                  <span className="badge badge-human">{prediction.verification_state}</span>
                )}
                {prediction.conflict && (
                  <span className="badge badge-conflict" title="independent checks disagreed">
                    disagreement
                  </span>
                )}
                <button className="link" onClick={() => setOpen(expanded ? null : prediction.label)}>
                  {expanded ? 'hide' : 'why'}
                </button>
              </div>

              {prediction.alternative != null && (
                <div className="tag-alt">
                  another check said <strong>{render(prediction.alternative)}</strong>
                </div>
              )}

              <div className="tag-ev">
                {prediction.evidence ? (
                  <q>{prediction.evidence.text}</q>
                ) : (
                  <span className="muted">
                    no supporting span found in the transcript
                  </span>
                )}
              </div>

              {expanded && prediction.breakdown && (
                <table className="breakdown">
                  <tbody>
                    {Object.entries(prediction.breakdown).map(([key, value]) => (
                      <tr key={key}>
                        <th>{key.replace(/_/g, ' ')}</th>
                        <td>
                          {typeof value === 'object'
                            ? JSON.stringify(value)
                            : String(value)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

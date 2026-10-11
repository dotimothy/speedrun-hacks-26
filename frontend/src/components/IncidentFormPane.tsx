import { useEffect, useState } from 'react'
import type { IncidentForm, Prediction } from '../types'

interface Props {
  form: IncidentForm
  predictions: Prediction[]
  missing: string[]
  onConfirm: (fields: Record<string, unknown>) => void
  busy: boolean
}

const CHOICES: Record<string, string[]> = {
  incident_type: [
    'structure_fire', 'medical', 'traffic_collision', 'crime_in_progress',
    'gas_leak', 'water_rescue', 'other', 'unknown',
  ],
  injuries: ['none', 'suspected', 'confirmed', 'unknown'],
  weapons_present: ['yes', 'no', 'unknown'],
  caller_safe: ['yes', 'no', 'unknown'],
}

const TEXT_FIELDS = ['location_text', 'callback_number'] as const

export default function IncidentFormPane({ form, predictions, missing, onConfirm, busy }: Props) {
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [touched, setTouched] = useState<Set<string>>(new Set())

  // A fresh model pass may change fields the dispatcher has not touched. Those
  // should update on screen; the ones they HAVE edited must not be pulled out
  // from under them mid-typing.
  useEffect(() => {
    setDraft((previous) => {
      const next: Record<string, unknown> = { ...(form as unknown as Record<string, unknown>) }
      for (const field of touched) next[field] = previous[field]
      return next
    })
  }, [form, touched])

  const stateOf = (label: string) =>
    predictions.find((prediction) => prediction.label === label)?.verification_state ?? 'unverified'

  const edit = (field: string, value: unknown) => {
    setTouched((previous) => new Set(previous).add(field))
    setDraft((previous) => ({ ...previous, [field]: value }))
  }

  const confirmOne = (field: string) => onConfirm({ [field]: draft[field] })

  const confirmAll = () => {
    const fields: Record<string, unknown> = {}
    for (const key of [...Object.keys(CHOICES), ...TEXT_FIELDS]) fields[key] = draft[key]
    onConfirm(fields)
  }

  const row = (field: string, input: React.ReactNode) => {
    const state = stateOf(field)
    return (
      <div className={'form-row' + (missing.includes(field) ? ' form-row-missing' : '')} key={field}>
        <label>{field.replace(/_/g, ' ')}</label>
        {input}
        <span className={'badge badge-' + state}>{state}</span>
        <button className="link" disabled={busy} onClick={() => confirmOne(field)}>
          confirm
        </button>
      </div>
    )
  }

  return (
    <section className="pane">
      <header className="pane-head">
        <h3>Suggested incident form</h3>
        <span className="muted">a draft — nothing here is filed</span>
      </header>

      {Object.entries(CHOICES).map(([field, options]) =>
        row(
          field,
          <select value={String(draft[field] ?? 'unknown')} onChange={(event) => edit(field, event.target.value)}>
            {options.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>,
        ),
      )}

      {TEXT_FIELDS.map((field) =>
        row(
          field,
          <input
            type="text"
            value={String(draft[field] ?? '')}
            placeholder={missing.includes(field) ? 'ask the caller' : ''}
            onChange={(event) => edit(field, event.target.value)}
          />,
        ),
      )}

      {row(
        'persons_involved',
        <input
          type="number"
          min={0}
          value={draft.persons_involved == null ? '' : String(draft.persons_involved)}
          onChange={(event) =>
            edit('persons_involved', event.target.value === '' ? null : Number(event.target.value))
          }
        />,
      )}

      <div className="form-row">
        <label>hazards</label>
        <span className="tag-value">
          {form.hazards.length ? form.hazards.join(', ') : 'none found'}
        </span>
      </div>

      <footer className="pane-foot">
        <button className="primary" disabled={busy} onClick={confirmAll}>
          Confirm all fields
        </button>
        <span className="muted">
          Confirmed values are never overwritten by a later model pass.
        </span>
      </footer>
    </section>
  )
}

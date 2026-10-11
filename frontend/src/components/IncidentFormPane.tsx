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
  occupancy: ['dwelling', 'multi_unit', 'commercial', 'unknown'],
  fire_extent: ['smoke_only', 'one_room', 'one_floor', 'whole_building', 'spreading', 'unknown'],
  chief_complaint: ['chest_pain', 'trouble_breathing', 'unconscious', 'bleeding', 'seizure', 'unknown'],
  awake: ['yes', 'no', 'unknown'],
  breathing: ['yes', 'no', 'unknown'],
}

const LABELS: Record<string, string> = {
  incident_type: 'Nature',
  location_text: 'Location',
  callback_number: 'Callback',
  caller_name: 'Caller',
  chief_complaint: 'Chief complaint',
  awake: 'Awake',
  breathing: 'Breathing',
  occupancy: 'Occupancy',
  fire_extent: 'How big',
  injuries: 'Injuries',
  persons_involved: 'People',
  weapons_present: 'Weapon',
  caller_safe: 'Caller safe',
  hazards: 'Hazards',
}

const SHARED = ['incident_type', 'location_text', 'callback_number', 'caller_name']

const SECTIONS: Record<string, { title: string; fields: string[] }> = {
  medical: {
    title: 'Medical',
    fields: ['chief_complaint', 'awake', 'breathing', 'injuries', 'persons_involved'],
  },
  fire: {
    title: 'Fire',
    fields: ['occupancy', 'fire_extent', 'injuries', 'persons_involved', 'caller_safe', 'hazards'],
  },
  traffic: {
    title: 'Traffic',
    fields: ['injuries', 'persons_involved', 'hazards'],
  },
  crime: {
    title: 'Crime',
    fields: ['weapons_present', 'caller_safe', 'injuries'],
  },
  scene: {
    title: 'Scene',
    fields: ['injuries', 'persons_involved', 'hazards'],
  },
}

function sectionKey(incident: string): string {
  if (incident === 'medical') return 'medical'
  if (incident === 'structure_fire' || incident === 'gas_leak') return 'fire'
  if (incident === 'traffic_collision') return 'traffic'
  if (incident === 'crime_in_progress') return 'crime'
  return 'scene'
}

export default function IncidentFormPane({ form, predictions, missing, onConfirm, busy }: Props) {
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [touched, setTouched] = useState<Set<string>>(new Set())

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

  const section = SECTIONS[sectionKey(String(draft.incident_type ?? form.incident_type))]
  const visible = [...SHARED, ...section.fields.filter((field) => field !== 'hazards')]

  const confirmAll = () => {
    const fields: Record<string, unknown> = {}
    for (const key of visible) fields[key] = draft[key]
    onConfirm(fields)
  }

  const control = (field: string) => {
    if (field === 'hazards') {
      return (
        <span className="tag-value">
          {form.hazards.length ? form.hazards.join(', ') : 'none found'}
        </span>
      )
    }
    if (field === 'persons_involved') {
      return (
        <input
          type="number"
          min={0}
          value={draft.persons_involved == null ? '' : String(draft.persons_involved)}
          onChange={(event) =>
            edit('persons_involved', event.target.value === '' ? null : Number(event.target.value))
          }
        />
      )
    }
    if (field in CHOICES) {
      return (
        <select value={String(draft[field] ?? 'unknown')} onChange={(event) => edit(field, event.target.value)}>
          {CHOICES[field].map((option) => (
            <option key={option} value={option}>
              {option.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
      )
    }
    return (
      <input
        type="text"
        value={String(draft[field] ?? '')}
        placeholder={missing.includes(field) ? 'ask the caller' : ''}
        onChange={(event) => edit(field, event.target.value)}
      />
    )
  }

  const row = (field: string) => {
    const state = stateOf(field)
    const label = field === 'caller_safe' && section.title === 'Fire' ? 'Everyone out' : (LABELS[field] ?? field)
    return (
      <div className={'form-row' + (missing.includes(field) ? ' form-row-missing' : '')} key={field}>
        <label>{label}</label>
        {control(field)}
        {field !== 'hazards' && <span className={'badge badge-' + state}>{state}</span>}
        {field !== 'hazards' && (
          <button className="link" disabled={busy} onClick={() => confirmOne(field)}>
            confirm
          </button>
        )}
      </div>
    )
  }

  return (
    <section className="pane">
      <header className="pane-head">
        <h3>Call record</h3>
        <span className="muted">suggested — a person confirms before anything is filed</span>
      </header>

      <div className="form-section">
        <h4>Every call</h4>
        {SHARED.map((field) => row(field))}
      </div>
      <div className="form-section">
        <h4>{section.title}</h4>
        {section.fields.map((field) => row(field))}
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

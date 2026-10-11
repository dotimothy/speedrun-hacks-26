import type { Snapshot } from '../workspace/v2'

export default function ScenarioDecisions({ focus }: { focus: Snapshot['decision_focus'] }) {
  if (!focus) return null
  return (
    <section className="scenario-decisions" aria-label="Scenario decision model">
      <h3>{focus.title}</h3>
      <p>{focus.purpose}</p>
      <dl>
        {focus.decisions.map(decision => (
          <div key={decision.field}>
            <dt>{decision.label}</dt>
            <dd>
              <strong>{decision.value == null || decision.value === 'unknown'
                ? 'Not established' : String(decision.value).replace(/_/g, ' ')}</strong>
              <span>{decision.status.replace(/_/g, ' ')}</span>
              {decision.suggestion_id && <button type="button" onClick={() => {
                const row = document.getElementById(decision.suggestion_id!)
                row?.scrollIntoView({ block: 'nearest' })
                row?.focus()
              }}>Review</button>}
            </dd>
          </div>
        ))}
      </dl>
      <p className="decision-caption">Interpretations guide the next questions. Only operator-accepted values enter the report.</p>
    </section>
  )
}

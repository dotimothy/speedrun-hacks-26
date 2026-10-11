import { useEffect, useState } from 'react'
import type { ModelInfo } from '../types'

interface EngineInfo {
  name: string
  model?: string
  is_local?: boolean
  ready?: boolean
  inference_location?: string
  warning?: string
  error?: string
  note?: string
}

interface Props {
  open: boolean
  onClose: () => void
  models: ModelInfo | null
  health: Record<string, any> | null
  whisperSize: string
  onWhisperSize: (size: string) => void
  engine: string
  onEngine: (engine: string) => void
}

/**
 * Everything that changes how the system answers, in one place.
 *
 * The engine list is the part that earns the panel. Three of the four
 * available engines run on this machine and one does not, and "where did the
 * caller's words go" is not a detail to leave implicit in a config file --
 * especially in a product whose pitch is local inference. So the locality of
 * each engine is shown on the control that selects it, and choosing a cloud
 * engine says plainly what that means.
 */
export default function SettingsMenu({
  open,
  onClose,
  models,
  health,
  whisperSize,
  onWhisperSize,
  engine,
  onEngine,
}: Props) {
  const [engines, setEngines] = useState<EngineInfo[]>([])

  useEffect(() => {
    const listed = models?.decision_engines
    if (Array.isArray(listed)) {
      // The backend may send plain names (v1) or full descriptions (v2).
      setEngines(
        listed.map((entry: unknown) =>
          typeof entry === 'string' ? { name: entry, is_local: true, ready: true } : (entry as EngineInfo),
        ),
      )
    }
  }, [models])

  if (!open) return null

  const selected = engines.find((e) => e.name === engine)
  const usingCloud = selected ? selected.is_local === false : false

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Settings">
        <header className="drawer-head">
          <h2>Settings</h2>
          <button className="link" onClick={onClose}>
            close
          </button>
        </header>

        {usingCloud && (
          <p className="cloud-warning">
            This engine runs off this machine. The caller's words are sent to a third
            party, and the project's local-inference claim does not hold while it is
            selected — say so in the demo rather than leaving the slide up.
          </p>
        )}

        <section className="drawer-section">
          <h3>Decision engine</h3>
          <p className="muted">Which model answers the typed questions about the call.</p>
          <div className="engine-list">
            {engines.map((item) => {
              const disabled = item.ready === false
              return (
                <label
                  key={item.name}
                  className={
                    'engine' +
                    (engine === item.name ? ' engine-active' : '') +
                    (disabled ? ' engine-disabled' : '')
                  }
                >
                  <input
                    type="radio"
                    name="engine"
                    value={item.name}
                    checked={engine === item.name}
                    disabled={disabled}
                    onChange={() => onEngine(item.name)}
                  />
                  <span className="engine-body">
                    <span className="engine-row">
                      <strong>{item.name}</strong>
                      <span className={'badge ' + (item.is_local === false ? 'band-mid' : 'band-high')}>
                        {item.is_local === false ? 'off this machine' : 'on this machine'}
                      </span>
                      {disabled && <span className="badge band-none">unavailable</span>}
                    </span>
                    <span className="muted">{item.model}</span>
                    {item.error && <span className="engine-note warn">{item.error}</span>}
                    {item.note && !item.error && <span className="engine-note muted">{item.note}</span>}
                  </span>
                </label>
              )
            })}
          </div>
          {models && (models as any).cloud_inference_enabled === false && (
            <p className="muted">
              Cloud engines are disabled for this deployment. Set
              <code> DISPATCHIQ_ALLOW_CLOUD=1</code> to offer them — deliberately, in
              configuration, not from this menu.
            </p>
          )}
        </section>

        <section className="drawer-section">
          <h3>Speech recognition</h3>
          <label className="setting-row">
            <span>Whisper model</span>
            <select value={whisperSize} onChange={(event) => onWhisperSize(event.target.value)}>
              {(models?.whisper_sizes ?? ['small']).map((size) => (
                <option key={size} value={size}>
                  {size}
                  {models?.whisper_loaded.includes(size) ? ' (loaded)' : ''}
                </option>
              ))}
            </select>
          </label>
          <p className="muted">
            Larger is more accurate and slower. On a Mac this runs on the CPU —
            CTranslate2 has no Metal backend — so prefer <code>tiny</code> or{' '}
            <code>base</code> there.
          </p>
        </section>

        <section className="drawer-section">
          <h3>Abstention thresholds</h3>
          <p className="muted">
            Where the system stops guessing and reports <code>unknown</code>. Read-only
            here: changing them changes what every score means, so they live in
            configuration.
          </p>
          <table className="latency">
            <tbody>
              {Object.entries(models?.thresholds ?? {}).map(([name, value]) => (
                <tr key={name}>
                  <th>{name.replace(/_/g, ' ')}</th>
                  <td>{String(value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {health && (
          <section className="drawer-section">
            <h3>This machine</h3>
            <table className="latency">
              <tbody>
                <tr>
                  <th>whisper</th>
                  <td>
                    {health.whisper?.device}/{health.whisper?.compute_type}
                  </td>
                </tr>
                <tr>
                  <th>laya</th>
                  <td>{health.laya?.ready ? health.laya?.device : 'unavailable'}</td>
                </tr>
                <tr>
                  <th>platform</th>
                  <td>
                    {health.platform?.system} {health.platform?.machine}
                  </td>
                </tr>
                <tr>
                  <th>inference</th>
                  <td>{health.inference_is_local === false ? 'leaves this machine' : 'local'}</td>
                </tr>
              </tbody>
            </table>
            <p className="muted">{health.calibration_warning}</p>
          </section>
        )}
      </aside>
    </>
  )
}

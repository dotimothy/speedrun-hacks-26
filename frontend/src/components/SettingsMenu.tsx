import { useEffect, useRef } from 'react'
import type { ModelInfo } from '../types'
import './SettingsMenu.css'

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
  phoneDemoMode?: boolean
  onPhoneDemoMode?: (enabled: boolean) => void
  loading?: boolean
  busy?: boolean
  error?: string | null
  onRetry?: () => void
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
  phoneDemoMode = false,
  onPhoneDemoMode,
  loading = false,
  busy = false,
  error,
  onRetry,
}: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const engines: EngineInfo[] = (models?.decision_engines ?? []).map(entry =>
    typeof entry === 'string' ? { name: entry } : entry)
  useEffect(() => {
    const node = dialog.current
    if (open && node && !node.open) node.showModal()
    return () => { if (node?.open) node.close() }
  }, [open])

  if (!open) return null

  const selected = engines.find((e) => e.name === (engine || models?.decision_default))
  const usingCloud = selected ? selected.is_local === false : false

  return (
      <dialog ref={dialog} className="model-settings" aria-label="Settings"
        onCancel={onClose} onClick={event => { if (event.target === event.currentTarget) onClose() }}>
        <div className="settings-content">
        <header className="drawer-head">
          <h2>Settings</h2>
          <button className="link" onClick={onClose}>
            close
          </button>
        </header>

        {usingCloud && (
          <p className="cloud-warning">
            This model sends the caller's transcript to the configured external provider.
          </p>
        )}

        <section className="drawer-section">
          <h3>Decision engine</h3>
          <p className="muted">Applies to new input and the active phone session. Existing results stay as recorded. Your choice is remembered in this browser.</p>
          {loading && <p role="status">Loading available models…</p>}
          {busy && <p role="status">Saving model selection…</p>}
          {error && <p role="alert">{error} {onRetry && <button onClick={onRetry}>Reload models</button>}</p>}
          <div className="engine-list">
            <label className={'engine' + (!engine ? ' engine-active' : '')}>
              <input type="radio" name="engine" value="" checked={!engine} disabled={loading || busy || !models}
                onChange={() => onEngine('')} />
              <span className="engine-body"><strong>Server default{models ? ` (${models.decision_default})` : ''}</strong></span>
            </label>
            {engines.map((item) => {
              const unavailable = item.ready === false
              const disabled = unavailable || loading || busy
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
                      <strong>{{ laya: 'Laya', rules: 'Rules', openai: 'OpenAI', openai_decisions: 'OpenAI Decisions', jev: 'Jev' }[item.name] || item.name}</strong>
                      <span className={'badge ' + (item.is_local === false ? 'band-mid' : 'band-high')}>
                        {item.is_local === false ? 'off this machine' : 'on this machine'}
                      </span>
                      {unavailable && <span className="badge band-none">unavailable</span>}
                    </span>
                    <span className="muted">{item.model}</span>
                    {item.error && <span className="engine-note warn">{item.error}</span>}
                    {item.note && !item.error && <span className="engine-note muted">{item.note}</span>}
                  </span>
                </label>
              )
            })}
          </div>
          {models?.cloud_inference_enabled === false && (
            <p className="muted">
              Cloud engines are not enabled for this deployment.
            </p>
          )}
        </section>

        {onPhoneDemoMode && <section className="drawer-section">
          <h3>Phone demo mode</h3>
          <label className="setting-row">
            <span>Short demo (about one minute)</span>
            <input type="checkbox" checked={phoneDemoMode} disabled={loading || busy || !models}
              onChange={event => onPhoneDemoMode(event.target.checked)} />
          </label>
          <p className="muted">Up to six concise questions covering location, callback, injuries, safety, headcount, and whereabouts. Each is asked once, then a closing message. Applies to new phone calls and the selected active call.</p>
        </section>}

        <section className="drawer-section">
          <h3>Speech recognition</h3>
          <label className="setting-row">
            <span>Whisper model</span>
            <select value={whisperSize} disabled={loading || busy || !models} onChange={(event) => onWhisperSize(event.target.value)}>
              {(models?.whisper_sizes ?? ['small']).map((size) => (
                <option key={size} value={size}>
                  {size}
                  {models?.whisper_loaded.includes(size) ? ' (loaded)' : ''}
                </option>
              ))}
            </select>
          </label>
          <p className="muted">
            Used for microphone and WAV input from this workspace. Phone audio uses the server's speech settings.
          </p>
        </section>

        <section className="drawer-section">
          <h3>Summary &amp; follow-up LLM</h3>
          <p>{models?.call_assistant?.enabled ? `${models.call_assistant.provider} · ${models.call_assistant.model}` : 'Unavailable'}</p>
          <p className="muted">Runs after the decision model and sends the transcript and extracted fields to OpenAI. Generates operator drafts; does not change the reviewed record.</p>
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
        </div>
      </dialog>
  )
}

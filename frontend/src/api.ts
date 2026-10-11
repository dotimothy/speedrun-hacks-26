import type { ApprovedPhrase, ModelInfo, ProcessResponse } from './types'

// The server cannot measure the network, so it returns null for those fields
// and the client fills them in from its own clock. The alternative would be
// the server inventing a number it has no way to observe.
function withClientTiming(data: ProcessResponse, wallMs: number): ProcessResponse {
  const server = data.latency_ms.server_total ?? 0
  return {
    ...data,
    latency_ms: {
      ...data.latency_ms,
      end_to_end: Math.round(wallMs),
      network: Math.max(0, Math.round(wallMs - server)),
    },
  }
}

async function parse(response: Response): Promise<ProcessResponse> {
  if (!response.ok) {
    const body = await response.text()
    throw new Error(response.status + ': ' + body.slice(0, 300))
  }
  return response.json()
}

export async function processTranscript(
  sessionId: string,
  transcript: string,
  confirmed: Record<string, unknown> = {},
): Promise<ProcessResponse> {
  const started = performance.now()
  const response = await fetch('/api/process', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, transcript, confirmed }),
  })
  return withClientTiming(await parse(response), performance.now() - started)
}

export async function processAudio(
  sessionId: string,
  wav: Blob,
  whisperSize: string,
  confirmed: Record<string, unknown> = {},
): Promise<ProcessResponse> {
  const form = new FormData()
  form.append(
    'payload',
    JSON.stringify({ session_id: sessionId, whisper_size: whisperSize, confirmed }),
  )
  form.append('audio', wav, sessionId + '.wav')
  const started = performance.now()
  const response = await fetch('/api/process', { method: 'POST', body: form })
  return withClientTiming(await parse(response), performance.now() - started)
}

export async function fetchModels(): Promise<ModelInfo> {
  return (await fetch('/api/models')).json()
}

export async function fetchHealth(): Promise<Record<string, any>> {
  return (await fetch('/api/health')).json()
}

export async function fetchPhrases(): Promise<ApprovedPhrase[]> {
  const data = await (await fetch('/api/phrases')).json()
  return data.phrases
}

export async function confirmFields(
  sessionId: string,
  fields: Record<string, unknown>,
): Promise<void> {
  await fetch('/api/sessions/' + encodeURIComponent(sessionId) + '/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fields }),
  })
}

export async function speak(phraseId: string, sessionId?: string): Promise<string> {
  const response = await fetch('/api/speak', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phrase_id: phraseId, session_id: sessionId }),
  })
  const data = await response.json()
  if (!data.audio_wav_base64) {
    // Documented fallback: the dispatcher reads the line out loud instead.
    throw new Error(data.fallback ?? 'speech unavailable')
  }
  const audio = new Audio('data:audio/wav;base64,' + data.audio_wav_base64)
  await audio.play()
  return data.text as string
}

export async function fetchSpoken(
  sessionId: string,
): Promise<Array<{ id: string; language: string; text: string }>> {
  const response = await fetch('/api/sessions/' + encodeURIComponent(sessionId) + '/spoken')
  if (!response.ok) return []
  return (await response.json()).phrases
}

export async function fetchFixtureList(): Promise<string[]> {
  // The committed fixtures double as the demo script and the offline fallback.
  const response = await fetch('/fixtures/index.json')
  if (!response.ok) return []
  const index = await response.json()
  return index.map((entry: { id: string }) => entry.id)
}

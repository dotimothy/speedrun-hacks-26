// Microphone capture, encoded as the WAV the backend contract names.
//
// MediaRecorder would hand us webm/opus, which the server would then have to
// decode. Whisper wants 16 kHz mono PCM anyway, so the browser does the
// conversion and the server receives exactly what it asked for. No library.
//
// getUserMedia needs a secure context: http://localhost:5173 qualifies, a LAN
// IP does not. Demo from localhost.

export function downsample(input: Float32Array, inRate: number, outRate = 16000): Float32Array {
  if (outRate >= inRate) return input
  const ratio = inRate / outRate
  const out = new Float32Array(Math.floor(input.length / ratio))
  for (let i = 0; i < out.length; i++) {
    // Average the source window instead of point-sampling, which would alias
    // audibly and cost transcription accuracy.
    const start = Math.floor(i * ratio)
    const end = Math.min(input.length, Math.floor((i + 1) * ratio))
    let sum = 0
    for (let j = start; j < end; j++) sum += input[j]
    out[i] = end > start ? sum / (end - start) : 0
  }
  return out
}

export function encodeWav(samples: Float32Array, sampleRate = 16000): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)

  const writeString = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i))
  }

  writeString(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  writeString(8, 'WAVE')
  writeString(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true) // PCM
  view.setUint16(22, 1, true) // mono
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeString(36, 'data')
  view.setUint32(40, samples.length * 2, true)

  let offset = 44
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true)
    offset += 2
  }
  return new Blob([view], { type: 'audio/wav' })
}

export class Recorder {
  private context: AudioContext | null = null
  private stream: MediaStream | null = null
  private chunks: Float32Array[] = []
  private node: ScriptProcessorNode | null = null

  get recording(): boolean {
    return this.context !== null
  }

  async start(): Promise<void> {
    this.chunks = []
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    })
    this.context = new AudioContext()
    const source = this.context.createMediaStreamSource(this.stream)

    // ScriptProcessor is deprecated but universally present and synchronous,
    // which keeps this to one file. An AudioWorklet would need a separate
    // module served at a stable URL for no behavioural gain at demo scale.
    this.node = this.context.createScriptProcessor(4096, 1, 1)
    this.node.onaudioprocess = (event) => {
      this.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)))
    }
    source.connect(this.node)
    this.node.connect(this.context.destination)
  }

  async stop(): Promise<Blob> {
    const rate = this.context?.sampleRate ?? 48000
    this.node?.disconnect()
    this.stream?.getTracks().forEach((track) => track.stop())
    await this.context?.close()
    this.context = null
    this.node = null
    this.stream = null

    const total = this.chunks.reduce((sum, chunk) => sum + chunk.length, 0)
    const joined = new Float32Array(total)
    let offset = 0
    for (const chunk of this.chunks) {
      joined.set(chunk, offset)
      offset += chunk.length
    }
    return encodeWav(downsample(joined, rate), 16000)
  }
}

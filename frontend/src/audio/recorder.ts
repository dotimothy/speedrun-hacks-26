// Microphone capture, encoded as the WAV the backend contract names.
//
// MediaRecorder would hand us webm/opus, which the server would then have to
// decode. Whisper wants 16 kHz mono PCM anyway, so the browser does the
// conversion and the server receives exactly what it asked for. No library.
//
// getUserMedia needs a secure context: http://localhost:5173 qualifies, a LAN
// IP does not. Demo from localhost.
//
// Safari matters here because half the team is on a Mac: it still exposes the
// constructor as webkitAudioContext, and it refuses to start an AudioContext
// that was not created inside a user gesture, so the context is built in the
// click handler and resumed explicitly.

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

function createAudioContext(): AudioContext {
  const Ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) throw new Error('this browser has no Web Audio support')
  return new Ctor()
}

export class Recorder {
  private context: AudioContext | null = null
  private stream: MediaStream | null = null
  private chunks: Float32Array[] = []
  private node: ScriptProcessorNode | null = null
  private analyser: AnalyserNode | null = null
  private sink: GainNode | null = null
  private meterBuffer: Float32Array = new Float32Array(0)
  private startedAt = 0

  get recording(): boolean {
    return this.context !== null
  }

  /** Seconds captured so far. */
  get elapsed(): number {
    if (!this.context) return 0
    return (performance.now() - this.startedAt) / 1000
  }

  /** Current input loudness, 0..1, for the level meter. */
  level(): number {
    if (!this.analyser) return 0
    this.analyser.getFloatTimeDomainData(this.meterBuffer)
    let sum = 0
    for (let i = 0; i < this.meterBuffer.length; i++) {
      sum += this.meterBuffer[i] * this.meterBuffer[i]
    }
    const rms = Math.sqrt(sum / this.meterBuffer.length)
    // Speech RMS sits well below 1.0, so scale into a range that actually
    // moves on screen rather than hugging the bottom of the meter.
    return Math.min(1, rms * 4)
  }

  async start(): Promise<void> {
    this.chunks = []
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    })
    this.context = createAudioContext()
    // Safari starts contexts suspended unless they were created in a gesture.
    if (this.context.state === 'suspended') await this.context.resume()
    const source = this.context.createMediaStreamSource(this.stream)

    this.analyser = this.context.createAnalyser()
    this.analyser.fftSize = 1024
    this.meterBuffer = new Float32Array(this.analyser.fftSize)
    source.connect(this.analyser)

    // ScriptProcessor is deprecated but universally present and synchronous,
    // which keeps this to one file. An AudioWorklet would need a separate
    // module served at a stable URL for no behavioural gain at demo scale.
    this.node = this.context.createScriptProcessor(4096, 1, 1)
    this.node.onaudioprocess = (event) => {
      this.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)))
    }
    source.connect(this.node)

    // A ScriptProcessor only runs while connected to the graph's destination,
    // but routing the microphone there would put it through the speakers --
    // which, in a room with the laptop mic open, is a feedback loop. Send it
    // to a silent gain node instead: the processor still ticks, nothing is
    // audible.
    this.sink = this.context.createGain()
    this.sink.gain.value = 0
    this.node.connect(this.sink)
    this.sink.connect(this.context.destination)

    this.startedAt = performance.now()
  }

  private collect(): Float32Array {
    const total = this.chunks.reduce((sum, chunk) => sum + chunk.length, 0)
    const joined = new Float32Array(total)
    let offset = 0
    for (const chunk of this.chunks) {
      joined.set(chunk, offset)
      offset += chunk.length
    }
    return joined
  }

  /**
   * WAV of everything captured so far, WITHOUT stopping the recording.
   *
   * Live mode re-sends the whole call each time rather than appending slices.
   * Whisper reads a complete utterance far better than a 4-second fragment cut
   * mid-word, and re-transcribing 20 seconds costs well under a second on a
   * GPU -- so accuracy is worth the repeated work.
   */
  snapshot(): Blob | null {
    if (!this.context) return null
    const samples = this.collect()
    if (samples.length === 0) return null
    return encodeWav(downsample(samples, this.context.sampleRate), 16000)
  }

  async stop(): Promise<Blob> {
    const rate = this.context?.sampleRate ?? 48000
    const samples = this.collect()

    this.node?.disconnect()
    this.analyser?.disconnect()
    this.sink?.disconnect()
    this.stream?.getTracks().forEach((track) => track.stop())
    await this.context?.close()
    this.context = null
    this.node = null
    this.analyser = null
    this.sink = null
    this.stream = null

    return encodeWav(downsample(samples, rate), 16000)
  }
}

/**
 * Exact duration in seconds, read from the WAV header.
 *
 * The `<audio>` element's own `duration` is the obvious source and is not a
 * reliable one: it is NaN until metadata loads, and Chrome defers media
 * loading entirely in a backgrounded tab, which leaves the scrubber frozen at
 * 0:00 with no error to explain it. For linear PCM the header already carries
 * the answer -- data chunk size divided by byte rate -- so take it from there
 * and let the element catch up on its own.
 *
 * Chunks are walked rather than read at fixed offsets, because ffmpeg writes a
 * LIST/INFO chunk ahead of `data` and a hard-coded offset would land in it.
 */
export async function wavDuration(blob: Blob): Promise<number | null> {
  try {
    const view = new DataView(await blob.arrayBuffer())
    const tag = (offset: number) =>
      String.fromCharCode(
        view.getUint8(offset), view.getUint8(offset + 1),
        view.getUint8(offset + 2), view.getUint8(offset + 3),
      )

    if (tag(0) !== 'RIFF' || tag(8) !== 'WAVE') return null

    let byteRate = 0
    let dataSize = 0
    let offset = 12

    while (offset + 8 <= view.byteLength) {
      const id = tag(offset)
      const size = view.getUint32(offset + 4, true)
      const body = offset + 8
      if (id === 'fmt ') byteRate = view.getUint32(body + 8, true)
      else if (id === 'data') dataSize = Math.min(size, view.byteLength - body)
      // Chunks are word-aligned, so an odd size is followed by a pad byte.
      offset = body + size + (size % 2)
    }

    if (!byteRate || !dataSize) return null
    return dataSize / byteRate
  } catch {
    return null
  }
}

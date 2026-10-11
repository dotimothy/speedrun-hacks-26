import { useEffect, useRef, useState } from 'react'
import { wavDuration } from '../audio/recorder'

interface Props {
  // Object URL for the current call, fixture or recording alike.
  src: string | null
  // The same audio as a blob, so the duration can be read from the WAV header
  // rather than waited on from the media element.
  blob: Blob | null
  label: string
}

function clock(seconds: number): string {
  if (!Number.isFinite(seconds)) return '0:00'
  const minutes = Math.floor(seconds / 60)
  return minutes + ':' + String(Math.floor(seconds % 60)).padStart(2, '0')
}

/**
 * Plays the call the dashboard is showing.
 *
 * Worth having for its own sake -- an audience that cannot hear the call has
 * to take the transcript on trust -- and worth having for checking the
 * system: when Whisper mishears a street name, this is how you find out it
 * was the audio rather than the model.
 */
export default function CallAudio({ src, blob, label }: Props) {
  const audio = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState(false)
  const [position, setPosition] = useState(0)
  const [duration, setDuration] = useState(0)

  // A new call replaces the old one; do not keep playing the previous audio
  // underneath a transcript that no longer matches it.
  useEffect(() => {
    setPlaying(false)
    setPosition(0)
    setDuration(0)
    audio.current?.pause()

    if (!blob) return
    let cancelled = false
    wavDuration(blob).then((seconds) => {
      // Only fill in what the element has not already reported.
      if (!cancelled && seconds) setDuration((current) => current || seconds)
    })
    return () => {
      cancelled = true
    }
  }, [src, blob])

  if (!src) {
    return (
      <section className="pane">
        <header className="pane-head">
          <h3>Call audio</h3>
        </header>
        <p className="muted">No audio loaded. Pick a scenario or record from the microphone.</p>
      </section>
    )
  }

  const toggle = async () => {
    const element = audio.current
    if (!element) return
    if (element.paused) {
      await element.play()
      setPlaying(true)
    } else {
      element.pause()
      setPlaying(false)
    }
  }

  const seek = (event: React.ChangeEvent<HTMLInputElement>) => {
    const element = audio.current
    if (!element) return
    element.currentTime = Number(event.target.value)
    setPosition(element.currentTime)
  }

  return (
    <section className="pane">
      <header className="pane-head">
        <h3>Call audio</h3>
        <span className="chip">{label}</span>
      </header>

      <audio
        ref={audio}
        src={src}
        preload="metadata"
        onLoadedMetadata={(event) => {
          const reported = event.currentTarget.duration
          if (Number.isFinite(reported) && reported > 0) setDuration(reported)
        }}
        onTimeUpdate={(event) => setPosition(event.currentTarget.currentTime)}
        onEnded={() => setPlaying(false)}
        onPause={() => setPlaying(false)}
        onPlay={() => setPlaying(true)}
      />

      <div className="player">
        <button className="play" onClick={toggle} aria-label={playing ? 'Pause' : 'Play'}>
          {playing ? '❚❚' : '▶'}
        </button>
        <input
          className="scrub"
          type="range"
          min={0}
          max={duration || 0}
          step={0.05}
          value={position}
          onChange={seek}
          aria-label="Seek"
        />
        <span className="clock">
          {clock(position)} / {clock(duration)}
        </span>
        <a className="link" href={src} download={label + '.wav'}>
          save wav
        </a>
      </div>
    </section>
  )
}

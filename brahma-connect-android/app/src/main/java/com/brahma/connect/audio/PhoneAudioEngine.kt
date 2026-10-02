package com.brahma.connect.audio

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.util.Base64
import android.util.Log
import androidx.core.content.ContextCompat
import com.brahma.connect.core.AgentStateStore
import java.util.Locale
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Turns the phone into Brahma's body: the microphone streams PCM16 16 kHz mono
 * frames to the gateway as `event { kind: "audio_in" }` messages and Gemini's
 * 24 kHz voice (`event { kind: "audio_out" }`) plays through [AudioTrack].
 *
 * The engine is half-duplex by design — the microphone is muted while Brahma is
 * speaking (plus a short tail) so the phone's own speaker never feeds back into
 * the model as a phantom user turn.
 */
class PhoneAudioEngine(
    private val context: Context,
    private val listener: Listener,
) {
    interface Listener {
        /** One frame of microphone audio, already base64 encoded. */
        fun onMicFrame(base64Pcm: String, sampleRate: Int)

        /** Playback finished or was interrupted; used for barge-in bookkeeping. */
        fun onPlaybackStateChanged(playing: Boolean)
    }

    companion object {
        private const val TAG = "PhoneAudioEngine"
        const val INPUT_SAMPLE_RATE = 16_000
        const val OUTPUT_SAMPLE_RATE = 24_000
        private const val FRAME_SAMPLES = 640            // 40 ms at 16 kHz
        private const val MIC_TAIL_MS = 400L             // keep mic muted after speech
    }

    private val running = AtomicBoolean(false)
    private val speaking = AtomicBoolean(false)
    @Volatile private var micMutedUntil = 0L
    @Volatile private var capturingEnabled = true

    private var record: AudioRecord? = null
    private var captureThread: Thread? = null
    private var playback: AudioTrack? = null
    private var playbackThread: Thread? = null
    private val playbackQueue = LinkedBlockingQueue<ByteArray>(64)

    private var tts: TextToSpeech? = null
    private val mainHandler = Handler(Looper.getMainLooper())

    // ── permissions ──────────────────────────────────────────────────────────

    fun hasMicrophonePermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    // ── lifecycle ────────────────────────────────────────────────────────────

    /** Start capturing the microphone (no-op without RECORD_AUDIO permission). */
    fun start() {
        if (!hasMicrophonePermission()) {
            AgentStateStore.addLog("Microphone permission missing; voice input disabled.")
            return
        }
        if (!running.compareAndSet(false, true)) return
        startCapture()
        startPlayback()
        AgentStateStore.addLog("Voice link online (16 kHz in / 24 kHz out).")
    }

    fun stop() {
        if (!running.compareAndSet(true, false)) return
        captureThread?.interrupt()
        captureThread = null
        runCatching { record?.stop() }
        runCatching { record?.release() }
        record = null
        playbackQueue.clear()
        runCatching { playback?.pause() }
        runCatching { playback?.flush() }
        runCatching { playback?.release() }
        playback = null
        playbackThread?.interrupt()
        playbackThread = null
        speaking.set(false)
    }

    /** Called when the user sends a message: stop Brahma mid-sentence. */
    fun bargeIn() {
        playbackQueue.clear()
        runCatching { playback?.pause() }
        runCatching { playback?.flush() }
        runCatching { playback?.play() }
        speaking.set(false)
        micMutedUntil = 0L
        listener.onPlaybackStateChanged(false)
    }

    fun setCapturingEnabled(enabled: Boolean) {
        capturingEnabled = enabled
    }

    /** Accept one base64 PCM16 chunk from the gateway and play it. */
    fun enqueueAudio(base64Pcm: String) {
        if (!running.get()) return
        val bytes = runCatching { Base64.decode(base64Pcm, Base64.DEFAULT) }.getOrNull() ?: return
        if (bytes.isEmpty()) return
        micMutedUntil = System.currentTimeMillis() + MIC_TAIL_MS
        if (!speaking.getAndSet(true)) {
            listener.onPlaybackStateChanged(true)
        }
        if (!playbackQueue.offer(bytes)) {
            // Backlog: drop the oldest frame to keep latency low.
            playbackQueue.poll()
            playbackQueue.offer(bytes)
        }
    }

    /** Speak a short line with the platform TTS voice (fallback / UI notices). */
    fun speak(text: String) {
        if (text.isBlank()) return
        if (tts == null) {
            tts = TextToSpeech(context) { status ->
                if (status == TextToSpeech.SUCCESS) {
                    tts?.language = Locale.getDefault()
                }
            }
        }
        micMutedUntil = System.currentTimeMillis() + MIC_TAIL_MS
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "brahma-speak")
    }

    // ── capture ──────────────────────────────────────────────────────────────

    private fun startCapture() {
        val minBuffer = AudioRecord.getMinBufferSize(
            INPUT_SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferSize = maxOf(minBuffer, FRAME_SAMPLES * 2 * 4)
        val recorder = try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                AudioRecord.Builder()
                    .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
                    .setAudioFormat(
                        AudioFormat.Builder()
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(INPUT_SAMPLE_RATE)
                            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                            .build()
                    )
                    .setBufferSizeInBytes(bufferSize)
                    .build()
            } else {
                @Suppress("DEPRECATION")
                AudioRecord(
                    MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    INPUT_SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    bufferSize,
                )
            }
        } catch (t: Throwable) {
            Log.w(TAG, "Microphone unavailable: ${t.message}")
            AgentStateStore.addLog("Microphone unavailable on this device.")
            return
        }
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            runCatching { recorder.release() }
            AgentStateStore.addLog("Microphone could not be initialised.")
            return
        }
        record = recorder
        runCatching { recorder.startRecording() }

        captureThread = Thread({
            val buffer = ByteArray(FRAME_SAMPLES * 2)
            while (running.get()) {
                val read = try {
                    recorder.read(buffer, 0, buffer.size)
                } catch (t: Throwable) {
                    -1
                }
                if (read <= 0) {
                    if (read < 0) break
                    continue
                }
                if (!capturingEnabled) continue
                if (System.currentTimeMillis() < micMutedUntil) continue   // half duplex
                val frame = buffer.copyOf(read)
                val encoded = Base64.encodeToString(frame, Base64.NO_WRAP)
                listener.onMicFrame(encoded, INPUT_SAMPLE_RATE)
            }
        }, "brahma-mic").apply { isDaemon = true; start() }
    }

    // ── playback ─────────────────────────────────────────────────────────────

    private fun startPlayback() {
        val minBuffer = AudioTrack.getMinBufferSize(
            OUTPUT_SAMPLE_RATE,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val track = try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                AudioTrack.Builder()
                    .setAudioAttributes(
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_ASSISTANT)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                            .build()
                    )
                    .setAudioFormat(
                        AudioFormat.Builder()
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(OUTPUT_SAMPLE_RATE)
                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                            .build()
                    )
                    .setBufferSizeInBytes(maxOf(minBuffer, OUTPUT_SAMPLE_RATE))
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build()
            } else {
                @Suppress("DEPRECATION")
                AudioTrack(
                    AudioManager.STREAM_MUSIC,
                    OUTPUT_SAMPLE_RATE,
                    AudioFormat.CHANNEL_OUT_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    maxOf(minBuffer, OUTPUT_SAMPLE_RATE),
                    AudioTrack.MODE_STREAM,
                )
            }
        } catch (t: Throwable) {
            Log.w(TAG, "Speaker unavailable: ${t.message}")
            return
        }
        playback = track
        runCatching { track.play() }

        playbackThread = Thread({
            while (running.get()) {
                val chunk = playbackQueue.poll()
                if (chunk == null) {
                    if (speaking.get()) {
                        speaking.set(false)
                        listener.onPlaybackStateChanged(false)
                    }
                    try {
                        Thread.sleep(20)
                    } catch (t: InterruptedException) {
                        break
                    }
                    continue
                }
                try {
                    track.write(chunk, 0, chunk.size)
                } catch (t: Throwable) {
                    Log.w(TAG, "Playback error: ${t.message}")
                    break
                }
            }
        }, "brahma-speaker").apply { isDaemon = true; start() }
    }

    fun release() {
        stop()
        mainHandler.post {
            tts?.stop()
            tts?.shutdown()
            tts = null
        }
    }
}

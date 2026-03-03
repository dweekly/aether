package com.aether.dualmictest

import android.annotation.SuppressLint
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Process
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.sqrt

class AudioRecordThread(
    private val label: String,
    private val device: AudioDeviceInfo,
    private val outputFile: File,
    private val onLevel: (Float) -> Unit,
    private val onStatus: (String) -> Unit,
    private val onEvent: (String) -> Unit
) : Thread("AudioRecordThread-$label") {

    private val running = AtomicBoolean(false)

    companion object {
        const val SAMPLE_RATE = 44100
        const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
    }

    fun stopRecording() {
        running.set(false)
    }

    val isRunning: Boolean get() = running.get()

    @SuppressLint("MissingPermission")
    override fun run() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)

        val minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
        if (minBuf <= 0) {
            onEvent("[$label] ERROR: getMinBufferSize=$minBuf")
            onStatus("ERROR: bad buffer size")
            return
        }

        val bufferSize = minBuf * 4
        val recorder: AudioRecord
        try {
            recorder = AudioRecord(
                MediaRecorder.AudioSource.MIC,
                SAMPLE_RATE,
                CHANNEL_CONFIG,
                AUDIO_FORMAT,
                bufferSize
            )
        } catch (e: Exception) {
            onEvent("[$label] ERROR creating AudioRecord: ${e.message}")
            onStatus("ERROR: init failed")
            return
        }

        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            onEvent("[$label] ERROR: AudioRecord not initialized (state=${recorder.state})")
            onStatus("ERROR: not initialized")
            recorder.release()
            return
        }

        val setOk = recorder.setPreferredDevice(device)
        onEvent("[$label] setPreferredDevice(${device.id})=$setOk")

        // Register callback to detect silencing/rerouting
        val sessionId = recorder.audioSessionId
        recorder.registerAudioRecordingCallback(
            { it.run() },
            object : AudioManager.AudioRecordingCallback() {
                override fun onRecordingConfigChanged(configs: MutableList<android.media.AudioRecordingConfiguration>?) {
                    val routed = recorder.routedDevice
                    val routedStr = if (routed != null) "ID=${routed.id} type=${routed.type}" else "null"
                    val myConfig = configs?.firstOrNull { it.clientAudioSessionId == sessionId }
                    val silenced = myConfig?.isClientSilenced ?: false
                    val msg = "[$label] configChanged: routed=$routedStr silenced=$silenced"
                    onEvent(msg)
                    if (silenced) onStatus("SILENCED by system")
                }
            }
        )

        val wavWriter = WavWriter(outputFile, SAMPLE_RATE, 1, 16)
        wavWriter.start()
        onEvent("[$label] WAV: ${outputFile.name}")

        recorder.startRecording()
        running.set(true)

        val routed = recorder.routedDevice
        val routedStr = if (routed != null) "ID=${routed.id} type=${routed.type}" else "null"
        onEvent("[$label] recording started, routed=$routedStr")
        onStatus("Recording… routed=$routedStr")

        val buf = ShortArray(minBuf)
        while (running.get()) {
            val read = recorder.read(buf, 0, buf.size)
            if (read > 0) {
                wavWriter.write(buf, read)
                val rms = computeRms(buf, read)
                onLevel(rms)
            } else if (read < 0) {
                onEvent("[$label] read error=$read")
                break
            }
        }

        recorder.stop()
        recorder.release()
        wavWriter.finish()

        onEvent("[$label] stopped, file=${outputFile.length()} bytes")
        onStatus("Stopped")
        onLevel(0f)
    }

    private fun computeRms(samples: ShortArray, count: Int): Float {
        var sum = 0.0
        for (i in 0 until count) {
            val s = samples[i].toDouble()
            sum += s * s
        }
        return (sqrt(sum / count) / Short.MAX_VALUE).toFloat()
    }
}

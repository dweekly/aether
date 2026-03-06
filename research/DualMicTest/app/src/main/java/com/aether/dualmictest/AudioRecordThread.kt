package com.aether.dualmictest

import android.annotation.SuppressLint
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Process
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.sqrt

class AudioRecordThread(
    private val label: String,
    private val device: AudioDeviceInfo?,
    private val audioSource: Int,
    private val outputFile: File,
    private val onLevel: (Float) -> Unit,
    private val onStatus: (String) -> Unit,
    private val onEvent: (String) -> Unit
) : Thread("AudioRecordThread-$label") {

    private val running = AtomicBoolean(false)

    /** After recording stops, holds the routed device type (or -1). */
    var routedDeviceType: Int = -1
        private set
    var routedDeviceId: Int = -1
        private set
    var wasSilenced: Boolean = false
        private set
    var initError: String? = null
        private set

    companion object {
        const val SAMPLE_RATE = 44100
        const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT

        fun audioSourceName(source: Int): String = when (source) {
            MediaRecorder.AudioSource.DEFAULT -> "DEFAULT"
            MediaRecorder.AudioSource.MIC -> "MIC"
            MediaRecorder.AudioSource.CAMCORDER -> "CAMCORDER"
            MediaRecorder.AudioSource.VOICE_RECOGNITION -> "VOICE_RECOGNITION"
            MediaRecorder.AudioSource.VOICE_COMMUNICATION -> "VOICE_COMMUNICATION"
            9 -> "UNPROCESSED"
            else -> "SOURCE_$source"
        }
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
            initError = "getMinBufferSize=$minBuf"
            onEvent("[$label] ERROR: getMinBufferSize=$minBuf")
            onStatus("ERROR: bad buffer size")
            return
        }

        val bufferSize = minBuf * 4
        val recorder: AudioRecord
        try {
            recorder = AudioRecord(
                audioSource,
                SAMPLE_RATE,
                CHANNEL_CONFIG,
                AUDIO_FORMAT,
                bufferSize
            )
        } catch (e: Exception) {
            initError = e.message ?: "unknown"
            onEvent("[$label] ERROR creating AudioRecord: ${e.message}")
            onStatus("ERROR: init failed")
            return
        }

        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            initError = "state=${recorder.state}"
            onEvent("[$label] ERROR: AudioRecord not initialized (state=${recorder.state})")
            onStatus("ERROR: not initialized")
            recorder.release()
            return
        }

        if (device != null) {
            val setOk = recorder.setPreferredDevice(device)
            onEvent("[$label] setPreferredDevice(${device.id})=$setOk")
        }

        val sessionId = recorder.audioSessionId
        recorder.registerAudioRecordingCallback(
            { it.run() },
            object : AudioManager.AudioRecordingCallback() {
                override fun onRecordingConfigChanged(configs: MutableList<android.media.AudioRecordingConfiguration>?) {
                    val routed = recorder.routedDevice
                    val routedStr = if (routed != null) "ID=${routed.id} type=${routed.type}" else "null"
                    val myConfig = configs?.firstOrNull { it.clientAudioSessionId == sessionId }
                    val silenced = myConfig?.isClientSilenced ?: false
                    if (silenced) wasSilenced = true
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
        if (routed != null) {
            routedDeviceType = routed.type
            routedDeviceId = routed.id
        }
        val routedStr = if (routed != null) "ID=${routed.id} type=${routed.type}" else "null"
        onEvent("[$label] recording started, source=${audioSourceName(audioSource)}, routed=$routedStr")
        onStatus("Rec ${audioSourceName(audioSource)} routed=$routedStr")

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

        // Capture final routed device
        val finalRouted = recorder.routedDevice
        if (finalRouted != null) {
            routedDeviceType = finalRouted.type
            routedDeviceId = finalRouted.id
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

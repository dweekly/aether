package com.aether.dualmictest

import android.Manifest
import android.content.pm.PackageManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Bundle
import android.os.Environment
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var tvDeviceList: TextView
    private lateinit var btnRecord: Button
    private lateinit var pbLevel1: ProgressBar
    private lateinit var tvStatus1: TextView
    private lateinit var pbLevel2: ProgressBar
    private lateinit var tvStatus2: TextView
    private lateinit var tvLog: TextView

    private val audioManager by lazy { getSystemService(AUDIO_SERVICE) as AudioManager }

    private var thread1: AudioRecordThread? = null
    private var thread2: AudioRecordThread? = null
    private var recording = false

    companion object {
        private const val REQ_AUDIO = 1001
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tvDeviceList = findViewById(R.id.tvDeviceList)
        btnRecord = findViewById(R.id.btnRecord)
        pbLevel1 = findViewById(R.id.pbLevel1)
        tvStatus1 = findViewById(R.id.tvStatus1)
        pbLevel2 = findViewById(R.id.pbLevel2)
        tvStatus2 = findViewById(R.id.tvStatus2)
        tvLog = findViewById(R.id.tvLog)

        enumerateDevices()

        btnRecord.setOnClickListener {
            if (!recording) startRecording() else stopRecording()
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO), REQ_AUDIO)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_AUDIO) {
            if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
                log("RECORD_AUDIO granted")
            } else {
                log("RECORD_AUDIO DENIED — app cannot function")
                btnRecord.isEnabled = false
            }
        }
    }

    private fun enumerateDevices() {
        tvDeviceList.text = DeviceUtils.formatAllInputDevices(audioManager)
    }

    private fun startRecording() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            log("Permission not granted")
            return
        }

        val builtinMic = DeviceUtils.findDeviceByType(audioManager, AudioDeviceInfo.TYPE_BUILTIN_MIC)
        val wiredHeadset = DeviceUtils.findDeviceByType(audioManager, AudioDeviceInfo.TYPE_WIRED_HEADSET)

        if (builtinMic == null) {
            log("WARNING: no BUILTIN_MIC found")
        }
        if (wiredHeadset == null) {
            log("WARNING: no WIRED_HEADSET found — plug in a TRRS headset")
        }

        val ts = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val outDir = getExternalFilesDir(Environment.DIRECTORY_MUSIC) ?: filesDir

        if (builtinMic != null) {
            val file1 = File(outDir, "builtin_${ts}.wav")
            thread1 = AudioRecordThread(
                label = "Builtin",
                device = builtinMic,
                outputFile = file1,
                onLevel = { rms -> runOnUiThread { pbLevel1.progress = (rms * 1000).toInt() } },
                onStatus = { s -> runOnUiThread { tvStatus1.text = s } },
                onEvent = { msg -> runOnUiThread { log(msg) } }
            ).also { it.start() }
        } else {
            tvStatus1.text = "No device"
        }

        if (wiredHeadset != null) {
            val file2 = File(outDir, "headset_${ts}.wav")
            thread2 = AudioRecordThread(
                label = "Headset",
                device = wiredHeadset,
                outputFile = file2,
                onLevel = { rms -> runOnUiThread { pbLevel2.progress = (rms * 1000).toInt() } },
                onStatus = { s -> runOnUiThread { tvStatus2.text = s } },
                onEvent = { msg -> runOnUiThread { log(msg) } }
            ).also { it.start() }
        } else {
            tvStatus2.text = "No device"
        }

        recording = true
        btnRecord.text = "Stop Recording"
        log("Recording started")
    }

    private fun stopRecording() {
        log("Stopping…")
        btnRecord.isEnabled = false

        thread1?.stopRecording()
        thread2?.stopRecording()

        Thread {
            thread1?.join(3000)
            thread2?.join(3000)
            thread1 = null
            thread2 = null

            runOnUiThread {
                recording = false
                btnRecord.text = "Start Recording"
                btnRecord.isEnabled = true
                log("Recording stopped")
            }
        }.start()
    }

    private fun log(msg: String) {
        val ts = SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(Date())
        val line = "$ts  $msg"
        val current = tvLog.text.toString()
        tvLog.text = if (current.isEmpty()) line else "$line\n$current"
    }

    override fun onDestroy() {
        super.onDestroy()
        thread1?.stopRecording()
        thread2?.stopRecording()
    }
}

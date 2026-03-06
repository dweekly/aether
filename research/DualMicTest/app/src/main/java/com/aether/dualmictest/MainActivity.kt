package com.aether.dualmictest

import android.Manifest
import android.content.pm.PackageManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.media.MediaRecorder
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
    private lateinit var btnCombo: Button
    private lateinit var pbLevel1: ProgressBar
    private lateinit var tvStatus1: TextView
    private lateinit var pbLevel2: ProgressBar
    private lateinit var tvStatus2: TextView
    private lateinit var tvLog: TextView

    private val audioManager by lazy { getSystemService(AUDIO_SERVICE) as AudioManager }

    private var thread1: AudioRecordThread? = null
    private var thread2: AudioRecordThread? = null
    private var recording = false
    private var comboRunning = false

    companion object {
        private const val REQ_AUDIO = 1001
        private const val COMBO_RECORD_SECONDS = 5

        val AUDIO_SOURCES = listOf(
            MediaRecorder.AudioSource.MIC,
            MediaRecorder.AudioSource.CAMCORDER,
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            9 // UNPROCESSED
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tvDeviceList = findViewById(R.id.tvDeviceList)
        btnRecord = findViewById(R.id.btnRecord)
        btnCombo = findViewById(R.id.btnCombo)
        pbLevel1 = findViewById(R.id.pbLevel1)
        tvStatus1 = findViewById(R.id.tvStatus1)
        pbLevel2 = findViewById(R.id.pbLevel2)
        tvStatus2 = findViewById(R.id.tvStatus2)
        tvLog = findViewById(R.id.tvLog)

        enumerateDevices()

        btnRecord.setOnClickListener {
            if (!recording) startRecording() else stopRecording()
        }

        btnCombo.setOnClickListener {
            if (!comboRunning) startComboTest()
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
                log("RECORD_AUDIO DENIED")
                btnRecord.isEnabled = false
                btnCombo.isEnabled = false
            }
        }
    }

    private fun enumerateDevices() {
        tvDeviceList.text = DeviceUtils.formatAllInputDevices(audioManager)
    }

    // --- Single recording: VOICE_COMMUNICATION on builtin (→routes to BUILT),
    //     CAMCORDER on headset (→routes to WIRED) based on combo test findings ---

    private fun startRecording() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            log("Permission not granted")
            return
        }

        val builtinMic = DeviceUtils.findDeviceByType(audioManager, AudioDeviceInfo.TYPE_BUILTIN_MIC)
        val wiredHeadset = DeviceUtils.findDeviceByType(audioManager, AudioDeviceInfo.TYPE_WIRED_HEADSET)

        if (builtinMic == null) log("WARNING: no BUILTIN_MIC found")
        if (wiredHeadset == null) log("WARNING: no WIRED_HEADSET found")

        val ts = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val outDir = getExternalFilesDir(Environment.DIRECTORY_MUSIC) ?: filesDir

        // VOICE_COMMUNICATION on headset device → HAL routes to BUILTIN mic
        if (wiredHeadset != null) {
            val file1 = File(outDir, "device_builtin_${ts}.wav")
            thread1 = AudioRecordThread(
                label = "DeviceBuiltin",
                device = wiredHeadset,
                audioSource = MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                outputFile = file1,
                onLevel = { rms -> runOnUiThread { pbLevel1.progress = (rms * 1000).toInt() } },
                onStatus = { s -> runOnUiThread { tvStatus1.text = s } },
                onEvent = { msg -> runOnUiThread { log(msg) } }
            ).also { it.start() }
        } else {
            tvStatus1.text = "No device"
        }

        // CAMCORDER on builtin device → HAL routes to WIRED headset
        if (builtinMic != null) {
            val file2 = File(outDir, "wired_mic_${ts}.wav")
            thread2 = AudioRecordThread(
                label = "WiredMic",
                device = builtinMic,
                audioSource = MediaRecorder.AudioSource.CAMCORDER,
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
        btnCombo.isEnabled = false
        log("Recording started")
    }

    private fun stopRecording() {
        log("Stopping...")
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
                btnCombo.isEnabled = true
                log("Recording stopped")
            }
        }.start()
    }

    // --- Combo test: all AudioSource pairs ---

    private fun startComboTest() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            log("Permission not granted")
            return
        }

        val builtinMic = DeviceUtils.findDeviceByType(audioManager, AudioDeviceInfo.TYPE_BUILTIN_MIC)
        val wiredHeadset = DeviceUtils.findDeviceByType(audioManager, AudioDeviceInfo.TYPE_WIRED_HEADSET)

        if (builtinMic == null) {
            log("COMBO: no BUILTIN_MIC found, aborting")
            return
        }
        if (wiredHeadset == null) {
            log("COMBO: no WIRED_HEADSET found, aborting")
            return
        }

        comboRunning = true
        btnCombo.isEnabled = false
        btnRecord.isEnabled = false

        val combos = mutableListOf<Pair<Int, Int>>()
        for (src1 in AUDIO_SOURCES) {
            for (src2 in AUDIO_SOURCES) {
                combos.add(src1 to src2)
            }
        }

        val ts = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val outDir = File(getExternalFilesDir(Environment.DIRECTORY_MUSIC), "combo_$ts").apply { mkdirs() }

        log("=== COMBO TEST: ${combos.size} pairs, ${COMBO_RECORD_SECONDS}s each ===")
        log("Output: combo_$ts/")

        Thread {
            val results = mutableListOf<String>()
            results.add("builtin_source,headset_source,builtin_routed_type,headset_routed_type,builtin_silenced,headset_silenced,builtin_error,headset_error,builtin_file,headset_file")

            for ((idx, combo) in combos.withIndex()) {
                val (src1, src2) = combo
                val src1Name = AudioRecordThread.audioSourceName(src1)
                val src2Name = AudioRecordThread.audioSourceName(src2)

                runOnUiThread {
                    log("--- [${idx + 1}/${combos.size}] builtin=$src1Name headset=$src2Name ---")
                    tvStatus1.text = "Combo: $src1Name"
                    tvStatus2.text = "Combo: $src2Name"
                    pbLevel1.progress = 0
                    pbLevel2.progress = 0
                }

                val file1 = File(outDir, "builtin_${src1Name}_headset_${src2Name}_b.wav")
                val file2 = File(outDir, "builtin_${src1Name}_headset_${src2Name}_h.wav")

                val t1 = AudioRecordThread(
                    label = "B",
                    device = builtinMic,
                    audioSource = src1,
                    outputFile = file1,
                    onLevel = { rms -> runOnUiThread { pbLevel1.progress = (rms * 1000).toInt() } },
                    onStatus = { },
                    onEvent = { msg -> runOnUiThread { log(msg) } }
                )
                val t2 = AudioRecordThread(
                    label = "H",
                    device = wiredHeadset,
                    audioSource = src2,
                    outputFile = file2,
                    onLevel = { rms -> runOnUiThread { pbLevel2.progress = (rms * 1000).toInt() } },
                    onStatus = { },
                    onEvent = { msg -> runOnUiThread { log(msg) } }
                )

                t1.start()
                t2.start()

                Thread.sleep(COMBO_RECORD_SECONDS * 1000L)

                t1.stopRecording()
                t2.stopRecording()
                t1.join(3000)
                t2.join(3000)

                val row = listOf(
                    src1Name, src2Name,
                    t1.routedDeviceType.toString(), t2.routedDeviceType.toString(),
                    t1.wasSilenced.toString(), t2.wasSilenced.toString(),
                    t1.initError ?: "", t2.initError ?: "",
                    file1.name, file2.name
                ).joinToString(",")
                results.add(row)

                runOnUiThread {
                    log("  builtin routed=${t1.routedDeviceType} silenced=${t1.wasSilenced} err=${t1.initError ?: "none"}")
                    log("  headset routed=${t2.routedDeviceType} silenced=${t2.wasSilenced} err=${t2.initError ?: "none"}")
                }

                // Brief pause between combos to let HAL settle
                Thread.sleep(500)
            }

            // Write CSV results
            val csvFile = File(outDir, "results.csv")
            csvFile.writeText(results.joinToString("\n"))

            runOnUiThread {
                log("=== COMBO TEST COMPLETE ===")
                log("CSV: ${csvFile.absolutePath}")
                log("${combos.size} combos tested")
                comboRunning = false
                btnCombo.isEnabled = true
                btnRecord.isEnabled = true
                tvStatus1.text = "Done"
                tvStatus2.text = "Done"
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

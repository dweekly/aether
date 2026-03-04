# DualMicTest

Android app to determine whether the Moto G 2026 (MediaTek Dimensity 6300, MT6365 PMIC) can simultaneously record from the built-in mic and a TRRS wired headset mic as independent audio streams. This is the foundational hardware validation for Aether's [GA-CVR](../../Product%20Requirements%20Document.md) dual-stream cockpit voice recorder.

## Background

The MT6365 PMIC has separate analog input pins — AU_VIN0/VIN2 for the built-in mic, AU_VIN1 for the headset — with independent PGA paths. The hardware *should* support concurrent capture, but Android's audio HAL typically muxes inputs. This app definitively answers the question.

## How it works

1. Enumerates all `AudioDeviceInfo` inputs and displays them
2. Creates two `AudioRecordThread`s, each with its own `AudioRecord` instance:
   - **Stream 1:** pinned to `TYPE_BUILTIN_MIC` via `setPreferredDevice()`
   - **Stream 2:** pinned to `TYPE_WIRED_HEADSET` via `setPreferredDevice()`
3. Both threads record 44.1 kHz / 16-bit mono PCM to separate timestamped WAV files in `getExternalFilesDir(DIRECTORY_MUSIC)`
4. Real-time RMS level meters show whether each stream is receiving distinct audio
5. `AudioRecordingCallback` monitors routing changes and silencing events

## Possible outcomes

| # | Result | Indicator | Meaning |
|---|--------|-----------|---------|
| 1 | Both capture distinct audio | Independent level meter activity | HAL routes inputs independently — GA-CVR works |
| 2 | Both capture identical audio | Meters move in sync; `getRoutedDevice()` same for both | HAL muxes to one source |
| 3 | One stream silenced | One meter flat; `isClientSilenced()=true` in log | Concurrency framework silencing |
| 4 | Second AudioRecord fails | Error in event log at init | HAL only supports one input stream |

## Test procedure

1. Build and install:
   ```bash
   ./gradlew assembleDebug
   adb install app/build/outputs/apk/debug/app-debug.apk
   ```
2. Plug in a TRRS headset with mic
3. Launch "Dual Mic Test", grant RECORD_AUDIO permission
4. Verify device list shows both `BUILTIN_MIC` and `WIRED_HEADSET`
5. Tap **Start Recording**
6. Speak into the phone mic, then into the headset mic — watch the level meters
7. Tap **Stop Recording**
8. Pull and compare the WAV files:
   ```bash
   adb pull /sdcard/Android/data/com.aether.dualmictest/files/Music/
   ```
9. Open both WAVs in Audacity and verify distinct waveforms

## Project structure

```
├── app/build.gradle.kts              minSdk 29, targetSdk 35, no Compose
└── app/src/main/
    ├── AndroidManifest.xml           RECORD_AUDIO only
    ├── res/layout/activity_main.xml  ScrollView with level meters + event log
    ├── res/values/strings.xml
    └── java/com/aether/dualmictest/
        ├── MainActivity.kt           UI, permissions, thread lifecycle
        ├── AudioRecordThread.kt      AudioRecord + setPreferredDevice + RMS + WAV
        ├── WavWriter.kt              Streams PCM to WAV, writes header on finish
        └── DeviceUtils.kt            Enumerate and format AudioDeviceInfo
```

Zero third-party dependencies (core-ktx + appcompat only).

## Findings

*Awaiting device arrival — results will be recorded here.*

# DualMicTest

Android app to determine whether the Moto G 2026 (MediaTek Dimensity 6300, MT6365 PMIC) can simultaneously record from the built-in mic and a TRRS wired headset mic as independent audio streams. This is the foundational hardware validation for Aether's [GA-CVR](../../prd.md) dual-stream cockpit voice recorder.

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

**Outcome #2 — HAL muxes both streams to one physical input. No AudioSource combination produces true dual-input capture on this device.**

Tested 2026-03-05 on Moto G 2026 (`nevada_g_sysu` / `utah`, MediaTek Dimensity 6300, MT6365 PMIC) with a TRRS microphone, connected via WiFi ADB.

### Test 1: Default dual AudioRecord (both MIC source)

- Both `AudioRecord` instances started without error (`status 0`), no `isClientSilenced` events
- `setPreferredDevice()` accepted both `TYPE_BUILTIN_MIC` and `TYPE_WIRED_HEADSET` without error
- **Cross-correlation: r = 1.00 at 880-sample (20 ms) lag** — same signal, different pipeline delay
- At zero lag, Pearson r = −0.12 (misleadingly low due to phase offset)
- The HAL ignores `setPreferredDevice()` and routes both `AudioRecord` instances to the same physical input

### Test 2: Exhaustive AudioSource combo sweep (5x5 = 25 pairs)

Tested all pairs of: `MIC`, `CAMCORDER`, `VOICE_RECOGNITION`, `VOICE_COMMUNICATION`, `UNPROCESSED` — one assigned to each device. 5-second recordings per combo with cross-correlation analysis.

| Builtin source | Headset source | B→routed | H→routed | Correlation | Verdict |
|---|---|---|---|---|---|
| MIC | MIC | WIRED | WIRED | 1.00 | Same signal |
| MIC | CAMCORDER | WIRED | WIRED | 1.00 | Same signal |
| MIC | VOICE_RECOGNITION | WIRED | WIRED | 1.00 | Same signal |
| MIC | VOICE_COMMUNICATION | WIRED | **BUILT** | 0.61 | Appears different |
| MIC | UNPROCESSED | BUILT | BUILT | 1.00 | Same signal |
| CAMCORDER | VOICE_COMMUNICATION | WIRED | **BUILT** | 0.04 | Appears different |
| VOICE_RECOGNITION | VOICE_COMMUNICATION | WIRED | **BUILT** | 0.04 | Appears different |
| VOICE_COMMUNICATION | VOICE_COMMUNICATION | BUILT | BUILT | 0.06 | Appears different |
| *(all other non-VOICECOMM pairs)* | | *same* | *same* | *~1.00* | Same signal |

`VOICE_COMMUNICATION` was the only AudioSource that caused `getRoutedDevice()` to report different device types. All other combos routed both streams identically.

### Test 3: Proof test — physical mic isolation

Used the most promising combo (`CAMCORDER` → wired, `VOICE_COMMUNICATION` → builtin) for a 34-second recording. User blew directly into the wired TRRS mic during seconds 10–22 to create a loud, localized signal.

**Result: both streams captured the same wired mic audio.** The `VOICE_COMMUNICATION` stream applied heavy DSP (noise gate, echo cancellation) that zeroed out quiet sections, creating the illusion of independence. But during loud segments, the same signal appeared on both streams. The HAL reports `BUILTIN_MIC` as the routed device for the VOICE_COMMUNICATION stream, but the actual PCM comes from the wired mic.

Overall cross-correlation: r = 0.11 (low only because of VOICE_COMMUNICATION's aggressive noise gating on quiet segments, not because of different source audio).

### Conclusions

1. **`setPreferredDevice()` is silently ignored** — the HAL always routes to whichever single physical input it prefers (wired mic when plugged in)
2. **`getRoutedDevice()` is unreliable** — reports different device types for `VOICE_COMMUNICATION` streams but the actual audio comes from the same physical input
3. **No AudioSource combination achieves true dual-input capture** on this MediaTek HAL
4. The MT6365 PMIC's separate analog input paths (AU_VIN0/VIN2 vs AU_VIN1) are not exposed through Android's audio framework

### Next steps for GA-CVR

1. **USB audio class device** — use a USB-C audio interface as the second input; gets its own HAL stream entirely outside the PMIC path
2. **AAudio / Oboe (native)** — bypass the Java AudioRecord layer; may allow direct HAL stream routing
3. **Custom AudioPolicy** — requires rooted device or OEM partnership to modify `audio_policy_configuration.xml`
4. **Different device** — test on a Pixel or Samsung with known multi-stream HAL support

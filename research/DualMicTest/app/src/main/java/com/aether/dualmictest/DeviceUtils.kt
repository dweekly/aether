package com.aether.dualmictest

import android.media.AudioDeviceInfo
import android.media.AudioManager

object DeviceUtils {

    fun getInputDevices(audioManager: AudioManager): Array<AudioDeviceInfo> =
        audioManager.getDevices(AudioManager.GET_DEVICES_INPUTS)

    fun findDeviceByType(audioManager: AudioManager, type: Int): AudioDeviceInfo? =
        getInputDevices(audioManager).firstOrNull { it.type == type }

    fun formatDevice(d: AudioDeviceInfo): String = buildString {
        append("ID=${d.id}  type=${typeName(d.type)}")
        if (d.productName.isNotEmpty()) append("  product=\"${d.productName}\"")
        if (d.address.isNotEmpty()) append("  addr=\"${d.address}\"")
        val rates = d.sampleRates
        if (rates.isNotEmpty()) append("  rates=${rates.joinToString(",")}")
        val chCounts = d.channelCounts
        if (chCounts.isNotEmpty()) append("  ch=${chCounts.joinToString(",")}")
    }

    fun formatAllInputDevices(audioManager: AudioManager): String {
        val devices = getInputDevices(audioManager)
        if (devices.isEmpty()) return "(no input devices found)"
        return devices.joinToString("\n") { formatDevice(it) }
    }

    private fun typeName(type: Int): String = when (type) {
        AudioDeviceInfo.TYPE_BUILTIN_MIC -> "BUILTIN_MIC"
        AudioDeviceInfo.TYPE_WIRED_HEADSET -> "WIRED_HEADSET"
        AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "BT_SCO"
        AudioDeviceInfo.TYPE_BLUETOOTH_A2DP -> "BT_A2DP"
        AudioDeviceInfo.TYPE_USB_DEVICE -> "USB_DEVICE"
        AudioDeviceInfo.TYPE_USB_HEADSET -> "USB_HEADSET"
        AudioDeviceInfo.TYPE_TELEPHONY -> "TELEPHONY"
        AudioDeviceInfo.TYPE_IP -> "IP"
        AudioDeviceInfo.TYPE_BUS -> "BUS"
        AudioDeviceInfo.TYPE_REMOTE_SUBMIX -> "REMOTE_SUBMIX"
        else -> "UNKNOWN($type)"
    }
}

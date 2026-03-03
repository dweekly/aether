package com.aether.dualmictest

import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder

class WavWriter(
    private val file: File,
    private val sampleRate: Int = 44100,
    private val channels: Int = 1,
    private val bitsPerSample: Int = 16
) {
    private var raf: RandomAccessFile? = null
    private var dataBytes: Long = 0

    fun start() {
        raf = RandomAccessFile(file, "rw").apply {
            // Write 44-byte placeholder header
            write(ByteArray(44))
        }
        dataBytes = 0
    }

    fun write(samples: ShortArray, count: Int) {
        val raf = this.raf ?: return
        val buf = ByteBuffer.allocate(count * 2).order(ByteOrder.LITTLE_ENDIAN)
        for (i in 0 until count) {
            buf.putShort(samples[i])
        }
        raf.write(buf.array(), 0, count * 2)
        dataBytes += count * 2
    }

    fun finish() {
        val raf = this.raf ?: return
        val byteRate = sampleRate * channels * bitsPerSample / 8
        val blockAlign = channels * bitsPerSample / 8

        raf.seek(0)
        val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN).apply {
            // RIFF header
            put('R'.code.toByte()); put('I'.code.toByte())
            put('F'.code.toByte()); put('F'.code.toByte())
            putInt((36 + dataBytes).toInt()) // chunk size
            put('W'.code.toByte()); put('A'.code.toByte())
            put('V'.code.toByte()); put('E'.code.toByte())

            // fmt subchunk
            put('f'.code.toByte()); put('m'.code.toByte())
            put('t'.code.toByte()); put(' '.code.toByte())
            putInt(16)                      // subchunk1 size
            putShort(1)                     // PCM format
            putShort(channels.toShort())
            putInt(sampleRate)
            putInt(byteRate)
            putShort(blockAlign.toShort())
            putShort(bitsPerSample.toShort())

            // data subchunk
            put('d'.code.toByte()); put('a'.code.toByte())
            put('t'.code.toByte()); put('a'.code.toByte())
            putInt(dataBytes.toInt())
        }
        raf.write(header.array())
        raf.close()
        this.raf = null
    }
}

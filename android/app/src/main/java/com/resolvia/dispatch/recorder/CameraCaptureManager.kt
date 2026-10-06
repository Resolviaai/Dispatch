package com.resolvia.dispatch.recorder

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.util.Log
import androidx.camera.core.CameraSelector
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.*
import androidx.camera.view.PreviewView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.io.File

/**
 * CameraX Video Capture Engine for POCO C65 and Android devices.
 * Streams camera frames to PreviewView and records FHD 1080p MP4 segments with audio.
 */
class CameraCaptureManager(private val context: Context) {

    companion object {
        private const val TAG = "CameraCaptureManager"
    }

    private var videoCapture: VideoCapture<Recorder>? = null
    private var activeRecording: Recording? = null
    var isRecording: Boolean = false
        private set

    /**
     * Binds CameraX Preview and VideoCapture to the given LifecycleOwner and PreviewView.
     */
    fun initializeCamera(
        lifecycleOwner: LifecycleOwner,
        previewView: PreviewView,
        onReady: (() -> Unit)? = null
    ) {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        cameraProviderFuture.addListener({
            try {
                val cameraProvider = cameraProviderFuture.get()

                val preview = Preview.Builder().build().also {
                    it.setSurfaceProvider(previewView.surfaceProvider)
                }

                val qualitySelector = QualitySelector.from(
                    Quality.FHD,
                    FallbackStrategy.lowerQualityOrHigherThan(Quality.FHD)
                )

                val recorder = Recorder.Builder()
                    .setQualitySelector(qualitySelector)
                    .build()

                videoCapture = VideoCapture.withOutput(recorder)

                val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA

                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(
                    lifecycleOwner,
                    cameraSelector,
                    preview,
                    videoCapture
                )

                Log.i(TAG, "CameraX FHD VideoCapture successfully bound to lifecycle.")
                onReady?.invoke()

            } catch (exc: Exception) {
                Log.e(TAG, "Failed to bind CameraX lifecycle: ${exc.message}", exc)
            }
        }, ContextCompat.getMainExecutor(context))
    }

    /**
     * Starts recording into the target segment .tmp file.
     */
    fun startSegmentRecording(
        targetTmpFile: File,
        onFinalized: (file: File, durationMs: Long) -> Unit
    ) {
        val vc = videoCapture ?: run {
            Log.e(TAG, "VideoCapture not initialized")
            return
        }

        val outputOptions = FileOutputOptions.Builder(targetTmpFile).build()
        val pendingRecording = vc.output.prepareRecording(context, outputOptions)

        if (ActivityCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            pendingRecording.withAudioEnabled()
        }

        isRecording = true
        activeRecording = pendingRecording.start(ContextCompat.getMainExecutor(context)) { recordEvent ->
            when (recordEvent) {
                is VideoRecordEvent.Start -> {
                    Log.i(TAG, "Started recording video segment: ${targetTmpFile.name}")
                }
                is VideoRecordEvent.Finalize -> {
                    isRecording = false
                    val durationMs = recordEvent.recordingStats.recordedDurationNanos / 1_000_000
                    if (!recordEvent.hasError()) {
                        Log.i(TAG, "Segment finalized successfully: ${targetTmpFile.name} (${durationMs}ms, ${targetTmpFile.length()} bytes)")
                        onFinalized(targetTmpFile, durationMs)
                    } else {
                        Log.e(TAG, "Segment recording failed with error code: ${recordEvent.error}", recordEvent.cause)
                    }
                }
            }
        }
    }

    /**
     * Stops the currently active segment recording.
     */
    fun stopActiveRecording() {
        if (activeRecording != null) {
            activeRecording?.stop()
            activeRecording = null
            isRecording = false
        }
    }
}

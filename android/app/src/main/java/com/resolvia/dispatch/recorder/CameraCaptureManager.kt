package com.resolvia.dispatch.recorder

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.util.Log
import android.util.Range
import androidx.camera.core.Camera
import androidx.camera.core.CameraControl
import androidx.camera.core.CameraInfo
import androidx.camera.core.CameraSelector
import androidx.camera.core.FocusMeteringAction
import androidx.camera.core.MeteringPoint
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.*
import androidx.camera.view.PreviewView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * CameraX Video Capture Engine for POCO C65 and Android devices.
 * Streams camera frames to PreviewView and records FHD 1080p MP4 segments with audio.
 * Supports: Front/Back camera, Tap-to-Focus, AE/AF Lock, Exposure Bias, Torch, and 1x/2x Zoom.
 */
class CameraCaptureManager(private val context: Context) {

    companion object {
        private const val TAG = "CameraCaptureManager"
    }

    private var camera: Camera? = null
    private var videoCapture: VideoCapture<Recorder>? = null
    private var activeRecording: Recording? = null
    
    var isRecording: Boolean = false
        private set

    var currentLensFacing: Int = CameraSelector.LENS_FACING_BACK
        private set

    var isTorchEnabled: Boolean = false
        private set

    var isAeAfLocked: Boolean = false
        private set

    var currentZoomRatio: Float = 1.0f
        private set

    var currentExposureIndex: Int = 0
        private set

    /**
     * Binds CameraX Preview and VideoCapture to the given LifecycleOwner and PreviewView.
     */
    fun initializeCamera(
        lifecycleOwner: LifecycleOwner,
        previewView: PreviewView,
        lensFacing: Int = currentLensFacing,
        onReady: (() -> Unit)? = null
    ) {
        currentLensFacing = lensFacing
        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        cameraProviderFuture.addListener({
            try {
                val cameraProvider = cameraProviderFuture.get()

                val preview = Preview.Builder().build().also {
                    it.setSurfaceProvider(previewView.surfaceProvider)
                }

                // Strict 1080p FHD configuration optimized for POCO C65 hardware sensor
                val qualitySelector = QualitySelector.from(
                    Quality.FHD,
                    FallbackStrategy.lowerQualityOrHigherThan(Quality.FHD)
                )

                val recorder = Recorder.Builder()
                    .setQualitySelector(qualitySelector)
                    .build()

                videoCapture = VideoCapture.withOutput(recorder)

                val cameraSelector = CameraSelector.Builder()
                    .requireLensFacing(currentLensFacing)
                    .build()

                cameraProvider.unbindAll()
                camera = cameraProvider.bindToLifecycle(
                    lifecycleOwner,
                    cameraSelector,
                    preview,
                    videoCapture
                )

                // Restore previous state if applicable
                camera?.cameraControl?.setZoomRatio(currentZoomRatio)
                if (currentExposureIndex != 0) {
                    camera?.cameraControl?.setExposureCompensationIndex(currentExposureIndex)
                }

                Log.i(TAG, "CameraX 1080p FHD VideoCapture bound successfully (Lens: $currentLensFacing).")
                onReady?.invoke()

            } catch (exc: Exception) {
                Log.e(TAG, "Failed to bind CameraX lifecycle: ${exc.message}", exc)
            }
        }, ContextCompat.getMainExecutor(context))
    }

    /**
     * Switches between Back Camera and Front/Selfie Camera.
     */
    fun switchCamera(
        lifecycleOwner: LifecycleOwner,
        previewView: PreviewView,
        onReady: (() -> Unit)? = null
    ) {
        val nextLens = if (currentLensFacing == CameraSelector.LENS_FACING_BACK) {
            CameraSelector.LENS_FACING_FRONT
        } else {
            CameraSelector.LENS_FACING_BACK
        }
        isTorchEnabled = false
        isAeAfLocked = false
        initializeCamera(lifecycleOwner, previewView, nextLens, onReady)
    }

    /**
     * Focuses and meters on the tapped point on the PreviewView.
     */
    fun focusOnPoint(meteringPoint: MeteringPoint) {
        val cam = camera ?: return
        val action = FocusMeteringAction.Builder(meteringPoint, FocusMeteringAction.FLAG_AF or FocusMeteringAction.FLAG_AE)
            .setAutoCancelDuration(4, TimeUnit.SECONDS)
            .build()
        cam.cameraControl.startFocusAndMetering(action)
        Log.i(TAG, "Focus and metering point dispatched.")
    }

    /**
     * Locks or unlocks Auto Exposure & Auto Focus (AE/AF Lock).
     */
    fun toggleAeAfLock(): Boolean {
        val cam = camera ?: return false
        isAeAfLocked = !isAeAfLocked
        if (isAeAfLocked) {
            // Lock focus by disabling auto-cancel on a center point
            val factory = androidx.camera.core.SurfaceOrientedMeteringPointFactory(1f, 1f)
            val centerPoint = factory.createPoint(0.5f, 0.5f)
            val action = FocusMeteringAction.Builder(centerPoint, FocusMeteringAction.FLAG_AF or FocusMeteringAction.FLAG_AE)
                .disableAutoCancel()
                .build()
            cam.cameraControl.startFocusAndMetering(action)
        } else {
            cam.cameraControl.cancelFocusAndMetering()
        }
        return isAeAfLocked
    }

    /**
     * Toggles flashlight / torch (back camera only).
     */
    fun toggleTorch(): Boolean {
        val cam = camera ?: return false
        if (currentLensFacing != CameraSelector.LENS_FACING_BACK) return false
        isTorchEnabled = !isTorchEnabled
        cam.cameraControl.enableTorch(isTorchEnabled)
        return isTorchEnabled
    }

    /**
     * Sets zoom ratio (e.g. 1.0f or 2.0f).
     */
    fun setZoom(ratio: Float) {
        val cam = camera ?: return
        currentZoomRatio = ratio
        cam.cameraControl.setZoomRatio(ratio)
    }

    /**
     * Sets exposure compensation bias index.
     */
    fun setExposureIndex(index: Int) {
        val cam = camera ?: return
        currentExposureIndex = index
        cam.cameraControl.setExposureCompensationIndex(index)
    }

    fun getExposureRange(): Range<Int>? {
        return camera?.cameraInfo?.exposureState?.exposureCompensationRange
    }

    /**
     * Lists available audio input devices (detecting internal vs external microphones).
     */
    fun getAudioInputDevices(): List<AudioDeviceInfo> {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
            return audioManager.getDevices(AudioManager.GET_DEVICES_INPUTS).toList()
        }
        return emptyList()
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
                    Log.i(TAG, "Started recording 1080p FHD video segment: ${targetTmpFile.name}")
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

package com.resolvia.dispatch.recorder

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.util.Range
import android.util.Rational
import android.hardware.camera2.CaptureRequest
import android.util.Size
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
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
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import java.io.File
import java.util.concurrent.Executors
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

    private val recordingExecutor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

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

    var isAudioEnabled: Boolean = true
        private set

    var currentExposureIndex: Int = 0
        private set

    private var orientationEventListener: android.view.OrientationEventListener? = null
    var currentRotation: Int = android.view.Surface.ROTATION_0
        private set
    var currentOrientationMode: String = "portrait"
        private set

    init {
        setupOrientationListener()
    }

    private fun setupOrientationListener() {
        orientationEventListener = object : android.view.OrientationEventListener(context) {
            override fun onOrientationChanged(orientation: Int) {
                if (orientation == ORIENTATION_UNKNOWN) return
                val newRotation = when (orientation) {
                    in 45..134 -> android.view.Surface.ROTATION_270
                    in 135..224 -> android.view.Surface.ROTATION_180
                    in 225..314 -> android.view.Surface.ROTATION_90
                    else -> android.view.Surface.ROTATION_0
                }
                if (newRotation != currentRotation) {
                    currentRotation = newRotation
                    currentOrientationMode = if (newRotation == android.view.Surface.ROTATION_90 || newRotation == android.view.Surface.ROTATION_270) {
                        "landscape"
                    } else {
                        "portrait"
                    }
                    try {
                        videoCapture?.targetRotation = currentRotation
                    } catch (e: Exception) {
                        Log.w(TAG, "Failed to update targetRotation: ${e.message}")
                    }
                }
            }
        }
        if (orientationEventListener?.canDetectOrientation() == true) {
            orientationEventListener?.enable()
        }
    }

    fun toggleAudio(): Boolean {
        isAudioEnabled = !isAudioEnabled
        try {
            activeRecording?.mute(!isAudioEnabled)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to mute/unmute active recording: ${e.message}")
        }
        return isAudioEnabled
    }

    var activeLifecycleOwner: LifecycleOwner? = null
        private set

    val isInitialized: Boolean
        get() = videoCapture != null

    var activePreviewView: PreviewView? = null
        private set

    /**
     * Builds a ResolutionSelector targeting crisp 16:9 aspect ratio and 1080p resolution across all devices.
     */
    private fun createResolutionSelector(): ResolutionSelector {
        return ResolutionSelector.Builder()
            .setAspectRatioStrategy(AspectRatioStrategy.RATIO_16_9_FALLBACK_AUTO_STRATEGY)
            .setResolutionStrategy(
                ResolutionStrategy(
                    Size(1920, 1080),
                    ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER
                )
            )
            .build()
    }

    /**
     * Builds a Universal QualitySelector compatible with all Android sensors (Back & Front, Pixel, Samsung, Poco, etc.)
     * Ordered UHD -> FHD -> HD -> SD so it automatically picks maximum hardware quality without fallback crashes.
     */
    private fun createQualitySelector(): QualitySelector {
        return QualitySelector.fromOrderedList(
            listOf(Quality.UHD, Quality.FHD, Quality.HD, Quality.SD),
            FallbackStrategy.lowerQualityOrHigherThan(Quality.SD)
        )
    }

    /**
     * Applies Camera2 hardware extensions for continuous video autofocus, auto-exposure, and stabilization.
     */
    @androidx.annotation.OptIn(ExperimentalCamera2Interop::class)
    private fun applyCamera2Optimizations(previewBuilder: Preview.Builder, lensFacing: Int = currentLensFacing) {
        try {
            val extender = Camera2Interop.Extender(previewBuilder)
            extender.setCaptureRequestOption(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO)
            extender.setCaptureRequestOption(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
            extender.setCaptureRequestOption(CaptureRequest.CONTROL_AWB_MODE, CaptureRequest.CONTROL_AWB_MODE_AUTO)
            extender.setCaptureRequestOption(CaptureRequest.CONTROL_AE_ANTIBANDING_MODE, CaptureRequest.CONTROL_AE_ANTIBANDING_MODE_AUTO)

            // Sharpness & Native Camera Noise Reduction algorithms
            extender.setCaptureRequestOption(CaptureRequest.NOISE_REDUCTION_MODE, CaptureRequest.NOISE_REDUCTION_MODE_HIGH_QUALITY)
            extender.setCaptureRequestOption(CaptureRequest.EDGE_MODE, CaptureRequest.EDGE_MODE_HIGH_QUALITY)
            extender.setCaptureRequestOption(CaptureRequest.COLOR_CORRECTION_MODE, CaptureRequest.COLOR_CORRECTION_MODE_HIGH_QUALITY)
            extender.setCaptureRequestOption(CaptureRequest.TONEMAP_MODE, CaptureRequest.TONEMAP_MODE_HIGH_QUALITY)

            // Hardware autofocus & physical lens optical stabilization (no digital EIS cropping)
            if (lensFacing == CameraSelector.LENS_FACING_BACK) {
                extender.setCaptureRequestOption(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO)
                extender.setCaptureRequestOption(CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE, CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE_OFF)
                extender.setCaptureRequestOption(CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE, CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE_ON)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Camera2Interop options not supported on this device: ${e.message}")
        }
    }

    /**
     * Binds CameraX Preview and VideoCapture to the given LifecycleOwner and PreviewView.
     */
    fun initializeCamera(
        lifecycleOwner: LifecycleOwner,
        previewView: PreviewView,
        lensFacing: Int = currentLensFacing,
        onReady: (() -> Unit)? = null
    ) {
        activeLifecycleOwner = lifecycleOwner
        activePreviewView = previewView
        currentLensFacing = lensFacing
        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        cameraProviderFuture.addListener({
            try {
                val cameraProvider = cameraProviderFuture.get()

                val previewBuilder = Preview.Builder().setResolutionSelector(createResolutionSelector())
                applyCamera2Optimizations(previewBuilder, currentLensFacing)
                val preview = previewBuilder.build().also {
                    it.setSurfaceProvider(previewView.surfaceProvider)
                }

                val recorder = Recorder.Builder()
                    .setExecutor(recordingExecutor)
                    .setQualitySelector(createQualitySelector())
                    .build()

                videoCapture = VideoCapture.withOutput(recorder).apply {
                    targetRotation = currentRotation
                }

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

                Log.i(TAG, "CameraX High-Resolution 16:9 VideoCapture bound successfully (Lens: $currentLensFacing).")
                onReady?.invoke()

            } catch (exc: Exception) {
                Log.e(TAG, "Failed to bind CameraX lifecycle: ${exc.message}", exc)
            }
        }, ContextCompat.getMainExecutor(context))
    }

    /**
     * Suspending coroutine that binds VideoCapture directly to LifecycleOwner (Activity or Service)
     * and returns true upon success, false upon failure.
     */
    suspend fun bindLifecycle(
        lifecycleOwner: LifecycleOwner,
        lensFacing: Int = currentLensFacing
    ): Boolean = suspendCancellableCoroutine { continuation ->
        activeLifecycleOwner = lifecycleOwner
        currentLensFacing = lensFacing
        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        cameraProviderFuture.addListener({
            try {
                val cameraProvider = cameraProviderFuture.get()

                val recorder = Recorder.Builder()
                    .setQualitySelector(createQualitySelector())
                    .build()

                videoCapture = VideoCapture.withOutput(recorder).apply {
                    targetRotation = currentRotation
                }

                val cameraSelector = CameraSelector.Builder()
                    .requireLensFacing(currentLensFacing)
                    .build()

                val useCases = mutableListOf<androidx.camera.core.UseCase>()
                activePreviewView?.let { pv ->
                    val pb = Preview.Builder().setResolutionSelector(createResolutionSelector())
                    applyCamera2Optimizations(pb)
                    val preview = pb.build().also {
                        it.setSurfaceProvider(pv.surfaceProvider)
                    }
                    useCases.add(preview)
                }
                videoCapture?.let { useCases.add(it) }

                cameraProvider.unbindAll()
                camera = cameraProvider.bindToLifecycle(
                    lifecycleOwner,
                    cameraSelector,
                    *useCases.toTypedArray()
                )

                camera?.cameraControl?.setZoomRatio(currentZoomRatio)
                if (currentExposureIndex != 0) {
                    camera?.cameraControl?.setExposureCompensationIndex(currentExposureIndex)
                }

                Log.i(TAG, "VideoCapture bound to Lifecycle (Lens: $currentLensFacing).")
                if (continuation.isActive) continuation.resume(true)
            } catch (exc: Exception) {
                Log.e(TAG, "Failed to bind VideoCapture to Lifecycle: ${exc.message}", exc)
                videoCapture = null
                if (continuation.isActive) continuation.resume(false)
            }
        }, ContextCompat.getMainExecutor(context))
    }

    /**
     * Binds VideoCapture directly to LifecycleService so recording continues with screen off.
     * Retains live PreviewView binding if the Activity View is attached.
     */
    fun bindToService(
        serviceLifecycleOwner: LifecycleOwner,
        lensFacing: Int = currentLensFacing,
        onReady: ((Boolean) -> Unit)? = null
    ) {
        activeLifecycleOwner = serviceLifecycleOwner
        currentLensFacing = lensFacing
        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        cameraProviderFuture.addListener({
            try {
                val cameraProvider = cameraProviderFuture.get()

                val recorder = Recorder.Builder()
                    .setQualitySelector(createQualitySelector())
                    .build()

                videoCapture = VideoCapture.withOutput(recorder).apply {
                    targetRotation = currentRotation
                }

                val cameraSelector = CameraSelector.Builder()
                    .requireLensFacing(currentLensFacing)
                    .build()

                val serviceUseCases = mutableListOf<androidx.camera.core.UseCase>()
                activePreviewView?.let { pv ->
                    val pb = Preview.Builder().setResolutionSelector(createResolutionSelector())
                    applyCamera2Optimizations(pb)
                    val preview = pb.build().also {
                        it.setSurfaceProvider(pv.surfaceProvider)
                    }
                    serviceUseCases.add(preview)
                }
                videoCapture?.let { serviceUseCases.add(it) }

                cameraProvider.unbindAll()
                camera = cameraProvider.bindToLifecycle(
                    serviceLifecycleOwner,
                    cameraSelector,
                    *serviceUseCases.toTypedArray()
                )

                camera?.cameraControl?.setZoomRatio(currentZoomRatio)
                if (currentExposureIndex != 0) {
                    camera?.cameraControl?.setExposureCompensationIndex(currentExposureIndex)
                }

                Log.i(TAG, "VideoCapture bound to LifecycleService (Lens: $currentLensFacing).")
                onReady?.invoke(true)
            } catch (exc: Exception) {
                Log.e(TAG, "Failed to bind VideoCapture to LifecycleService: ${exc.message}", exc)
                videoCapture = null
                onReady?.invoke(false)
            }
        }, ContextCompat.getMainExecutor(context))
    }

    /**
     * Suspending coroutine that binds VideoCapture to LifecycleService and returns success/failure.
     */
    suspend fun bindToServiceAsync(
        serviceLifecycleOwner: LifecycleOwner,
        lensFacing: Int = currentLensFacing
    ): Boolean = suspendCancellableCoroutine { cont ->
        bindToService(serviceLifecycleOwner, lensFacing) { ok ->
            if (cont.isActive) cont.resume(ok)
        }
    }

    /**
     * Toggles lens facing between BACK and FRONT, re-binding to active LifecycleOwner.
     */
    suspend fun toggleLensFacing(): Int {
        val nextLens = if (currentLensFacing == CameraSelector.LENS_FACING_BACK) {
            CameraSelector.LENS_FACING_FRONT
        } else {
            CameraSelector.LENS_FACING_BACK
        }
        isTorchEnabled = false
        isAeAfLocked = false
        val owner = activeLifecycleOwner
        val pv = activePreviewView
        if (owner != null && pv != null) {
            initializeCamera(owner, pv, nextLens)
            currentLensFacing = nextLens
        } else if (owner != null) {
            val success = bindLifecycle(owner, nextLens)
            if (success) {
                currentLensFacing = nextLens
            }
        } else {
            currentLensFacing = nextLens
        }
        return currentLensFacing
    }

    /**
     * Switches between Back Camera and Front/Selfie Camera with PreviewView.
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

    private var lastMeteringPoint: MeteringPoint? = null

    /**
     * Focuses and meters on the tapped (x, y) coordinate on PreviewView.
     */
    fun focusOnPoint(
        x: Float,
        y: Float,
        previewView: PreviewView,
        onComplete: ((Boolean) -> Unit)? = null
    ) {
        val cam = camera ?: return
        try {
            isAeAfLocked = false
            val point = previewView.meteringPointFactory.createPoint(x, y)
            lastMeteringPoint = point
            val action = FocusMeteringAction.Builder(point, FocusMeteringAction.FLAG_AF or FocusMeteringAction.FLAG_AE)
                .setAutoCancelDuration(3, TimeUnit.SECONDS)
                .build()
            val future = cam.cameraControl.startFocusAndMetering(action)
            future.addListener({
                try {
                    val result = future.get()
                    onComplete?.invoke(result.isFocusSuccessful)
                } catch (_: Exception) {
                    onComplete?.invoke(false)
                }
            }, ContextCompat.getMainExecutor(context))
            Log.i(TAG, "Focus dispatched at ($x, $y).")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to dispatch focus: ${e.message}")
        }
    }

    /**
     * Focuses and meters on the given metering point.
     */
    fun focusOnPoint(meteringPoint: MeteringPoint) {
        val cam = camera ?: return
        isAeAfLocked = false
        lastMeteringPoint = meteringPoint
        val action = FocusMeteringAction.Builder(meteringPoint, FocusMeteringAction.FLAG_AF or FocusMeteringAction.FLAG_AE)
            .setAutoCancelDuration(3, TimeUnit.SECONDS)
            .build()
        cam.cameraControl.startFocusAndMetering(action)
        Log.i(TAG, "Focus and metering point dispatched.")
    }

    /**
     * Locks Auto Exposure & Auto Focus (AE/AF Lock) permanently at the specified (x, y) coordinate.
     */
    fun lockAeAfAtPoint(
        x: Float,
        y: Float,
        previewView: PreviewView,
        onComplete: ((Boolean) -> Unit)? = null
    ): Boolean {
        val cam = camera ?: return false
        return try {
            val point = previewView.meteringPointFactory.createPoint(x, y)
            lastMeteringPoint = point
            val action = FocusMeteringAction.Builder(point, FocusMeteringAction.FLAG_AF or FocusMeteringAction.FLAG_AE)
                .disableAutoCancel()
                .build()
            isAeAfLocked = true
            val future = cam.cameraControl.startFocusAndMetering(action)
            future.addListener({
                try {
                    val result = future.get()
                    onComplete?.invoke(result.isFocusSuccessful)
                } catch (_: Exception) {
                    onComplete?.invoke(false)
                }
            }, ContextCompat.getMainExecutor(context))
            Log.i(TAG, "AE/AF permanently locked at ($x, $y).")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to lock AE/AF: ${e.message}")
            false
        }
    }

    /**
     * Unlocks Auto Exposure & Auto Focus, returning to continuous auto-metering.
     */
    fun unlockAeAf() {
        val cam = camera ?: return
        if (isAeAfLocked) {
            isAeAfLocked = false
            try {
                cam.cameraControl.cancelFocusAndMetering()
                Log.i(TAG, "AE/AF unlocked and cancelled.")
            } catch (e: Exception) {
                Log.w(TAG, "Error cancelling focus/metering: ${e.message}")
            }
        }
    }

    /**
     * Locks or unlocks Auto Exposure & Auto Focus (AE/AF Lock).
     */
    fun toggleAeAfLock(previewView: PreviewView? = null): Boolean {
        val cam = camera ?: return false
        isAeAfLocked = !isAeAfLocked
        if (isAeAfLocked) {
            // Lock focus & exposure permanently on last tapped spot or center
            val point = lastMeteringPoint ?: if (previewView != null && previewView.width > 0 && previewView.height > 0) {
                previewView.meteringPointFactory.createPoint(previewView.width / 2f, previewView.height / 2f)
            } else {
                val factory = androidx.camera.core.SurfaceOrientedMeteringPointFactory(1f, 1f)
                factory.createPoint(0.5f, 0.5f)
            }
            val action = FocusMeteringAction.Builder(point, FocusMeteringAction.FLAG_AF or FocusMeteringAction.FLAG_AE)
                .disableAutoCancel()
                .build()
            cam.cameraControl.startFocusAndMetering(action)
            Log.i(TAG, "AE/AF permanently locked.")
        } else {
            cam.cameraControl.cancelFocusAndMetering()
            Log.i(TAG, "AE/AF unlocked.")
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
     * Toggles microphone audio recording status.
     */
    fun toggleMic(): Boolean {
        isAudioEnabled = !isAudioEnabled
        try {
            activeRecording?.mute(!isAudioEnabled)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to mute/unmute active recording: ${e.message}")
        }
        return isAudioEnabled
    }

    /**
     * Sets zoom ratio clamped to camera capabilities (e.g. 1.0f, 2.0f, 3.0f).
     */
    fun setZoom(ratio: Float): Float {
        val cam = camera ?: run {
            currentZoomRatio = ratio
            return ratio
        }
        val zoomState = cam.cameraInfo.zoomState.value
        val minZ = zoomState?.minZoomRatio ?: 1.0f
        val maxZ = zoomState?.maxZoomRatio ?: 5.0f
        val clamped = ratio.coerceIn(minZ, maxZ)
        currentZoomRatio = clamped
        cam.cameraControl.setZoomRatio(clamped)
        return currentZoomRatio
    }

    fun setZoomRatio(ratio: Float): Float = setZoom(ratio)

    data class ZoomInfo(
        val minZoomRatio: Float,
        val maxZoomRatio: Float,
        val currentZoomRatio: Float
    )

    fun getZoomInfo(): ZoomInfo {
        val cam = camera ?: return ZoomInfo(1.0f, 5.0f, currentZoomRatio)
        val zoomState = cam.cameraInfo.zoomState.value
        val minZ = zoomState?.minZoomRatio ?: 1.0f
        val maxZ = zoomState?.maxZoomRatio ?: 5.0f
        return ZoomInfo(minZ, maxZ, currentZoomRatio)
    }

    /**
     * Cycles zoom between 1.0x -> 2.0x -> 3.0x -> 1.0x.
     */
    fun toggleZoom(): Float {
        val nextZoom = when {
            currentZoomRatio < 1.5f -> 2.0f
            currentZoomRatio < 2.5f -> 3.0f
            else -> 1.0f
        }
        return setZoom(nextZoom)
    }

    data class ExposureCompensationInfo(
        val minIndex: Int,
        val maxIndex: Int,
        val currentIndex: Int,
        val stepEv: Float,
        val isSupported: Boolean
    )

    fun getExposureInfo(): ExposureCompensationInfo {
        val cam = camera ?: return ExposureCompensationInfo(-4, 4, currentExposureIndex, 0.5f, true)
        val state = cam.cameraInfo.exposureState
        val range = state.exposureCompensationRange
        val step = if (state.exposureCompensationStep.denominator > 0) {
            state.exposureCompensationStep.numerator.toFloat() / state.exposureCompensationStep.denominator.toFloat()
        } else {
            0.5f
        }
        val supported = (range.lower < range.upper) || state.isExposureCompensationSupported
        val minIdx = if (range.lower < range.upper) range.lower else -4
        val maxIdx = if (range.lower < range.upper) range.upper else 4
        return ExposureCompensationInfo(
            minIndex = minIdx,
            maxIndex = maxIdx,
            currentIndex = currentExposureIndex.coerceIn(minIdx, maxIdx),
            stepEv = step,
            isSupported = supported
        )
    }

    /**
     * Sets exposure compensation bias index cleanly clamped to hardware capabilities.
     */
    fun setExposureIndex(index: Int): Int {
        val cam = camera ?: run {
            currentExposureIndex = index
            return index
        }
        val range = cam.cameraInfo.exposureState.exposureCompensationRange
        val minIdx = if (range.lower < range.upper) range.lower else -4
        val maxIdx = if (range.lower < range.upper) range.upper else 4
        currentExposureIndex = index.coerceIn(minIdx, maxIdx)
        try {
            cam.cameraControl.setExposureCompensationIndex(currentExposureIndex)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to set exposure compensation index: ${e.message}")
        }
        return currentExposureIndex
    }

    /**
     * Resets exposure compensation back to default (0).
     */
    fun resetExposure(): Int {
        return setExposureIndex(0)
    }

    /**
     * Steps exposure bias by delta (-1 or +1).
     */
    fun stepExposure(delta: Int): Int {
        val cam = camera ?: return currentExposureIndex
        val range = cam.cameraInfo.exposureState.exposureCompensationRange
        val target = (currentExposureIndex + delta).coerceIn(range.lower, range.upper)
        setExposureIndex(target)
        return currentExposureIndex
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
        onError: ((errorCode: Int, cause: Throwable?) -> Unit)? = null,
        onFinalized: (file: File, durationMs: Long) -> Unit
    ) {
        val vc = videoCapture ?: run {
            Log.e(TAG, "VideoCapture not initialized")
            onError?.invoke(-1, IllegalStateException("VideoCapture not initialized"))
            return
        }

        try {
            vc.targetRotation = currentRotation
        } catch (e: Exception) {
            Log.w(TAG, "Failed to apply targetRotation before recording: ${e.message}")
        }
        Log.i(TAG, "Starting segment recording (Orientation: $currentOrientationMode, Rotation: $currentRotation)")

        val outputOptions = FileOutputOptions.Builder(targetTmpFile).build()
        val pendingRecording = vc.output.prepareRecording(context, outputOptions)

        if (isAudioEnabled && ActivityCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            pendingRecording.withAudioEnabled()
        }

        isRecording = true
        activeRecording = pendingRecording.start(recordingExecutor) { recordEvent ->
            when (recordEvent) {
                is VideoRecordEvent.Start -> {
                    isRecording = true
                    mainHandler.post {
                        RecordingForegroundService.instance?.updateNotification()
                    }
                    Log.i(TAG, "Started recording 1080p FHD video segment: ${targetTmpFile.name} ($currentOrientationMode)")
                }
                is VideoRecordEvent.Finalize -> {
                    isRecording = false
                    mainHandler.post {
                        RecordingForegroundService.instance?.updateNotification()
                    }
                    val durationMs = recordEvent.recordingStats.recordedDurationNanos / 1_000_000
                    if (!recordEvent.hasError()) {
                        if (durationMs < 1000L || targetTmpFile.length() == 0L) {
                            Log.w(TAG, "Discarding sub-second or empty segment (${durationMs}ms, ${targetTmpFile.length()} bytes): ${targetTmpFile.name}")
                            targetTmpFile.delete()
                            onError?.invoke(-2, IllegalStateException("Recording too short (<1s)"))
                        } else {
                            Log.i(TAG, "Segment finalized successfully: ${targetTmpFile.name} (${durationMs}ms, ${targetTmpFile.length()} bytes)")
                            onFinalized(targetTmpFile, durationMs)
                        }
                    } else {
                        Log.e(TAG, "Segment recording failed with error code: ${recordEvent.error}", recordEvent.cause)
                        targetTmpFile.delete()
                        onError?.invoke(recordEvent.error, recordEvent.cause)
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
            RecordingForegroundService.instance?.updateNotification()
        }
    }

    /**
     * Releases listeners and resources.
     */
    fun destroy() {
        orientationEventListener?.disable()
        stopActiveRecording()
    }
}

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
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CaptureRequest
import android.util.Size
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.Camera2CameraControl
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.CaptureRequestOptions
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
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import com.google.common.util.concurrent.ListenableFuture

internal fun isAeAfLockConfirmed(hasAf: Boolean, hasAe: Boolean, focusSuccessful: Boolean): Boolean =
    hasAe || (hasAf && focusSuccessful)

internal fun clampExposureIndex(index: Int, minIndex: Int, maxIndex: Int): Int =
    index.coerceIn(minIndex, maxIndex)

/**
 * CameraX Video Capture Engine for POCO C65 and Android devices.
 * Streams camera frames to PreviewView and records FHD 1080p MP4 segments with audio.
 * Supports: Front/Back camera, Tap-to-Focus, AE/AF Lock, Exposure Bias, Torch, and 1x/2x Zoom.
 */
class CameraCaptureManager(private val context: Context) {

    companion object {
        private const val TAG = "CameraCaptureManager"
        private const val LOCK_CONFIRMATION_TIMEOUT_MS = 4_500L
        private const val LOCK_RECOVERY_TIMEOUT_MS = 1_800L
    }

    private val recordingExecutor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    private var camera: Camera? = null
    private var videoCapture: VideoCapture<Recorder>? = null
    private var activeRecording: Recording? = null
    private val focusOperationGeneration = CameraRequestGeneration()
    private val exposureOperationGeneration = CameraRequestGeneration()
    private var boundLifecycleOwner: LifecycleOwner? = null
    private var boundLensFacing: Int? = null
    
    var isRecording: Boolean = false
        private set

    var currentLensFacing: Int = CameraSelector.LENS_FACING_BACK
        private set

    var isTorchEnabled: Boolean = false
        private set

    @Volatile var isAeAfLocked: Boolean = false
        private set

    private fun clearConfirmedLockState() {
        isAeAfLocked = false
    }

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

    fun setAudioEnabled(enabled: Boolean) {
        isAudioEnabled = enabled
        try {
            activeRecording?.mute(!isAudioEnabled)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to update audio mute state: ${e.message}")
        }
    }

    var activeLifecycleOwner: LifecycleOwner? = null
        private set

    val isInitialized: Boolean
        get() = camera != null && videoCapture != null && !isBindingCamera

    var activePreviewView: PreviewView? = null
        private set

    private val pendingReadyCallbacks = mutableListOf<() -> Unit>()
    private val pendingErrorCallbacks = mutableListOf<(String) -> Unit>()

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
     * Builds a QualitySelector prioritizing 1080p FHD video capture to match the 1080p preview and UI claims.
     * Ordered FHD -> HD -> SD so it automatically picks high-definition quality without framing crop mismatches.
     */
    private fun createQualitySelector(): QualitySelector {
        return QualitySelector.fromOrderedList(
            listOf(Quality.FHD, Quality.HD, Quality.SD),
            FallbackStrategy.lowerQualityOrHigherThan(Quality.SD)
        )
    }

    /**
     * Applies Camera2 hardware extensions for continuous video autofocus and auto-exposure.
     * Uses FAST / device default processing for smooth live preview without latency or color shifting.
     */
    @androidx.annotation.OptIn(ExperimentalCamera2Interop::class)
    private fun applyCamera2Optimizations(previewBuilder: Preview.Builder, lensFacing: Int = currentLensFacing) {
        try {
            val extender = Camera2Interop.Extender(previewBuilder)
            extender.setCaptureRequestOption(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO)
            extender.setCaptureRequestOption(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
            extender.setCaptureRequestOption(CaptureRequest.CONTROL_AWB_MODE, CaptureRequest.CONTROL_AWB_MODE_AUTO)
            extender.setCaptureRequestOption(CaptureRequest.CONTROL_AE_ANTIBANDING_MODE, CaptureRequest.CONTROL_AE_ANTIBANDING_MODE_AUTO)

            // Fast preview processing to maintain stable 30fps without thermal throttling or color shift
            extender.setCaptureRequestOption(CaptureRequest.NOISE_REDUCTION_MODE, CaptureRequest.NOISE_REDUCTION_MODE_FAST)
            extender.setCaptureRequestOption(CaptureRequest.EDGE_MODE, CaptureRequest.EDGE_MODE_FAST)

            // Hardware continuous autofocus for video if supported
            if (lensFacing == CameraSelector.LENS_FACING_BACK) {
                extender.setCaptureRequestOption(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Camera2Interop options not supported on this device: ${e.message}")
        }
    }

    private var isBindingCamera = false

    /**
     * Binds CameraX Preview and VideoCapture to the given LifecycleOwner and PreviewView.
     */
    fun initializeCamera(
        lifecycleOwner: LifecycleOwner,
        previewView: PreviewView,
        lensFacing: Int = currentLensFacing,
        onReady: (() -> Unit)? = null,
        onError: ((String) -> Unit)? = null
    ) {
        activeLifecycleOwner = lifecycleOwner
        activePreviewView = previewView

        if (isBindingCamera) {
            Log.i(TAG, "Camera binding already in progress, queuing callbacks.")
            currentLensFacing = lensFacing
            onReady?.let { pendingReadyCallbacks.add(it) }
            onError?.let { pendingErrorCallbacks.add(it) }
            return
        }

        if (camera != null && videoCapture != null && boundLifecycleOwner == lifecycleOwner && boundLensFacing == lensFacing) {
            currentLensFacing = lensFacing
            Log.i(TAG, "Camera already cleanly bound and active. Re-confirming ready.")
            onReady?.invoke()
            return
        }

        currentLensFacing = lensFacing
        focusOperationGeneration.invalidate()
        exposureOperationGeneration.invalidate()
        clearConfirmedLockState()

        onReady?.let { pendingReadyCallbacks.add(it) }
        onError?.let { pendingErrorCallbacks.add(it) }
        isBindingCamera = true

        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        cameraProviderFuture.addListener({
            try {
                val cameraProvider = cameraProviderFuture.get()
                val lensFacingToBind = currentLensFacing

                val previewBuilder = Preview.Builder().setResolutionSelector(createResolutionSelector())
                applyCamera2Optimizations(previewBuilder, lensFacingToBind)
                val preview = previewBuilder.build().also {
                    it.setSurfaceProvider(previewView.surfaceProvider)
                }

                val recorder = Recorder.Builder()
                    .setExecutor(recordingExecutor)
                    .setQualitySelector(createQualitySelector())
                    .build()

                val nextVideoCapture = VideoCapture.withOutput(recorder).apply {
                    targetRotation = currentRotation
                }

                val cameraSelector = CameraSelector.Builder()
                    .requireLensFacing(lensFacingToBind)
                    .build()

                cameraProvider.unbindAll()
                val boundCamera = cameraProvider.bindToLifecycle(
                    lifecycleOwner,
                    cameraSelector,
                    preview,
                    nextVideoCapture
                )
                camera = boundCamera
                videoCapture = nextVideoCapture
                boundLifecycleOwner = lifecycleOwner
                boundLensFacing = lensFacingToBind

                // Restore previous state if applicable
                boundCamera.cameraControl.setZoomRatio(currentZoomRatio)
                val exposure = boundCamera.cameraInfo.exposureState
                val range = exposure.exposureCompensationRange
                if (exposure.isExposureCompensationSupported && range.lower < range.upper) {
                    currentExposureIndex = clampExposureIndex(currentExposureIndex, range.lower, range.upper)
                    if (currentExposureIndex != exposure.exposureCompensationIndex) {
                        boundCamera.cameraControl.setExposureCompensationIndex(currentExposureIndex)
                    }
                } else {
                    currentExposureIndex = 0
                }

                isBindingCamera = false
                Log.i(TAG, "CameraX High-Resolution 1080p VideoCapture bound successfully (Lens: $lensFacingToBind).")
                val callbacks = pendingReadyCallbacks.toList()
                pendingReadyCallbacks.clear()
                pendingErrorCallbacks.clear()
                callbacks.forEach { it.invoke() }

            } catch (exc: Exception) {
                isBindingCamera = false
                camera = null
                videoCapture = null
                boundLifecycleOwner = null
                boundLensFacing = null
                val msg = exc.message ?: "Camera binding failed"
                Log.e(TAG, "Failed to bind CameraX lifecycle: $msg", exc)
                val errCallbacks = pendingErrorCallbacks.toList()
                pendingReadyCallbacks.clear()
                pendingErrorCallbacks.clear()
                errCallbacks.forEach { it.invoke(msg) }
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
    suspend fun toggleLensFacing(
        onReady: (() -> Unit)? = null,
        onError: ((String) -> Unit)? = null
    ): Int {
        val nextLens = if (currentLensFacing == CameraSelector.LENS_FACING_BACK) {
            CameraSelector.LENS_FACING_FRONT
        } else {
            CameraSelector.LENS_FACING_BACK
        }
        isTorchEnabled = false
        clearConfirmedLockState()
        focusOperationGeneration.invalidate()
        exposureOperationGeneration.invalidate()
        val owner = activeLifecycleOwner
        val pv = activePreviewView
        if (owner != null && pv != null) {
            initializeCamera(owner, pv, nextLens, onReady, onError)
        } else if (owner != null) {
            val success = try { bindLifecycle(owner, nextLens) } catch (e: Exception) {
                onError?.invoke(e.message ?: "Camera lens switch failed")
                false
            }
            if (success) {
                currentLensFacing = nextLens
                onReady?.invoke()
            } else {
                onError?.invoke("Camera lens switch failed")
            }
        } else {
            onError?.invoke("Camera preview is not available")
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
        clearConfirmedLockState()
        initializeCamera(lifecycleOwner, previewView, nextLens, onReady)
    }

    private var lastMeteringPoint: MeteringPoint? = null

    data class FocusResult(
        val success: Boolean,
        val isLocked: Boolean,
        val hasAf: Boolean,
        val hasAe: Boolean,
        val error: String? = null
    )

    /**
     * Focuses and meters on the tapped (x, y) coordinate on PreviewView.
     */
    fun focusOnPoint(
        x: Float,
        y: Float,
        previewView: PreviewView,
        onComplete: ((FocusResult) -> Unit)? = null
    ) {
        val operationGeneration = focusOperationGeneration.begin()
        clearConfirmedLockState()
        val cam = camera
        if (cam == null) {
            onComplete?.invoke(FocusResult(success = false, isLocked = false, hasAf = false, hasAe = false, error = "Camera not initialized"))
            return
        }
        try {
            isAeAfLocked = false
            val point = previewView.meteringPointFactory.createPoint(x, y)
            lastMeteringPoint = point

            val info = cam.cameraInfo
            val canAf = try {
                info.isFocusMeteringSupported(FocusMeteringAction.Builder(point, FocusMeteringAction.FLAG_AF).build())
            } catch (_: Exception) { false }
            val canAe = try {
                info.isFocusMeteringSupported(FocusMeteringAction.Builder(point, FocusMeteringAction.FLAG_AE).build())
            } catch (_: Exception) { false }

            val flags = (if (canAf) FocusMeteringAction.FLAG_AF else 0) or (if (canAe) FocusMeteringAction.FLAG_AE else 0)
            if (flags == 0) {
                onComplete?.invoke(FocusResult(success = false, isLocked = false, hasAf = false, hasAe = false, error = "Focus/metering not supported on this camera"))
                return
            }

            val action = FocusMeteringAction.Builder(point, flags)
                .setAutoCancelDuration(3, TimeUnit.SECONDS)
                .build()

            val future = cam.cameraControl.startFocusAndMetering(action)
            future.addListener({
                if (!focusOperationGeneration.isCurrent(operationGeneration)) return@addListener
                try {
                    val result = future.get()
                    onComplete?.invoke(FocusResult(success = result.isFocusSuccessful, isLocked = false, hasAf = canAf, hasAe = canAe))
                } catch (e: Exception) {
                    onComplete?.invoke(FocusResult(success = false, isLocked = false, hasAf = canAf, hasAe = canAe, error = e.message))
                }
            }, ContextCompat.getMainExecutor(context))
            Log.i(TAG, "Focus dispatched at ($x, $y) with flags=$flags.")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to dispatch focus: ${e.message}")
            onComplete?.invoke(FocusResult(success = false, isLocked = false, hasAf = false, hasAe = false, error = e.message))
        }
    }

    /**
     * Focuses and meters on the given metering point.
     */
    fun focusOnPoint(meteringPoint: MeteringPoint) {
        val cam = camera ?: return
        focusOperationGeneration.begin()
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
     * Accurately updates isAeAfLocked and reports completion ONLY after the hardware future succeeds.
     */
    fun lockAeAfAtPoint(
        x: Float,
        y: Float,
        previewView: PreviewView,
        onComplete: ((FocusResult) -> Unit)? = null
    ) {
        val operationGeneration = focusOperationGeneration.begin()
        isAeAfLocked = false
        val cam = camera
        if (cam == null) {
            isAeAfLocked = false
            onComplete?.invoke(FocusResult(success = false, isLocked = false, hasAf = false, hasAe = false, error = "Camera not initialized"))
            return
        }
        try {
            val point = previewView.meteringPointFactory.createPoint(x, y)
            lastMeteringPoint = point

            val info = cam.cameraInfo
            val canAf = try {
                info.isFocusMeteringSupported(FocusMeteringAction.Builder(point, FocusMeteringAction.FLAG_AF).build())
            } catch (_: Exception) { false }
            val canAe = try {
                info.isFocusMeteringSupported(FocusMeteringAction.Builder(point, FocusMeteringAction.FLAG_AE).build())
            } catch (_: Exception) { false }

            val flags = (if (canAf) FocusMeteringAction.FLAG_AF else 0) or (if (canAe) FocusMeteringAction.FLAG_AE else 0)
            if (flags == 0) {
                isAeAfLocked = false
                onComplete?.invoke(FocusResult(success = false, isLocked = false, hasAf = false, hasAe = false, error = "AE/AF lock not supported on this lens"))
                return
            }

            val action = FocusMeteringAction.Builder(point, flags)
                .disableAutoCancel()
                .build()

            val future = cam.cameraControl.startFocusAndMetering(action)
            future.addListener({
                if (!focusOperationGeneration.isCurrent(operationGeneration)) return@addListener
                try {
                    val result = future.get()
                    val isSuccess = isAeAfLockConfirmed(canAf, canAe, result.isFocusSuccessful)
                    isAeAfLocked = isSuccess
                    onComplete?.invoke(FocusResult(success = isSuccess, isLocked = isSuccess, hasAf = canAf, hasAe = canAe))
                    Log.i(TAG, "AE/AF lock completed: success=$isSuccess at ($x, $y).")
                } catch (e: Exception) {
                    if (!focusOperationGeneration.isCurrent(operationGeneration)) return@addListener
                    isAeAfLocked = false
                    onComplete?.invoke(FocusResult(success = false, isLocked = false, hasAf = canAf, hasAe = canAe, error = e.message))
                }
            }, ContextCompat.getMainExecutor(context))
        } catch (e: Exception) {
            if (focusOperationGeneration.isCurrent(operationGeneration)) isAeAfLocked = false
            Log.e(TAG, "Failed to lock AE/AF: ${e.message}")
            onComplete?.invoke(FocusResult(success = false, isLocked = false, hasAf = false, hasAe = false, error = e.message))
        }
    }

    /**
     * Unlocks Auto Exposure & Auto Focus, returning to continuous auto-metering.
     */
    fun unlockAeAf(onComplete: ((Boolean) -> Unit)? = null) {
        val operationGeneration = focusOperationGeneration.begin()
        val cam = camera
        if (cam != null) {
            try {
                val future = cam.cameraControl.cancelFocusAndMetering()
                future.addListener({
                    if (!focusOperationGeneration.isCurrent(operationGeneration)) return@addListener
                    try {
                        future.get()
                        isAeAfLocked = false
                        onComplete?.invoke(true)
                    } catch (e: Exception) {
                        Log.w(TAG, "Failed to confirm AE/AF unlock: ${e.message}")
                        onComplete?.invoke(false)
                    }
                }, ContextCompat.getMainExecutor(context))
                Log.i(TAG, "AE/AF unlock dispatched; waiting for CameraX confirmation.")
                return
            } catch (e: Exception) {
                Log.w(TAG, "Failed to cancel focus/metering: ${e.message}")
            }
        }
        if (cam == null) isAeAfLocked = false
        onComplete?.invoke(false)
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
        val cam = camera ?: return ExposureCompensationInfo(
            minIndex = 0,
            maxIndex = 0,
            currentIndex = 0,
            stepEv = 0f,
            isSupported = false
        )
        val state = cam.cameraInfo.exposureState
        val isSupported = state.isExposureCompensationSupported
        val range = state.exposureCompensationRange
        val step = if (state.exposureCompensationStep.denominator > 0) {
            state.exposureCompensationStep.numerator.toFloat() / state.exposureCompensationStep.denominator.toFloat()
        } else {
            0f
        }
        val supported = isSupported && (range.lower < range.upper)
        return ExposureCompensationInfo(
            minIndex = range.lower,
            maxIndex = range.upper,
            currentIndex = state.exposureCompensationIndex,
            stepEv = step,
            isSupported = supported
        )
    }

    /**
     * Sets exposure compensation bias index cleanly clamped to hardware capabilities.
     * Reports actual result through asynchronous callback.
     */
    fun setExposureIndex(index: Int, onComplete: ((Int?, String?) -> Unit)? = null): Int {
        val cam = camera ?: run {
            onComplete?.invoke(null, "Camera not initialized")
            return currentExposureIndex
        }
        val state = cam.cameraInfo.exposureState
        if (!state.isExposureCompensationSupported) {
            onComplete?.invoke(null, "Exposure compensation not supported")
            return currentExposureIndex
        }
        val range = state.exposureCompensationRange
        val targetIndex = index.coerceIn(range.lower, range.upper)
        try {
            val future = cam.cameraControl.setExposureCompensationIndex(targetIndex)
            future.addListener({
                try {
                    val applied = future.get()
                    currentExposureIndex = applied
                    onComplete?.invoke(applied, null)
                    Log.i(TAG, "Exposure compensation applied: index=$applied")
                } catch (e: Exception) {
                    Log.w(TAG, "Exposure compensation future failed: ${e.message}")
                    onComplete?.invoke(null, e.message)
                }
            }, ContextCompat.getMainExecutor(context))
            return targetIndex
        } catch (e: Exception) {
            Log.w(TAG, "Failed to dispatch exposure compensation: ${e.message}")
            onComplete?.invoke(null, e.message)
            return currentExposureIndex
        }
    }

    /**
     * Resets exposure compensation back to default (0).
     */
    fun resetExposure(onComplete: ((Int?, String?) -> Unit)? = null): Int {
        return setExposureIndex(0, onComplete)
    }
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
                    when (RecordingSafetyPolicy.finalizeDisposition(
                        hasCameraError = recordEvent.hasError(),
                        fileLength = targetTmpFile.length(),
                        durationMs = durationMs
                    )) {
                        RecordingFinalizeDisposition.COMMIT -> {
                            Log.i(TAG, "Segment finalized successfully: ${targetTmpFile.name} (${durationMs}ms, ${targetTmpFile.length()} bytes)")
                            onFinalized(targetTmpFile, durationMs)
                        }
                        RecordingFinalizeDisposition.DELETE_EMPTY_AND_REPORT_ERROR -> {
                            Log.w(TAG, "Removing empty finalized segment: ${targetTmpFile.name}")
                            targetTmpFile.delete()
                            onError?.invoke(-2, IllegalStateException("Recording produced an empty segment"))
                        }
                        RecordingFinalizeDisposition.PRESERVE_AND_REPORT_ERROR -> {
                            val errorCode = if (recordEvent.hasError()) recordEvent.error else -2
                            val cause = recordEvent.cause ?: IllegalStateException("Recording segment was too short to finalize safely")
                            Log.e(TAG, "Preserving failed segment ${targetTmpFile.name} (${targetTmpFile.length()} bytes, ${durationMs}ms), error code: $errorCode", cause)
                            onError?.invoke(errorCode, cause)
                        }
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

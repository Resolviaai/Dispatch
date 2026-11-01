package com.resolvia.dispatch.renderer

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.RenderJobEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Foreground Service for Hardware-Accelerated Video Rendering on Android.
 * Runs Media3 Transformer in a foreground execution context with WakeLock so
 * rendering continues smoothly and does not get killed by Android even when
 * the user turns off the screen or switches apps.
 */
class RenderForegroundService : LifecycleService() {

    private var wakeLock: PowerManager.WakeLock? = null
    private var activeRenderJob: Job? = null
    private var lastProgressNotificationMs: Long = 0L

    companion object {
        const val CHANNEL_ID = "dispatch_render_channel"
        const val NOTIFICATION_ID = 2002
        private const val WAKE_LOCK_DURATION_MS = 30 * 60 * 1000L // 30 minutes max

        const val ACTION_START_RENDER = "com.resolvia.dispatch.action.START_RENDER"
        const val ACTION_CANCEL_RENDER = "com.resolvia.dispatch.action.CANCEL_RENDER"

        const val EXTRA_JOB_ID = "extra_job_id"
        const val EXTRA_CLIP_ID = "extra_clip_id"

        @Volatile
        var activeJobId: String? = null
            private set

        fun start(context: Context, jobId: String, clipId: String) {
            val intent = Intent(context, RenderForegroundService::class.java).apply {
                action = ACTION_START_RENDER
                putExtra(EXTRA_JOB_ID, jobId)
                putExtra(EXTRA_CLIP_ID, clipId)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun cancel(context: Context, jobId: String) {
            val intent = Intent(context, RenderForegroundService::class.java).apply {
                action = ACTION_CANCEL_RENDER
                putExtra(EXTRA_JOB_ID, jobId)
            }
            context.startService(intent)
        }
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        acquireWakeLock()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        val action = intent?.action ?: return START_NOT_STICKY

        when (action) {
            ACTION_CANCEL_RENDER -> {
                val jobId = intent.getStringExtra(EXTRA_JOB_ID)
                if (jobId == activeJobId) {
                    activeRenderJob?.cancel()
                    stopForegroundService()
                }
            }
            ACTION_START_RENDER -> {
                val jobId = intent.getStringExtra(EXTRA_JOB_ID)
                val clipId = intent.getStringExtra(EXTRA_CLIP_ID) ?: ""
                if (!jobId.isNullOrEmpty()) {
                    startForegroundWithNotification("Preparing video render...", 0)
                    startRenderPipeline(jobId, clipId)
                }
            }
        }
        return START_NOT_STICKY
    }

    private fun startRenderPipeline(jobId: String, clipId: String) {
        activeJobId = jobId
        activeRenderJob?.cancel()

        activeRenderJob = lifecycleScope.launch(Dispatchers.IO) {
            val database = AppDatabase.getDatabase(applicationContext)
            val dao = database.recordingDao()

            val job = dao.getRenderJobById(jobId)
            if (job == null) {
                android.util.Log.e("RenderForeground", "Render job $jobId not found in Room DB")
                stopForegroundService()
                return@launch
            }

            try {
                dao.updateRenderJobProgress(
                    jobId = jobId,
                    status = RenderJobEntity.STATUS_RENDERING,
                    progress = 0,
                    updatedAt = System.currentTimeMillis()
                )

                val renderer = AndroidClipRenderer(applicationContext, lifecycleScope)
                val outcome = renderer.renderClip(
                    job = job,
                    onProgress = { progressPercent: Int ->
                        val now = System.currentTimeMillis()
                        if (now - lastProgressNotificationMs > 250 || progressPercent == 100) {
                            lastProgressNotificationMs = now
                            updateNotification("Rendering video: $progressPercent%", progressPercent)
                            lifecycleScope.launch(Dispatchers.IO) {
                                dao.updateRenderJobProgress(
                                    jobId = jobId,
                                    status = RenderJobEntity.STATUS_RENDERING,
                                    progress = progressPercent,
                                    updatedAt = now
                                )
                            }
                        }
                    }
                )

                if (outcome.success && outcome.outputPath != null) {
                    val outputFile = File(outcome.outputPath)
                    val duration = outcome.durationSeconds

                    dao.markRenderJobRenderComplete(
                        jobId = jobId,
                        status = RenderJobEntity.STATUS_RENDER_COMPLETE,
                        outputPath = outcome.outputPath,
                        thumbnailPath = outcome.thumbnailPath,
                        duration = duration,
                        updatedAt = System.currentTimeMillis()
                    )

                    updateNotification("Render complete (${String.format("%.1f", duration)}s). Ready to publish!", 100)
                    enqueueR2UploadIfReady(jobId, outcome.outputPath)
                } else {
                    val errorMsg = outcome.errorMessage ?: "Unknown render error"
                    dao.updateRenderJobError(
                        jobId = jobId,
                        status = RenderJobEntity.STATUS_FAILED,
                        lastError = errorMsg,
                        updatedAt = System.currentTimeMillis()
                    )
                    updateNotification("Render failed: $errorMsg", 0)
                }
            } catch (e: Exception) {
                android.util.Log.e("RenderForeground", "Render job failed: ${e.message}", e)
                dao.updateRenderJobError(
                    jobId = jobId,
                    status = RenderJobEntity.STATUS_FAILED,
                    lastError = e.message ?: "Render failure",
                    updatedAt = System.currentTimeMillis()
                )
                updateNotification("Render error: ${e.message}", 0)
            } finally {
                withContext(Dispatchers.Main) {
                    stopForegroundService()
                }
            }
        }
    }

    private fun enqueueR2UploadIfReady(jobId: String, outputPath: String) {
        android.util.Log.i("RenderForeground", "Render job $jobId completed: $outputPath")
    }

    private fun startForegroundWithNotification(contentText: String, progress: Int) {
        val notification = buildNotification(contentText, progress)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROCESSING
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun updateNotification(contentText: String, progress: Int) {
        val notification = buildNotification(contentText, progress)
        val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        notificationManager.notify(NOTIFICATION_ID, notification)
    }

    private fun buildNotification(contentText: String, progress: Int): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Dispatch Video Processing")
            .setContentText(contentText)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setOngoing(progress in 1..99)
            .setProgress(100, progress, progress == 0)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_PROGRESS)
            .build()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val name = "Dispatch Video Rendering"
            val descriptionText = "Notifications for background video rendering and social export"
            val importance = NotificationManager.IMPORTANCE_LOW
            val channel = NotificationChannel(CHANNEL_ID, name, importance).apply {
                description = descriptionText
                enableVibration(false)
            }
            val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            notificationManager.createNotificationChannel(channel)
        }
    }

    private fun acquireWakeLock() {
        try {
            val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Dispatch:RenderWakeLock").apply {
                setReferenceCounted(false)
                acquire(WAKE_LOCK_DURATION_MS)
            }
        } catch (e: Exception) {
            android.util.Log.w("RenderForeground", "Could not acquire wake lock: ${e.message}")
        }
    }

    private fun stopForegroundService() {
        activeJobId = null
        try {
            wakeLock?.let {
                if (it.isHeld) it.release()
            }
            wakeLock = null
        } catch (_: Exception) {}
        stopForeground(false)
        stopSelf()
    }

    override fun onDestroy() {
        stopForegroundService()
        super.onDestroy()
    }
}

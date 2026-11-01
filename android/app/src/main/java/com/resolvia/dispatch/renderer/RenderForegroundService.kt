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
import com.google.gson.Gson
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.data.RenderJobEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okio.BufferedSink
import okio.source
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.TimeUnit

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

            // Genuinely resumable: If video was already rendered, resume R2 upload directly without re-rendering
            val existingOutput = File(job.outputVideoPath)
            if (existingOutput.exists() && existingOutput.length() > 0L &&
                (job.status == RenderJobEntity.STATUS_RENDER_COMPLETE || job.status == RenderJobEntity.STATUS_UPLOADING)) {
                android.util.Log.i("RenderForeground", "Job $jobId output already rendered (${existingOutput.length()} bytes). Resuming cloud upload directly.")
                updateNotification("Resuming cloud upload...", 0)
                try {
                    enqueueR2UploadIfReady(jobId, job.outputVideoPath)
                } finally {
                    withContext(Dispatchers.Main) {
                        stopForegroundService()
                    }
                }
                return@launch
            }

            try {
                // 1. Verify source video exists; if missing locally, stage from Cloud Worker
                var currentJob = job
                val sourceFile = File(currentJob.sourceVideoPath)
                if (!sourceFile.exists() || sourceFile.length() == 0L) {
                    updateNotification("Staging source video from cloud...", 0)
                    val pairing = PairingManager(applicationContext)
                    val token = pairing.cloudAccessToken.ifBlank { pairing.pcAuthToken }
                    val staged = stageSourceVideo(currentJob.clipId, token)
                    if (staged != null && staged.exists() && staged.length() > 0L) {
                        currentJob = currentJob.copy(sourceVideoPath = staged.absolutePath)
                        dao.updateRenderJob(currentJob)
                    } else {
                        throw IllegalStateException("Source video file not found locally or in cloud for clip ${currentJob.clipId}")
                    }
                }

                dao.updateRenderJobProgress(
                    jobId = jobId,
                    status = RenderJobEntity.STATUS_RENDERING,
                    progress = 0,
                    updatedAt = System.currentTimeMillis()
                )

                val renderer = AndroidClipRenderer(applicationContext, lifecycleScope)
                val outcome = renderer.renderClip(
                    job = currentJob,
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

    private suspend fun enqueueR2UploadIfReady(jobId: String, outputPath: String) {
        val database = AppDatabase.getDatabase(applicationContext)
        val dao = database.recordingDao()
        val job = dao.getRenderJobById(jobId) ?: return
        val outputFile = File(outputPath)
        if (!outputFile.exists() || outputFile.length() == 0L) {
            android.util.Log.e("RenderForeground", "Rendered output file missing or empty: $outputPath")
            return
        }

        val pairing = PairingManager(applicationContext)
        val token = pairing.cloudAccessToken.ifBlank { pairing.pcAuthToken }

        val workerUrl = "https://dispatch-cloud.resolviaai.workers.dev"
        val client = OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(300, TimeUnit.SECONDS)
            .readTimeout(300, TimeUnit.SECONDS)
            .build()
        val gson = Gson()

        try {
            dao.updateRenderJobProgress(
                jobId = jobId,
                status = RenderJobEntity.STATUS_UPLOADING,
                progress = 0,
                updatedAt = System.currentTimeMillis()
            )
            updateNotification("Reserving cloud upload...", 0)

            // Step 1: POST /reserve (deterministic token per job for safe resume; kind clip_video)
            val clientToken = "render_${job.jobId}"
            val reserveBody = JsonObject().apply {
                addProperty("kind", "clip_video")
                addProperty("mime_type", "video/mp4")
                addProperty("expected_bytes", outputFile.length())
                addProperty("client_token", clientToken)
                addProperty("clip_id", job.clipId)
            }

            val reserveReq = Request.Builder()
                .url("$workerUrl/reserve")
                .header("Authorization", "Bearer $token")
                .post(reserveBody.toString().toRequestBody("application/json; charset=UTF-8".toMediaType()))
                .build()

            val (reservationId, uploadUrl) = client.newCall(reserveReq).execute().use { resp ->
                val bodyStr = resp.body?.string() ?: ""
                if (!resp.isSuccessful) {
                    throw IllegalStateException("Cloud reservation failed (HTTP ${resp.code}): $bodyStr")
                }
                val json = gson.fromJson(bodyStr, JsonObject::class.java)
                val resId = json.get("reservation_id")?.asString
                    ?: throw IllegalStateException("Missing reservation_id in /reserve response")
                val upUrl = json.get("upload_url")?.asString
                    ?: throw IllegalStateException("Missing upload_url in /reserve response")
                Pair(resId, upUrl)
            }

            // Step 2: Stream PUT binary video to upload_url
            val totalBytes = outputFile.length()
            val uploadBody = object : RequestBody() {
                override fun contentType() = "video/mp4".toMediaType()
                override fun contentLength() = totalBytes
                override fun writeTo(sink: BufferedSink) {
                    outputFile.source().use { source ->
                        val buffer = okio.Buffer()
                        var uploaded = 0L
                        var lastReportMs = System.currentTimeMillis()
                        var read: Long
                        while (source.read(buffer, 32768L).also { read = it } != -1L) {
                            sink.write(buffer, read)
                            uploaded += read
                            val now = System.currentTimeMillis()
                            if (now - lastReportMs > 300 || uploaded == totalBytes) {
                                lastReportMs = now
                                val pct = ((uploaded.toDouble() / totalBytes) * 100).toInt().coerceIn(0, 99)
                                updateNotification("Uploading to cloud: $pct%", pct)
                                lifecycleScope.launch(Dispatchers.IO) {
                                    dao.updateRenderJobUploadOffset(jobId, uploaded, now)
                                }
                            }
                        }
                    }
                }
            }

            val putReq = Request.Builder()
                .url(uploadUrl)
                .put(uploadBody)
                .build()

            client.newCall(putReq).execute().use { resp ->
                if (!resp.isSuccessful) {
                    throw IllegalStateException("Binary video upload to R2 failed: HTTP ${resp.code}")
                }
            }

            // Step 3: POST /complete
            val completeBody = JsonObject().apply {
                addProperty("reservation_id", reservationId)
            }
            val completeReq = Request.Builder()
                .url("$workerUrl/complete")
                .header("Authorization", "Bearer $token")
                .post(completeBody.toString().toRequestBody("application/json; charset=UTF-8".toMediaType()))
                .build()

            val finalKey = client.newCall(completeReq).execute().use { resp ->
                val bodyStr = resp.body?.string() ?: ""
                if (!resp.isSuccessful) {
                    throw IllegalStateException("Cloud complete failed (HTTP ${resp.code}): $bodyStr")
                }
                val json = gson.fromJson(bodyStr, JsonObject::class.java)
                json.get("final_key")?.asString ?: "final/${job.clipId}"
            }

            dao.updateRenderJobProgress(
                jobId = jobId,
                status = RenderJobEntity.STATUS_UPLOAD_COMPLETE,
                progress = 100,
                updatedAt = System.currentTimeMillis()
            )
            updateNotification("Upload complete. Enqueueing social publishing...", 100)

            // Step 4: POST /publish/queue (preserves user-selected title, description, platforms, and visibility mode)
            val platformsList = job.platforms.split(",").map { it.trim() }.filter { it.isNotEmpty() }.ifEmpty { listOf("youtube") }
            val platformsArr = JsonArray().apply { platformsList.forEach { add(it) } }
            val publishBody = JsonObject().apply {
                addProperty("clip_id", job.clipId)
                addProperty("title", job.title.ifBlank { "Dispatch Clip " + job.clipId.take(8) })
                addProperty("description", job.description)
                add("platforms", platformsArr)
                addProperty("publish_mode", job.publishMode.ifBlank { "private" })
                addProperty("r2_key", finalKey)
                addProperty("rendered_fingerprint", job.renderFingerprint)
            }

            val pubReq = Request.Builder()
                .url("$workerUrl/publish/queue")
                .header("Authorization", "Bearer $token")
                .post(publishBody.toString().toRequestBody("application/json; charset=UTF-8".toMediaType()))
                .build()

            client.newCall(pubReq).execute().use { resp ->
                if (!resp.isSuccessful) {
                    android.util.Log.w("RenderForeground", "Social publish queueing non-fatal error: HTTP ${resp.code}")
                }
            }

            dao.markRenderJobPublishingQueued(
                jobId = jobId,
                status = RenderJobEntity.STATUS_PUBLISHING_QUEUED,
                r2Key = finalKey,
                completedAt = System.currentTimeMillis(),
                updatedAt = System.currentTimeMillis()
            )
            updateNotification("Published! Video rendered and queued for cloud publishing.", 100)
        } catch (e: Exception) {
            android.util.Log.e("RenderForeground", "R2 upload or publishing failed: ${e.message}", e)
            dao.updateRenderJobError(
                jobId = jobId,
                status = RenderJobEntity.STATUS_FAILED,
                lastError = "Upload error: ${e.message}",
                updatedAt = System.currentTimeMillis()
            )
            updateNotification("Upload error: ${e.message}", 0)
        }
    }

    private fun stageSourceVideo(clipId: String, token: String): File? {
        val workerUrl = "https://dispatch-cloud.resolviaai.workers.dev"
        val client = OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(180, TimeUnit.SECONDS)
            .build()
        val gson = Gson()

        val req = Request.Builder()
            .url("$workerUrl/clips/${clipId}/source-url")
            .header("Authorization", "Bearer $token")
            .get()
            .build()

        return try {
            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return null
                val bodyStr = resp.body?.string() ?: return null
                val json = gson.fromJson(bodyStr, JsonObject::class.java)
                val sourceUrl = json.get("source_url")?.asString ?: return null

                val cacheVideosDir = File(applicationContext.cacheDir, "source_videos").apply { mkdirs() }
                val targetFile = File(cacheVideosDir, "${clipId}.mp4")

                val dlReq = Request.Builder().url(sourceUrl).get().build()
                client.newCall(dlReq).execute().use { dlResp ->
                    if (!dlResp.isSuccessful) return null
                    val body = dlResp.body ?: return null
                    FileOutputStream(targetFile).use { fos ->
                        body.byteStream().copyTo(fos)
                    }
                }
                if (targetFile.exists() && targetFile.length() > 0L) targetFile else null
            }
        } catch (e: Exception) {
            android.util.Log.e("RenderForeground", "Failed to stage source video for clip $clipId: ${e.message}")
            null
        }
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

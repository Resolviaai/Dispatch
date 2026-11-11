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
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.isActive
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
import java.util.Locale
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * Foreground Service for Hardware-Accelerated Video Rendering on Android.
 * Runs Media3 Transformer sequentially across queued jobs in a foreground execution
 * context with WakeLock so rendering continues smoothly and does not get killed
 * by Android even when the user turns off the screen or switches apps.
 */
class RenderForegroundService : LifecycleService() {

    private var wakeLock: PowerManager.WakeLock? = null
    private var drainJob: Job? = null
    @Volatile
    private var activeChildJob: Job? = null
    private var isForegroundStarted: Boolean = false
    private var lastProgressNotificationMs: Long = 0L
    private val jobQueue = RenderJobQueue()

    data class PublishQueueOutcome(
        val queued: Boolean,
        val statusToPersist: String,
        val notificationText: String,
        val errorMessage: String? = null
    )

    data class SourceReclaimResult(
        val deletedFiles: List<File>,
        val preservedFiles: List<File>
    )

    /**
     * Thread-safe FIFO queue and drain lifecycle coordinator of pending (jobId, clipId)
     * render/upload work. Prevents starting job N+1 from cancelling job N, and prevents
     * shutdown races where a job enqueued during cleanup gets stranded by stopSelf().
     */
    class RenderJobQueue {
        private var currentJobId: String? = null
        private var draining: Boolean = false
        private val queue = ArrayDeque<Pair<String, String>>()

        @Synchronized
        fun enqueue(jobId: String, clipId: String): Boolean {
            if (jobId.isBlank()) return false
            if (jobId == currentJobId || queue.any { it.first == jobId }) {
                return false
            }
            queue.addLast(jobId to clipId)
            return true
        }

        /**
         * Atomically enqueues [items] (deduplicating against the active job and existing queue)
         * and returns true iff the caller must launch the drain loop (draining: false -> true).
         */
        @Synchronized
        fun enqueueAndShouldStartDrain(items: List<Pair<String, String>>): Boolean {
            for ((jobId, clipId) in items) {
                enqueue(jobId, clipId)
            }
            if (!draining && queue.isNotEmpty()) {
                draining = true
                return true
            }
            return false
        }

        @Synchronized
        fun pollNext(): Pair<String, String>? {
            val next = queue.removeFirstOrNull()
            currentJobId = next?.first
            return next
        }

        @Synchronized
        fun removeQueued(jobId: String): Boolean = queue.removeAll { it.first == jobId }

        @Synchronized
        fun activeOrNull(): String? = currentJobId

        @Synchronized
        fun isDraining(): Boolean = draining

        @Synchronized
        fun clearActive(jobId: String? = null) {
            if (jobId == null || currentJobId == jobId) {
                currentJobId = null
            }
        }

        /**
         * Atomically transitions draining -> false ONLY when the queue is still empty.
         * If a new job arrived during post-job cleanup or before Main shutdown, keeps
         * draining == true and returns false so the drain loop processes it instead of stopping.
         */
        @Synchronized
        fun finishDrainIfEmpty(): Boolean {
            if (queue.isNotEmpty()) {
                return false
            }
            draining = false
            currentJobId = null
            return true
        }

        @Synchronized
        fun reset() {
            queue.clear()
            currentJobId = null
            draining = false
        }

        @Synchronized
        fun snapshotQueued(): List<Pair<String, String>> = queue.toList()
    }

    companion object {
        const val CHANNEL_ID = "dispatch_render_channel"
        const val NOTIFICATION_ID = 2002
        private const val WAKE_LOCK_DURATION_MS = 30 * 60 * 1000L // 30 minutes max
        const val RETRYABLE_FAILED_TTL_MS = 72L * 60L * 60L * 1000L
        const val ORPHAN_SOURCE_MAX_AGE_MS = 24L * 60L * 60L * 1000L

        const val ACTION_START_RENDER = "com.resolvia.dispatch.action.START_RENDER"
        const val ACTION_CANCEL_RENDER = "com.resolvia.dispatch.action.CANCEL_RENDER"

        const val EXTRA_JOB_ID = "extra_job_id"
        const val EXTRA_CLIP_ID = "extra_clip_id"
        const val EXTRA_JOB_QUEUE = "extra_job_queue"

        private val ELIGIBLE_RESUME_STATUSES = setOf(
            RenderJobEntity.STATUS_QUEUED,
            RenderJobEntity.STATUS_RENDERING,
            RenderJobEntity.STATUS_RENDER_COMPLETE,
            RenderJobEntity.STATUS_UPLOADING,
            RenderJobEntity.STATUS_UPLOAD_COMPLETE
        )

        @Volatile
        var activeJobId: String? = null
            private set

        /**
         * Validates that a cloud access token is a real 3-segment Worker JWT.
         * Never falls back to pcAuthToken (LAN shared secret).
         */
        fun resolveWorkerJwt(cloudAccessToken: String?): String? {
            val trimmed = cloudAccessToken?.trim().orEmpty()
            if (trimmed.isEmpty()) return null
            val dotCount = trimmed.count { it == '.' }
            return if (dotCount == 2 && trimmed.length > 40 && !trimmed.any { it.isWhitespace() }) {
                trimmed
            } else {
                null
            }
        }

        /**
         * Mints a fresh client_token per reservation attempt so retries after
         * expired or failed reservations do not hit 409 token-reuse conflicts.
         */
        fun mintReservationClientToken(
            jobId: String,
            attemptId: String = UUID.randomUUID().toString().replace("-", "").take(12)
        ): String {
            val safeJob = jobId.trim().ifEmpty { "job" }
            val safeAttempt = attemptId.trim().ifEmpty { "0" }
            return "render_${safeJob}_${safeAttempt}"
        }

        /**
         * Evaluates the /publish/queue HTTP response strictly. Requires HTTP 2xx
         * and non-error JSON before transitioning to STATUS_PUBLISHING_QUEUED,
         * and never lies that the clip is already "Published!" when only queued.
         */
        fun evaluatePublishQueueResponse(httpStatusCode: Int, responseBody: String?): PublishQueueOutcome {
            val body = responseBody?.trim().orEmpty()
            var parsedError: String? = null
            var explicitFalse = false

            if (body.isNotEmpty()) {
                try {
                    val json = Gson().fromJson(body, JsonObject::class.java)
                    if (json != null) {
                        if (json.has("ok") && json.get("ok").isJsonPrimitive && !json.get("ok").asBoolean) {
                            explicitFalse = true
                        }
                        if (json.has("success") && json.get("success").isJsonPrimitive && !json.get("success").asBoolean) {
                            explicitFalse = true
                        }
                        parsedError = json.get("error")?.takeIf { it.isJsonPrimitive }?.asString
                            ?: json.get("detail")?.takeIf { it.isJsonPrimitive }?.asString
                            ?: json.get("message")?.takeIf { it.isJsonPrimitive }?.asString
                    }
                } catch (_: Exception) {
                    // Non-JSON body; HTTP status governs
                }
            }

            if (httpStatusCode in 200..299 && !explicitFalse) {
                return PublishQueueOutcome(
                    queued = true,
                    statusToPersist = RenderJobEntity.STATUS_PUBLISHING_QUEUED,
                    notificationText = "Queued for cloud publishing. Video will finish posting shortly."
                )
            }

            val reason = parsedError?.takeIf { it.isNotBlank() }
                ?: if (httpStatusCode in 200..299) {
                    "Cloud publish queue rejected request"
                } else {
                    "Cloud publish enqueue failed (HTTP $httpStatusCode)"
                }

            return PublishQueueOutcome(
                queued = false,
                statusToPersist = RenderJobEntity.STATUS_FAILED,
                notificationText = "Publish queue error: $reason",
                errorMessage = reason
            )
        }

        /**
         * Selects all eligible non-terminal render/upload jobs for startup resume,
         * deduplicated by jobId in FIFO creation order.
         */
        fun selectEligiblePendingRenderJobs(jobs: List<RenderJobEntity>): List<RenderJobEntity> {
            val seen = LinkedHashSet<String>()
            return jobs
                .filter { it.status in ELIGIBLE_RESUME_STATUSES }
                .sortedBy { it.createdAt }
                .filter { seen.add(it.jobId) }
        }

        fun isJobProtectingSource(job: RenderJobEntity, nowMs: Long = System.currentTimeMillis()): Boolean {
            if (job.status in ELIGIBLE_RESUME_STATUSES) {
                return true
            }
            if (job.status == RenderJobEntity.STATUS_FAILED) {
                val ageMs = (nowMs - job.updatedAt).coerceAtLeast(0L)
                return ageMs < RETRYABLE_FAILED_TTL_MS
            }
            return false
        }

        /**
         * Reclaims unneeded files inside cacheDir/source_videos while preserving every
         * source referenced by active or retryable render jobs.
         */
        fun reclaimUnusedSourceVideos(
            sourceVideosDir: File,
            jobs: List<RenderJobEntity>,
            completedClipId: String? = null,
            nowMs: Long = System.currentTimeMillis(),
            orphanMaxAgeMs: Long = ORPHAN_SOURCE_MAX_AGE_MS
        ): SourceReclaimResult {
            if (!sourceVideosDir.exists() || !sourceVideosDir.isDirectory) {
                return SourceReclaimResult(emptyList(), emptyList())
            }
            val dirCanonical = sourceVideosDir.canonicalFile
            val protectedClipIds = HashSet<String>()
            val protectedPaths = HashSet<String>()

            for (job in jobs) {
                if (!isJobProtectingSource(job, nowMs)) continue
                if (job.clipId.isNotBlank()) {
                    protectedClipIds.add(job.clipId)
                }
                if (job.sourceVideoPath.isNotBlank()) {
                    try {
                        protectedPaths.add(File(job.sourceVideoPath).canonicalPath)
                    } catch (_: Exception) {
                    }
                }
            }

            val terminalClipIds = jobs
                .filter {
                    it.status == RenderJobEntity.STATUS_PUBLISHING_QUEUED ||
                        it.status == RenderJobEntity.STATUS_CANCELLED
                }
                .map { it.clipId }
                .toSet()

            val deleted = mutableListOf<File>()
            val preserved = mutableListOf<File>()

            val files = sourceVideosDir.listFiles() ?: emptyArray()
            for (file in files) {
                if (!file.isFile) continue
                val canonical = try {
                    file.canonicalFile
                } catch (_: Exception) {
                    preserved.add(file)
                    continue
                }
                if (canonical.parentFile != dirCanonical) {
                    preserved.add(file)
                    continue
                }

                val fileName = file.name
                val clipIdFromName = when {
                    fileName.endsWith(".mp4.tmp") -> fileName.removeSuffix(".mp4.tmp")
                    fileName.endsWith(".mp4") -> fileName.removeSuffix(".mp4")
                    else -> null
                }

                val isProtectedByJob = (clipIdFromName != null && clipIdFromName in protectedClipIds) ||
                    (canonical.path in protectedPaths)
                if (isProtectedByJob) {
                    preserved.add(file)
                    continue
                }

                val isCompletedTarget = !completedClipId.isNullOrBlank() &&
                    clipIdFromName == completedClipId &&
                    !fileName.endsWith(".tmp")
                val isTerminalKnownClip = clipIdFromName != null &&
                    clipIdFromName in terminalClipIds &&
                    !fileName.endsWith(".tmp")
                val ageMs = (nowMs - file.lastModified()).coerceAtLeast(0L)
                val isExpiredOrphan = ageMs >= orphanMaxAgeMs

                if (isCompletedTarget || isTerminalKnownClip || isExpiredOrphan) {
                    if (file.delete()) {
                        deleted.add(file)
                    } else {
                        preserved.add(file)
                    }
                } else {
                    preserved.add(file)
                }
            }

            return SourceReclaimResult(deletedFiles = deleted, preservedFiles = preserved)
        }

        suspend fun loadEligiblePendingRenderJobs(database: AppDatabase): List<RenderJobEntity> {
            return try {
                val loaded = mutableListOf<RenderJobEntity>()
                database.openHelper.readableDatabase.query(
                    "SELECT jobId, clipId, renderFingerprint, projectDocumentJson, sourceVideoPath, outputVideoPath, " +
                        "thumbnailPath, layoutMode, cropAnchorX, targetResolution, title, description, platforms, " +
                        "publishMode, status, progressPercent, renderedDurationSeconds, r2Key, r2UploadOffset, " +
                        "r2TotalBytes, lastError, retryCount, createdAt, updatedAt, completedAt " +
                        "FROM render_jobs WHERE status IN ('QUEUED', 'RENDERING', 'RENDER_COMPLETE', 'UPLOADING', 'UPLOAD_COMPLETE') " +
                        "ORDER BY createdAt ASC"
                ).use { cursor ->
                    while (cursor.moveToNext()) {
                        loaded.add(
                            RenderJobEntity(
                                jobId = cursor.getString(0),
                                clipId = cursor.getString(1),
                                renderFingerprint = cursor.getString(2),
                                projectDocumentJson = cursor.getString(3),
                                sourceVideoPath = cursor.getString(4),
                                outputVideoPath = cursor.getString(5),
                                thumbnailPath = if (cursor.isNull(6)) null else cursor.getString(6),
                                layoutMode = cursor.getString(7),
                                cropAnchorX = cursor.getFloat(8),
                                targetResolution = cursor.getString(9),
                                title = cursor.getString(10),
                                description = cursor.getString(11),
                                platforms = cursor.getString(12),
                                publishMode = cursor.getString(13),
                                status = cursor.getString(14),
                                progressPercent = cursor.getInt(15),
                                renderedDurationSeconds = cursor.getFloat(16),
                                r2Key = if (cursor.isNull(17)) null else cursor.getString(17),
                                r2UploadOffset = cursor.getLong(18),
                                r2TotalBytes = cursor.getLong(19),
                                lastError = if (cursor.isNull(20)) null else cursor.getString(20),
                                retryCount = cursor.getInt(21),
                                createdAt = cursor.getLong(22),
                                updatedAt = cursor.getLong(23),
                                completedAt = if (cursor.isNull(24)) null else cursor.getLong(24)
                            )
                        )
                    }
                }
                selectEligiblePendingRenderJobs(loaded)
            } catch (_: Exception) {
                selectEligiblePendingRenderJobs(database.recordingDao().getPendingRenderJobs())
            }
        }

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

        fun startPendingJobs(context: Context, jobs: List<RenderJobEntity>) {
            val eligible = selectEligiblePendingRenderJobs(jobs)
            if (eligible.isEmpty()) return
            val encoded = ArrayList(eligible.map { "${it.jobId}|${it.clipId}" })
            val first = eligible.first()
            val intent = Intent(context, RenderForegroundService::class.java).apply {
                action = ACTION_START_RENDER
                putExtra(EXTRA_JOB_ID, first.jobId)
                putExtra(EXTRA_CLIP_ID, first.clipId)
                putStringArrayListExtra(EXTRA_JOB_QUEUE, encoded)
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
                if (!jobId.isNullOrBlank()) {
                    jobQueue.removeQueued(jobId)
                    if (jobId == activeJobId) {
                        // Cancel only the active child job so the outer drain loop
                        // keeps running and advances to remaining queued jobs.
                        activeChildJob?.cancel()
                    } else if (!jobQueue.isDraining() && jobQueue.snapshotQueued().isEmpty()) {
                        stopForegroundService()
                    }
                }
            }
            ACTION_START_RENDER -> {
                val incoming = mutableListOf<Pair<String, String>>()
                val batch = intent.getStringArrayListExtra(EXTRA_JOB_QUEUE)
                if (!batch.isNullOrEmpty()) {
                    for (entry in batch) {
                        val idx = entry.indexOf('|')
                        if (idx > 0) {
                            val jId = entry.substring(0, idx)
                            val cId = entry.substring(idx + 1)
                            incoming.add(jId to cId)
                        }
                    }
                }
                val singleJobId = intent.getStringExtra(EXTRA_JOB_ID)
                val singleClipId = intent.getStringExtra(EXTRA_CLIP_ID) ?: ""
                if (!singleJobId.isNullOrEmpty()) {
                    incoming.add(singleJobId to singleClipId)
                }

                val shouldStartDrain = jobQueue.enqueueAndShouldStartDrain(incoming)

                if (!isForegroundStarted) {
                    startForegroundWithNotification("Preparing video render...", 0)
                }

                if (shouldStartDrain) {
                    drainRenderQueue()
                } else if (!jobQueue.isDraining() && jobQueue.snapshotQueued().isEmpty()) {
                    stopForegroundService()
                }
            }
        }
        return START_NOT_STICKY
    }

    private fun drainRenderQueue() {
        drainJob = lifecycleScope.launch(Dispatchers.IO) {
            try {
                while (isActive) {
                    var ranAnyInPass = false
                    while (isActive) {
                        val next = jobQueue.pollNext() ?: break
                        ranAnyInPass = true
                        val (jobId, clipId) = next
                        activeJobId = jobId
                        val childJob = Job(coroutineContext[Job])
                        activeChildJob = childJob
                        try {
                            withContext(childJob) {
                                executeSingleRenderJob(jobId, clipId)
                            }
                        } catch (ce: CancellationException) {
                            if (!coroutineContext.isActive) throw ce
                            android.util.Log.i("RenderForeground", "Render job $jobId cancelled; moving to next queued job")
                        } catch (e: Exception) {
                            android.util.Log.e("RenderForeground", "Unexpected error draining job $jobId: ${e.message}", e)
                        } finally {
                            if (activeChildJob === childJob) {
                                activeChildJob = null
                            }
                            childJob.cancel()
                            jobQueue.clearActive(jobId)
                            activeJobId = null
                        }
                    }

                    if (ranAnyInPass) {
                        reclaimSourceVideosSafe(completedClipId = null)
                    }

                    // Atomically check queue on Main right before stopping foreground service so
                    // an ACTION_START_RENDER arriving during cleanup cannot be stranded.
                    var stopped = false
                    withContext(Dispatchers.Main) {
                        if (jobQueue.finishDrainIfEmpty()) {
                            stopForegroundService()
                            stopped = true
                        }
                    }
                    if (stopped) break
                }
            } catch (ce: CancellationException) {
                jobQueue.reset()
                throw ce
            }
        }
    }

    private suspend fun executeSingleRenderJob(jobId: String, clipId: String) = coroutineScope {
        val jobScope: CoroutineScope = this
        val database = AppDatabase.getDatabase(applicationContext)
        val dao = database.recordingDao()

        val job = dao.getRenderJobById(jobId)
        if (job == null) {
            android.util.Log.e("RenderForeground", "Render job $jobId not found in Room DB")
            return@coroutineScope
        }

        // Genuinely resumable: If video was already rendered, resume R2 upload/publish directly without re-rendering
        val existingOutput = File(job.outputVideoPath)
        if (existingOutput.exists() && existingOutput.length() > 0L &&
            (job.status == RenderJobEntity.STATUS_RENDER_COMPLETE ||
                job.status == RenderJobEntity.STATUS_UPLOADING ||
                job.status == RenderJobEntity.STATUS_UPLOAD_COMPLETE)) {
            android.util.Log.i("RenderForeground", "Job $jobId output already rendered (${existingOutput.length()} bytes). Resuming cloud upload directly.")
            updateNotification("Resuming cloud upload...", 0)
            enqueueR2UploadIfReady(jobId, job.outputVideoPath, jobScope)
            return@coroutineScope
        }

        try {
            // 1. Verify source video exists; if missing locally, stage from Cloud Worker using valid Worker JWT
            var currentJob = job
            val sourceFile = File(currentJob.sourceVideoPath)
            if (!sourceFile.exists() || sourceFile.length() == 0L) {
                updateNotification("Staging source video from cloud...", 0)
                val pairing = PairingManager(applicationContext)
                val token = resolveWorkerJwt(pairing.cloudAccessToken)
                    ?: throw IllegalStateException("Cloud sign-in required to stage source video for clip ${currentJob.clipId}")
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

            val renderer = AndroidClipRenderer(applicationContext, jobScope)
            val outcome = renderer.renderClip(
                job = currentJob,
                onProgress = { progressPercent: Int ->
                    val now = System.currentTimeMillis()
                    if (now - lastProgressNotificationMs > 250 || progressPercent == 100) {
                        lastProgressNotificationMs = now
                        updateNotification("Rendering video: $progressPercent%", progressPercent)
                        jobScope.launch(Dispatchers.IO) {
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
                val duration = outcome.durationSeconds

                dao.markRenderJobRenderComplete(
                    jobId = jobId,
                    status = RenderJobEntity.STATUS_RENDER_COMPLETE,
                    outputPath = outcome.outputPath,
                    thumbnailPath = outcome.thumbnailPath,
                    duration = duration,
                    updatedAt = System.currentTimeMillis()
                )

                updateNotification("Render complete (${String.format(Locale.US, "%.1f", duration)}s). Uploading to cloud...", 100)
                enqueueR2UploadIfReady(jobId, outcome.outputPath, jobScope)
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
        } catch (ce: CancellationException) {
            throw ce
        } catch (e: Exception) {
            android.util.Log.e("RenderForeground", "Render job failed: ${e.message}", e)
            dao.updateRenderJobError(
                jobId = jobId,
                status = RenderJobEntity.STATUS_FAILED,
                lastError = e.message ?: "Render failure",
                updatedAt = System.currentTimeMillis()
            )
            updateNotification("Render error: ${e.message}", 0)
        }
    }

    private suspend fun enqueueR2UploadIfReady(jobId: String, outputPath: String, jobScope: CoroutineScope) {
        val database = AppDatabase.getDatabase(applicationContext)
        val dao = database.recordingDao()
        val job = dao.getRenderJobById(jobId) ?: return
        val outputFile = File(outputPath)
        if (!outputFile.exists() || outputFile.length() == 0L) {
            android.util.Log.e("RenderForeground", "Rendered output file missing or empty: $outputPath")
            dao.updateRenderJobError(
                jobId = jobId,
                status = RenderJobEntity.STATUS_FAILED,
                lastError = "Rendered output file missing or empty",
                updatedAt = System.currentTimeMillis()
            )
            return
        }

        val pairing = PairingManager(applicationContext)
        val token = resolveWorkerJwt(pairing.cloudAccessToken)
        if (token == null) {
            val errMsg = "Cloud sign-in required (missing Worker JWT). Sign in to Dispatch Cloud and retry."
            android.util.Log.e("RenderForeground", "Refusing R2 upload for job $jobId: valid Worker JWT unavailable")
            dao.updateRenderJobError(
                jobId = jobId,
                status = RenderJobEntity.STATUS_FAILED,
                lastError = errMsg,
                updatedAt = System.currentTimeMillis()
            )
            updateNotification("Upload paused: sign in to Dispatch Cloud to publish.", 0)
            return
        }

        val workerUrl = "https://dispatch-cloud.resolviaai.workers.dev"
        val client = OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(300, TimeUnit.SECONDS)
            .readTimeout(300, TimeUnit.SECONDS)
            .build()
        val gson = Gson()

        try {
            var finalKey = job.r2Key?.takeIf { it.isNotBlank() && job.status == RenderJobEntity.STATUS_UPLOAD_COMPLETE }
            if (finalKey == null) {
                dao.updateRenderJobProgress(
                    jobId = jobId,
                    status = RenderJobEntity.STATUS_UPLOADING,
                    progress = 0,
                    updatedAt = System.currentTimeMillis()
                )
                updateNotification("Reserving cloud upload...", 0)

                // Step 1: POST /reserve with a fresh per-attempt client_token
                val clientToken = mintReservationClientToken(job.jobId)
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
                                    jobScope.launch(Dispatchers.IO) {
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

                finalKey = client.newCall(completeReq).execute().use { resp ->
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
            }

            updateNotification("Upload complete. Enqueueing social publishing...", 100)

            // Step 4: POST /publish/queue and require HTTP 2xx before transitioning to STATUS_PUBLISHING_QUEUED
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
                addProperty("idempotency_key", job.jobId)
            }

            val pubReq = Request.Builder()
                .url("$workerUrl/publish/queue")
                .header("Authorization", "Bearer $token")
                .post(publishBody.toString().toRequestBody("application/json; charset=UTF-8".toMediaType()))
                .build()

            val pubOutcome = client.newCall(pubReq).execute().use { resp ->
                val bodyStr = resp.body?.string()
                evaluatePublishQueueResponse(resp.code, bodyStr)
            }

            if (!pubOutcome.queued) {
                throw IllegalStateException(pubOutcome.errorMessage ?: "Cloud publish enqueue failed")
            }

            dao.markRenderJobPublishingQueued(
                jobId = jobId,
                status = pubOutcome.statusToPersist,
                r2Key = finalKey,
                completedAt = System.currentTimeMillis(),
                updatedAt = System.currentTimeMillis()
            )
            updateNotification(pubOutcome.notificationText, 100)
            reclaimSourceVideosSafe(completedClipId = job.clipId)
        } catch (ce: CancellationException) {
            throw ce
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

    private fun reclaimSourceVideosSafe(completedClipId: String?) {
        try {
            val sourceVideosDir = File(applicationContext.cacheDir, "source_videos")
            if (!sourceVideosDir.exists()) return
            val db = AppDatabase.getDatabase(applicationContext)
            val allJobs = mutableListOf<RenderJobEntity>()
            db.openHelper.readableDatabase.query(
                "SELECT jobId, clipId, status, sourceVideoPath, outputVideoPath, updatedAt FROM render_jobs"
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    allJobs.add(
                        RenderJobEntity(
                            jobId = cursor.getString(0),
                            clipId = cursor.getString(1),
                            renderFingerprint = "",
                            projectDocumentJson = "{}",
                            sourceVideoPath = cursor.getString(3),
                            outputVideoPath = cursor.getString(4),
                            status = cursor.getString(2),
                            updatedAt = cursor.getLong(5)
                        )
                    )
                }
            }
            val res = reclaimUnusedSourceVideos(
                sourceVideosDir = sourceVideosDir,
                jobs = allJobs,
                completedClipId = completedClipId
            )
            if (res.deletedFiles.isNotEmpty()) {
                android.util.Log.i("RenderForeground", "Reclaimed ${res.deletedFiles.size} unused staged source video(s)")
            }
        } catch (e: Exception) {
            android.util.Log.w("RenderForeground", "Source cache reclamation skipped: ${e.message}")
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
                val tempFile = File(cacheVideosDir, "${clipId}.mp4.tmp")
                if (tempFile.exists()) tempFile.delete()

                val dlReq = Request.Builder().url(sourceUrl).get().build()
                client.newCall(dlReq).execute().use { dlResp ->
                    if (!dlResp.isSuccessful) return null
                    val body = dlResp.body ?: return null
                    FileOutputStream(tempFile).use { fos ->
                        body.byteStream().copyTo(fos)
                    }
                }
                if (tempFile.exists() && tempFile.length() > 0L) {
                    if (targetFile.exists()) targetFile.delete()
                    if (tempFile.renameTo(targetFile) && targetFile.exists() && targetFile.length() > 0L) {
                        targetFile
                    } else {
                        tempFile.delete()
                        null
                    }
                } else {
                    tempFile.delete()
                    null
                }
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
        isForegroundStarted = true
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
        isForegroundStarted = false
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
        activeChildJob?.cancel()
        drainJob?.cancel()
        jobQueue.reset()
        stopForegroundService()
        super.onDestroy()
    }
}

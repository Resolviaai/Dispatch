package com.resolvia.dispatch.sync

import android.content.Context
import androidx.room.withTransaction
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.recorder.RecordingForegroundService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.FileInputStream
import java.io.IOException
import java.io.RandomAccessFile
import java.net.URL
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import android.media.MediaMetadataRetriever
import java.text.SimpleDateFormat
import java.util.*
import java.util.concurrent.TimeUnit

/**
 * Autonomous Direct YouTube Cloud Uploader for Dispatch Mobile.
 * Uploads finalized video recordings directly to YouTube Data API v3 as Private,
 * using chunked resumable PUT (8 MB chunks) with byte offset tracking in SQLite Room.
 * If network drops, queries Content-Range: bytes * /total and resumes without repeating data.
 */
data class DirectUploadResult(
    val success: Boolean,
    val youtubeVideoId: String? = null,
    val failureReason: String? = null
)

class YouTubeDirectUploadWorker(
    appContext: Context,
    params: WorkerParameters
) : CoroutineWorker(appContext, params) {

    private val db = AppDatabase.getDatabase(appContext)
    private val dao = db.recordingDao()
    private val pairingManager = PairingManager(appContext)
    private val gson = Gson()
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(300, TimeUnit.SECONDS)
        .readTimeout(300, TimeUnit.SECONDS)
        .build()

    /** Dispatch cloud control plane; holds the YouTube vault credentials. */
    private val workerUrl = "https://dispatch-cloud.resolviaai.workers.dev"

    /** Minimum gap between YouTube processing-detail checks for one segment. */
    private val processingRecheckIntervalMs = 2L * 60_000L

    /** Upper bound of YouTube processing checks before an explicit verdict. */
    private val maxProcessingChecks = 96

    /** Outcome of asking the cloud control plane for a scoped resumable upload session. */
    private sealed class CloudSessionRequest {
        data class SessionUrl(val url: String) : CloudSessionRequest()
        object AwaitingCredentials : CloudSessionRequest()
        object Retryable : CloudSessionRequest()
    }

    /** True when the WebView pushed a usable Supabase JWT + vault account pairing. */
    private fun cloudSessionActive(): Boolean =
        pairingManager.cloudAccessToken.isNotBlank() && pairingManager.rawVaultAccountId.isNotBlank()

    /**
     * Route A credential boundary: uploads must not require the PC or legacy
     * locally-stored OAuth while the cloud vault session is available. A segment
     * that already carries a resumable session URL or a recorded upload origin
     * keeps whichever mode created it; brand-new sessions prefer the cloud
     * vault and fall back to legacy local credentials only when the cloud
     * session is absent.
     */
    private fun decideUploadMode(seg: com.resolvia.dispatch.data.SegmentEntity): Boolean =
        UploadModePolicy.resolveUploadMode(
            uploadOrigin = seg.uploadOrigin,
            savedSessionUrl = pairingManager.getUploadSessionUrl(seg.segmentId),
            savedSessionIsCloud = if (pairingManager.getUploadSessionUrl(seg.segmentId).isNotBlank())
                pairingManager.isCloudUploadSession(seg.segmentId) else null,
            cloudSessionActive = cloudSessionActive()
        )

    /**
     * Asks the Dispatch cloud worker for a scoped YouTube resumable upload
     * session (POST /youtube/upload-sessions). The phone never holds YouTube
     * OAuth credentials here: the worker resolves the vault account and returns
     * a pre-authorized upload URL on www.googleapis.com, which the phone then
     * PUTs bytes directly to.
     */
    private suspend fun initiateCloudResumableSession(
        seg: com.resolvia.dispatch.data.SegmentEntity,
        file: File,
        dispatchId: String
    ): CloudSessionRequest {
        val token = pairingManager.cloudAccessToken
        val accountId = pairingManager.rawVaultAccountId.trim()
        val sha = if (seg.sha256Hash.isNotBlank()) seg.sha256Hash else calculateSha256(file)
        val body = JsonObject().apply {
            addProperty("account_id", accountId)
            addProperty("segment_id", seg.segmentId)
            addProperty("session_id", seg.sessionId)
            addProperty("sequence_number", seg.sequenceNumber)
            addProperty("dispatch_id", dispatchId)
            addProperty("file_size", file.length())
            if (sha.isNotBlank()) addProperty("sha256", sha)
        }
        val req = Request.Builder()
            .url("$workerUrl/youtube/upload-sessions")
            .header("Authorization", "Bearer $token")
            .post(body.toString().toRequestBody("application/json; charset=UTF-8".toMediaType()))
            .build()
        return try {
            client.newCall(req).execute().use { resp ->
                val respBody = resp.body?.string()
                when {
                    resp.isSuccessful && !respBody.isNullOrBlank() -> {
                        val json = gson.fromJson(respBody, JsonObject::class.java)
                        val url = json?.get("upload_url")?.asString
                        // Defense in depth: only PUT chunks to a genuine YouTube
                        // resumable host even if the worker were compromised.
                        val validHost = url != null && runCatching { URL(url) }.getOrNull()?.let { parsed ->
                            parsed.protocol == "https" && parsed.host == "www.googleapis.com"
                        } == true
                        if (validHost) {
                            pairingManager.saveUploadSessionUrl(seg.segmentId, url!!)
                            pairingManager.markCloudUploadSession(seg.segmentId, true)
                            CloudSessionRequest.SessionUrl(url)
                        } else {
                            android.util.Log.e("YouTubeUploader", "Worker returned an invalid resumable session URL for ${seg.segmentId}")
                            CloudSessionRequest.Retryable
                        }
                    }
                    // 401: handed-out Supabase JWT expired. 409: vault account
                    // disconnected. Both are paused-until-token-push, not errors.
                    resp.code == 401 || resp.code == 409 -> CloudSessionRequest.AwaitingCredentials
                    else -> {
                        android.util.Log.e("YouTubeUploader", "Upload session request failed: HTTP ${resp.code} for ${seg.segmentId}")
                        CloudSessionRequest.Retryable
                    }
                }
            }
        } catch (e: Exception) {
            android.util.Log.e("YouTubeUploader", "Cloud upload session request error for ${seg.segmentId}: ${e.message}")
            CloudSessionRequest.Retryable
        }
    }

    /** Scoped YouTube status checks and lookups performed by the cloud worker. */
    private suspend fun cloudYouTubeApi(endpoint: String, payload: JsonObject): JsonObject? {
        val body = JsonObject().apply {
            addProperty("account_id", pairingManager.rawVaultAccountId.trim())
            payload.entrySet().forEach { entry -> add(entry.key, entry.value) }
        }
        val req = Request.Builder()
            .url("$workerUrl/$endpoint")
            .header("Authorization", "Bearer ${pairingManager.cloudAccessToken}")
            .post(body.toString().toRequestBody("application/json; charset=UTF-8".toMediaType()))
            .build()
        return try {
            client.newCall(req).execute().use { resp ->
                val respBody = resp.body?.string()
                if (resp.code == 401 || resp.code == 409) {
                    android.util.Log.w("YouTubeUploader", "Cloud API $endpoint paused on HTTP ${resp.code} (awaiting fresh cloud token)")
                    null
                } else if (resp.isSuccessful && !respBody.isNullOrBlank()) {
                    gson.fromJson(respBody, JsonObject::class.java)
                } else {
                    android.util.Log.w("YouTubeUploader", "Cloud API $endpoint failed: HTTP ${resp.code}")
                    null
                }
            }
        } catch (e: Exception) {
            android.util.Log.w("YouTubeUploader", "Cloud API $endpoint failed: ${e.message}")
            null
        }
    }

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        var hasFailures = false
        val failedInThisRun = mutableSetOf<String>()

        // 0. Recover any interrupted sessions from prior crashes before draining
        recoverInterruptedSessions(applicationContext)

        // Advance segments already uploaded but still awaiting YouTube's verdict
        reconcileYouTubeProcessingStatuses()

        // Reconcile any previously uploaded segments awaiting PC ingestion acknowledgement
        reconcilePendingPcAcknowledgements()

        // Reliable drain loop: processes all pending segments including ones added during upload
        while (true) {
            val pendingSegments = dao.getPendingUploadSegments().filter { it.segmentId !in failedInThisRun }
            if (pendingSegments.isEmpty()) break

            // 1. Check if direct upload credentials exist: the cloud vault session
            // (Route A) or legacy locally-stored OAuth (PC-pairing fallback).
            var accessToken = pairingManager.youtubeAccessToken
            if (accessToken.isBlank() && pairingManager.youtubeRefreshToken.isNotBlank()) {
                accessToken = refreshYouTubeToken() ?: ""
            }

            if (accessToken.isBlank() && !cloudSessionActive()) {
                android.util.Log.w("YouTubeUploader", "YouTube credentials missing; marking segments waiting")
                for (seg in pendingSegments) {
                    dao.updateSegmentStatus(seg.segmentId, "WAITING_FOR_YOUTUBE")
                }
                return@withContext Result.failure()
            }

            // 2. Iterate and upload recordings directly to YouTube (Non-blocking batch)
            for (seg in pendingSegments) {
                // If already uploaded or already has YouTube ID, check for durable PC acknowledgement before deleting
                if (seg.status == "UPLOADED_TO_YOUTUBE" || seg.status == "PROCESSED_ON_YOUTUBE" || !seg.youtubeVideoId.isNullOrBlank()) {
                    val videoId = seg.youtubeVideoId
                    val dispatchId = "dsp_${seg.finalizedAt ?: seg.createdAt}_${seg.segmentId.takeLast(6)}"
                    val f = File(seg.filepath)
                    val sha = if (seg.sha256Hash.isNotBlank()) seg.sha256Hash else (if (f.exists()) calculateSha256(f) else "")
                    val fSize = if (seg.fileSizeBytes > 0L) seg.fileSizeBytes else (if (f.exists()) f.length() else 0L)
                    val pcAck = if (pairingManager.pcHost.isNotBlank() && !videoId.isNullOrBlank()) {
                        checkPcAcknowledgement(pairingManager.pcHost, videoId, dispatchId, seg.segmentId, sha, fSize)
                    } else false

                    if (pcAck) {
                        dao.updateSegmentStatus(seg.segmentId, "VERIFIED_BY_LAPTOP")
                        dao.deleteOutboxItem(seg.segmentId)
                        if (f.exists()) {
                            try { f.delete() } catch (_: Exception) {}
                        }
                        android.util.Log.i("YouTubeUploader", "Segment ${seg.segmentId} durably acknowledged by PC. Safely removed local file.")
                    } else {
                        // CRITICAL: NEVER delete local footage without verified PC storage acknowledgement!
                        dao.updateSegmentStatus(seg.segmentId, "PROCESSED_ON_YOUTUBE")
                        dao.deleteOutboxItem(seg.segmentId)
                        android.util.Log.i("YouTubeUploader", "Segment ${seg.segmentId} already uploaded (Video: $videoId), awaiting PC ingestion acknowledgement. Local recording preserved.")
                    }
                    continue
                }

                val file = File(seg.filepath)
                if (!file.exists() || file.length() == 0L) {
                    android.util.Log.w("YouTubeUploader", "Skipping missing or empty file for ${seg.segmentId}")
                    dao.updateSegmentStatus(seg.segmentId, "FAILED_MISSING_FILE")
                    continue
                }

                val dispatchId = "dsp_${seg.finalizedAt ?: System.currentTimeMillis()}_${seg.segmentId.takeLast(6)}"
                dao.updateSegmentStatus(seg.segmentId, "UPLOADING")
                val useCloud = decideUploadMode(seg)
                dao.updateSegmentUploadOrigin(seg.segmentId, if (useCloud) "cloud" else "legacy")
                val result = uploadToYouTubeDirect(accessToken, file, dispatchId, seg, useCloud)

                if (result.failureReason == "WAITING_FOR_CREDENTIALS") {
                    hasFailures = true
                    // Exclude from the rest of THIS drain pass so the loop cannot
                    // hot-spin re-selecting the same segment (the DAO pending set
                    // includes WAITING_FOR_YOUTUBE). The paused segment is retried
                    // on the next worker run, via pushed cloud credentials, or
                    // via WorkManager backoff — never in this same tight loop.
                    failedInThisRun.add(seg.segmentId)
                    android.util.Log.i("YouTubeUploader", "Segment ${seg.segmentId} paused: awaiting fresh cloud token or vault reconnect")
                    continue
                }

                if (result.success && !result.youtubeVideoId.isNullOrBlank()) {
                    val videoId = result.youtubeVideoId
                    // CRITICAL FORENSIC FIX: Receipt of videoId alone does NOT mean YouTube has finished processing!
                    // Check videos.list(part=processingDetails,status,id=videoId)
                    val processingStatus = checkYouTubeProcessingStatus(accessToken, videoId, useCloud)
                    android.util.Log.i("YouTubeUploader", "Segment ${seg.segmentId} (Video: $videoId) processing status: $processingStatus")

                    if (processingStatus == "succeeded") {
                        val updated = seg.copy(
                            status = "PROCESSED_ON_YOUTUBE",
                            youtubeVideoId = videoId
                        )
                        dao.updateSegment(updated)
                        dao.deleteOutboxItem(seg.segmentId)
                        pairingManager.clearUploadSessionUrl(seg.segmentId)
                        
                        // Check for immediate durable PC acknowledgement if PC is reachable
                        val sha = if (seg.sha256Hash.isNotBlank()) seg.sha256Hash else calculateSha256(file)
                        val pcAck = if (pairingManager.pcHost.isNotBlank()) {
                            checkPcAcknowledgement(pairingManager.pcHost, videoId, dispatchId, seg.segmentId, sha, file.length())
                        } else false

                        if (pcAck) {
                            dao.updateSegmentStatus(seg.segmentId, "VERIFIED_BY_LAPTOP")
                            val f = File(seg.filepath)
                            if (f.exists()) {
                                try { f.delete() } catch (_: Exception) {}
                            }
                            android.util.Log.i("YouTubeUploader", "Segment ${seg.segmentId} durably acknowledged by PC. Safely removed local file.")
                        } else {
                            android.util.Log.i("YouTubeUploader", "Segment ${seg.segmentId} processed successfully on YouTube. Local media preserved until PC ingestion.")
                        }
                    } else if (processingStatus == "processing" || processingStatus == "uploaded") {
                        val updated = seg.copy(
                            status = "UPLOADED_AWAITING_PROCESSING",
                            youtubeVideoId = videoId
                        )
                        dao.updateSegment(updated)
                        dao.deleteOutboxItem(seg.segmentId)
                        pairingManager.clearUploadSessionUrl(seg.segmentId)
                        android.util.Log.i("YouTubeUploader", "Segment ${seg.segmentId} uploaded; awaiting YouTube processing ($processingStatus). File retained.")
                    } else if (processingStatus == "failed") {
                        android.util.Log.e("YouTubeUploader", "Segment ${seg.segmentId} video $videoId failed processing on YouTube.")
                        dao.updateSegmentStatus(seg.segmentId, "FAILED_YOUTUBE_PROCESSING")
                        failedInThisRun.add(seg.segmentId)
                        hasFailures = true
                    } else {
                        // Ambiguous processing state: retain file and outbox status
                        val updated = seg.copy(
                            status = "UPLOADED_TO_YOUTUBE",
                            youtubeVideoId = videoId
                        )
                        dao.updateSegment(updated)
                        pairingManager.clearUploadSessionUrl(seg.segmentId)
                        android.util.Log.w("YouTubeUploader", "Segment ${seg.segmentId} uploaded; processing check was ambiguous. File retained.")
                    }
                } else {
                    hasFailures = true
                    failedInThisRun.add(seg.segmentId)
                    result.failureReason?.let {
                        android.util.Log.e("YouTubeUploader", "Retryable upload failure for ${seg.segmentId}: $it")
                    }
                    val offset = dao.getOutboxOffset(seg.segmentId) ?: 0L
                    if (offset >= file.length() && file.length() > 0L) {
                        android.util.Log.w("YouTubeUploader", "Segment ${seg.segmentId} bytes fully sent but remote ID unconfirmed; keeping outbox entry for reconciliation.")
                    }
                    dao.updateSegmentStatus(seg.segmentId, "FAILED_RETRY")
                    // Do NOT abort entire loop; continue with remaining segments
                    continue
                }
            }
        }

        // Final sweep: Reconcile any previously uploaded segments awaiting PC ingestion acknowledgement
        reconcilePendingPcAcknowledgements()
        // But also advance any segments whose YouTube processing completed in the meantime
        reconcileYouTubeProcessingStatuses()

        if (hasFailures) {
            Result.retry()
        } else {
            Result.success()
        }
    }

    private suspend fun uploadToYouTubeDirect(
        accessToken: String,
        file: File,
        dispatchId: String,
        seg: com.resolvia.dispatch.data.SegmentEntity,
        useCloud: Boolean,
        sessionRestarted: Boolean = false
    ): DirectUploadResult {
        var currentAccessToken = accessToken.takeIf { !useCloud } ?: ""
        val segId = seg.segmentId
        val totalBytes = file.length()
        if (totalBytes == 0L) return DirectUploadResult(false)

        // Cloud route with no live vault session must never POST into the void
        // (a blank-bearer request would surface as a 401 hot path, i.e. the
        // repeated-retry loop) — pause immediately for credential re-push.
        if (useCloud && !cloudSessionActive() && !pairingManager.isCloudUploadSession(segId)) {
            return DirectUploadResult(false, failureReason = "WAITING_FOR_CREDENTIALS")
        }

        // 0. Idempotency protection: check if video was already published to YouTube during previous attempt
        val existingVideoId = findExistingUploadByDispatchId(currentAccessToken, dispatchId, useCloud)
        if (!existingVideoId.isNullOrBlank()) {
            android.util.Log.i("YouTubeUploader", "Upload already confirmed on YouTube for $dispatchId: $existingVideoId")
            return DirectUploadResult(success = true, youtubeVideoId = existingVideoId)
        }

        val timestampStr = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(Date())
        val title = "[DISPATCH] Session ${seg.sessionId.takeLast(6)} - Part ${seg.sequenceNumber} ($timestampStr)"
        val sha = if (seg.sha256Hash.isNotBlank()) seg.sha256Hash else calculateSha256(file)
        val description = "dispatch_id: $dispatchId\nsession_id: ${seg.sessionId}\nsegment_id: ${seg.segmentId}\nsequence_number: ${seg.sequenceNumber}\nsource_sha256: $sha\nsource_file_size: ${file.length()}\nAutonomous mobile recording uploaded by Dispatch."

        // 1. Resumable Upload Initiation Request
        val metadataJson = JsonObject().apply {
            val snippet = JsonObject().apply {
                addProperty("title", title)
                addProperty("description", description)
                val tags = com.google.gson.JsonArray().apply {
                    add("dispatch")
                    add("dispatch_id_$dispatchId")
                    add("dispatch_session_${seg.sessionId}")
                    add("dispatch_seg_${seg.sequenceNumber}")
                    if (sha.isNotBlank()) {
                        add("sha256_$sha")
                    }
                }
                add("tags", tags)
                addProperty("categoryId", "22")
            }
            val status = JsonObject().apply {
                addProperty("privacyStatus", "unlisted")
                addProperty("selfDeclaredMadeForKids", false)
            }
            add("snippet", snippet)
            add("status", status)
        }

        val initUrl = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
        val initReq = Request.Builder()
            .url(initUrl)
            .header("Authorization", "Bearer $currentAccessToken")
            .header("X-Upload-Content-Type", "video/mp4")
            .header("X-Upload-Content-Length", totalBytes.toString())
            .post(metadataJson.toString().toRequestBody("application/json; charset=UTF-8".toMediaType()))
            .build()

        var uploadUrl: String? = pairingManager.getUploadSessionUrl(segId).ifBlank { null }

        // If no existing resumable session URL, initiate new session
        if (uploadUrl == null) {
            if (useCloud) {
                when (val outcome = initiateCloudResumableSession(seg, file, dispatchId)) {
                    is CloudSessionRequest.SessionUrl -> uploadUrl = outcome.url
                    is CloudSessionRequest.AwaitingCredentials -> {
                        dao.updateSegmentStatus(segId, "WAITING_FOR_YOUTUBE")
                        return DirectUploadResult(false, failureReason = "WAITING_FOR_CREDENTIALS")
                    }
                    is CloudSessionRequest.Retryable -> return DirectUploadResult(false)
                }
            } else {
                try {
                    client.newCall(initReq).execute().use { resp ->
                        if (resp.code == 401) {
                            val freshToken = refreshYouTubeToken()
                            if (freshToken != null) {
                                return uploadToYouTubeDirect(freshToken, file, dispatchId, seg, useCloud)
                            }
                            return DirectUploadResult(false)
                        }
                        if (!resp.isSuccessful) {
                            android.util.Log.e("YouTubeUploader", "Init failed: HTTP ${resp.code}")
                            return DirectUploadResult(false)
                        }
                        uploadUrl = resp.header("Location")
                        if (uploadUrl != null) {
                            pairingManager.saveUploadSessionUrl(segId, uploadUrl!!)
                        }
                    }
                } catch (e: Exception) {
                    android.util.Log.e("YouTubeUploader", "Failed to initiate resumable upload session: ${e.message}")
                    return DirectUploadResult(false)
                }
            }
        }

        var destination = uploadUrl ?: return DirectUploadResult(false)

        // 2. Chunked Resumable Upload (8 MB chunks) with persistent byte progress
        val chunkSize = 8 * 1024 * 1024L // 8MB chunks
        val savedOffset = dao.getOutboxOffset(segId) ?: 0L

        // Query session status if resuming after disconnect or retrying existing session
        val probedOffset = queryUploadStatus(destination, currentAccessToken, totalBytes)
        if (probedOffset == -1L) {
            // Session expired on YouTube: clear and restart fresh
            if (sessionRestarted) {
                return DirectUploadResult(false, failureReason = "Resumable upload session expired after restart")
            }
            pairingManager.clearUploadSessionUrl(segId)
            dao.updateOutboxOffset(segId, 0L)
            return uploadToYouTubeDirect(currentAccessToken, file, dispatchId, seg, useCloud, sessionRestarted = true)
        }
        val confirmedInitialOffset = probedOffset
            ?: return DirectUploadResult(false, failureReason = "Could not confirm resumable upload offset; source retained for retry")
        // The session query is authoritative, including when the device's saved offset is ahead.
        var currentOffset: Long = confirmedInitialOffset
        if (currentOffset != savedOffset) dao.updateOutboxOffset(segId, currentOffset)
        val progress = ConfirmedUploadProgress(currentOffset, totalBytes)

        suspend fun reconcileServerOffset(serverOffset: Long?): Boolean {
            val changed = progress.observe(serverOffset)
            currentOffset = progress.offset
            if (changed) dao.updateOutboxOffset(segId, currentOffset)
            return changed
        }

        suspend fun waitForNoProgressRetry(): Boolean {
            if (progress.retryExhausted) return false
            val jitter = kotlin.random.Random.nextLong(0L, ConfirmedUploadProgress.MAX_JITTER_MS + 1L)
            delay(progress.retryDelayMs(jitter))
            return true
        }

        // Upload in 8MB slices
        var raf: RandomAccessFile? = null
        var tokenRefreshAttempts = 0
        try {
            raf = RandomAccessFile(file, "r")
            while (currentOffset < totalBytes) {
                val start = currentOffset
                val end = minOf(start + chunkSize - 1L, totalBytes - 1L)
                val chunkLen = (end - start + 1L).toInt()

                val buffer = ByteArray(chunkLen)
                raf.seek(start)
                raf.readFully(buffer)

                val chunkBody = buffer.toRequestBody("video/mp4".toMediaType(), 0, chunkLen)
                // Cloud vault sessions: the worker-handed upload URL is already
                // scoped and pre-authorized; adding a phone OAuth header there
                // would be meaningless. Legacy sessions carry the local token.
                val chunkReqBuilder = Request.Builder()
                    .url(destination)
                    .header("Content-Length", chunkLen.toString())
                    .header("Content-Range", "bytes $start-$end/$totalBytes")
                if (currentAccessToken.isNotBlank()) {
                    chunkReqBuilder.header("Authorization", "Bearer $currentAccessToken")
                }
                val chunkReq = chunkReqBuilder.put(chunkBody).build()

                var retryWithNewToken = false
                var waitForNoProgress = false
                val response = try {
                    client.newCall(chunkReq).execute()
                } catch (e: IOException) {
                    android.util.Log.w("YouTubeUploader", "Ambiguous chunk failure for $segId at $start; checking server offset", e)
                    val probed = queryUploadStatus(destination, currentAccessToken, totalBytes)
                    if (probed == -1L) {
                        if (sessionRestarted) {
                            return DirectUploadResult(false, failureReason = "Resumable upload session expired after restart")
                        }
                        pairingManager.clearUploadSessionUrl(segId)
                        dao.updateOutboxOffset(segId, 0L)
                        return uploadToYouTubeDirect(currentAccessToken, file, dispatchId, seg, useCloud, sessionRestarted = true)
                    }
                    if (!reconcileServerOffset(probed)) {
                        if (!waitForNoProgressRetry()) {
                            return DirectUploadResult(false, failureReason = "Upload made no confirmed progress after ${progress.consecutiveNoProgress} status checks")
                        }
                    }
                    continue
                }

                response.use { resp ->
                    when (resp.code) {
                        308 -> {
                            // Validate server's Range header.
                            // If Range header is absent or invalid, never optimistically advance offset!
                            val rangeHeader = resp.header("Range")
                            val confirmedOffset = parseConfirmedRangeOffset(rangeHeader, totalBytes)

                            val serverOffset = if (confirmedOffset != null) {
                                confirmedOffset
                            } else {
                                android.util.Log.w("YouTubeUploader", "308 with missing/invalid/non-advancing Range header ('$rangeHeader') for $segId; querying server status.")
                                val probed = queryUploadStatus(destination, currentAccessToken, totalBytes)
                                if (probed == -1L) {
                                    if (sessionRestarted) {
                                        return DirectUploadResult(false, failureReason = "Resumable upload session expired after restart")
                                    }
                                    pairingManager.clearUploadSessionUrl(segId)
                                    dao.updateOutboxOffset(segId, 0L)
                                    return uploadToYouTubeDirect(currentAccessToken, file, dispatchId, seg, useCloud, sessionRestarted = true)
                                }
                                probed
                            }
                            val changed = reconcileServerOffset(serverOffset)
                            if (!changed) {
                                waitForNoProgress = true
                                android.util.Log.w("YouTubeUploader", "No confirmed upload progress for $segId at offset $currentOffset")
                            } else {
                                android.util.Log.i("YouTubeUploader", "Server-confirmed upload offset for $segId: $currentOffset/$totalBytes")
                            }
                        }
                        200, 201 -> {
                            currentOffset = totalBytes
                            dao.updateOutboxOffset(segId, totalBytes)
                            val bodyStr = resp.body?.string() ?: ""
                            val videoId = try {
                                val json = gson.fromJson(bodyStr, JsonObject::class.java)
                                json.get("id")?.asString
                            } catch (_: Exception) {
                                null
                            } ?: findExistingUploadByDispatchId(currentAccessToken, dispatchId, useCloud)

                            if (!videoId.isNullOrBlank()) {
                                pairingManager.clearUploadSessionUrl(segId)
                                android.util.Log.i("YouTubeUploader", "Upload 100% complete for $segId (Video ID: $videoId)")
                                return DirectUploadResult(success = true, youtubeVideoId = videoId)
                            } else {
                                android.util.Log.w("YouTubeUploader", "Upload completed HTTP ${resp.code} for $segId, but video ID could not be confirmed. Retaining session URL.")
                                return DirectUploadResult(success = false, youtubeVideoId = null)
                            }
                        }
                        401 -> {
                            if (useCloud) {
                                // Cloud vault sessions cannot be refreshed locally:
                                // a 401 means the session URL was invalidated, so
                                // re-open through the worker once, then pause.
                                pairingManager.clearUploadSessionUrl(segId)
                                if (sessionRestarted) {
                                    return DirectUploadResult(false, failureReason = "WAITING_FOR_CREDENTIALS")
                                }
                                return uploadToYouTubeDirect(accessToken, file, dispatchId, seg, useCloud, sessionRestarted = true)
                            }
                            if (tokenRefreshAttempts < 2) {
                                val freshToken = refreshYouTubeToken()
                                if (freshToken != null) {
                                    currentAccessToken = freshToken
                                    tokenRefreshAttempts++
                                    retryWithNewToken = true
                                } else {
                                    return DirectUploadResult(false)
                                }
                            } else {
                                android.util.Log.e("YouTubeUploader", "Exceeded token refresh attempts on 401 for $segId")
                                return DirectUploadResult(false)
                            }
                        }
                        else -> {
                            android.util.Log.e("YouTubeUploader", "Chunk error: HTTP ${resp.code} ${resp.message}")
                            return DirectUploadResult(false)
                        }
                    }
                    Unit
                }

                if (retryWithNewToken) {
                    continue
                }
                if (waitForNoProgress && !waitForNoProgressRetry()) {
                    return DirectUploadResult(false, failureReason = "Upload made no confirmed progress after ${progress.consecutiveNoProgress} responses")
                }
            }
        } catch (e: Exception) {
            android.util.Log.e("YouTubeUploader", "Exception during chunked upload: ${e.message}", e)
            return DirectUploadResult(false)
        } finally {
            try { raf?.close() } catch (_: Exception) {}
        }

        val finalCheckId = if (currentOffset >= totalBytes) findExistingUploadByDispatchId(currentAccessToken, dispatchId, useCloud) else null
        val isConfirmed = currentOffset >= totalBytes && !finalCheckId.isNullOrBlank()
        if (isConfirmed) {
            pairingManager.clearUploadSessionUrl(segId)
        }
        return DirectUploadResult(
            success = isConfirmed,
            youtubeVideoId = finalCheckId
        )
    }

    private suspend fun findExistingUploadByDispatchId(accessToken: String, dispatchId: String, useCloud: Boolean): String? {
        if (useCloud && cloudSessionActive()) {
            // Route A: the worker resolves the vault account and searches YouTube
            // for the dispatch_id tag; the phone holds no OAuth here.
            val json = cloudYouTubeApi("youtube/uploads/lookup", JsonObject().apply {
                addProperty("dispatch_id", dispatchId)
            })
            val videoId = json?.get("video_id")?.asString
            if (!videoId.isNullOrBlank()) {
                android.util.Log.i("YouTubeUploader", "Recovered existing YouTube upload for $dispatchId: $videoId")
            }
            return videoId
        }
        try {
            val queryUrl = "https://www.googleapis.com/youtube/v3/search?part=snippet&forMine=true&q=dispatch_id_$dispatchId&type=video&maxResults=1"
            val req = Request.Builder()
                .url(queryUrl)
                .header("Authorization", "Bearer $accessToken")
                .get()
                .build()
            client.newCall(req).execute().use { resp ->
                if (resp.isSuccessful) {
                    val bodyStr = resp.body?.string() ?: return null
                    val json = gson.fromJson(bodyStr, JsonObject::class.java)
                    val items = json.getAsJsonArray("items")
                    if (items != null && items.size() > 0) {
                        val first = items.get(0).asJsonObject
                        val idObj = first.getAsJsonObject("id")
                        val videoId = idObj?.get("videoId")?.asString
                        if (!videoId.isNullOrBlank()) {
                            android.util.Log.i("YouTubeUploader", "Recovered existing YouTube upload for $dispatchId: $videoId")
                            return videoId
                        }
                    }
                }
            }
        } catch (e: Exception) {
            android.util.Log.w("YouTubeUploader", "Error checking existing upload for $dispatchId: ${e.message}")
        }
        return null
    }

    private suspend fun checkYouTubeProcessingStatus(accessToken: String, videoId: String, useCloud: Boolean): String {
        if (useCloud && cloudSessionActive()) {
            val json = cloudYouTubeApi("youtube/videos/status", JsonObject().apply {
                addProperty("video_id", videoId)
            })
            return json?.get("status")?.asString ?: "unknown"
        }
        return try {
            val url = "https://www.googleapis.com/youtube/v3/videos?part=processingDetails,status&id=$videoId"
            val req = Request.Builder()
                .url(url)
                .header("Authorization", "Bearer $accessToken")
                .get()
                .build()
            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return "unknown"
                val bodyStr = resp.body?.string() ?: return "unknown"
                val json = gson.fromJson(bodyStr, JsonObject::class.java)
                val items = json.getAsJsonArray("items")
                if (items == null || items.size() == 0) return "not_found"

                val item = items.get(0).asJsonObject
                val statusObj = item.getAsJsonObject("status")
                val uploadStatus = statusObj?.get("uploadStatus")?.asString ?: ""

                val processingObj = item.getAsJsonObject("processingDetails")
                val procStatus = processingObj?.get("processingStatus")?.asString ?: ""

                when {
                    procStatus == "succeeded" -> "succeeded"
                    procStatus == "processing" || uploadStatus == "uploaded" -> "processing"
                    procStatus == "failed" || uploadStatus == "rejected" || uploadStatus == "failed" -> "failed"
                    else -> procStatus.ifBlank { uploadStatus.ifBlank { "unknown" } }
                }
            }
        } catch (e: Exception) {
            android.util.Log.w("YouTubeUploader", "Could not check video $videoId processing status: ${e.message}")
            "unknown"
        }
    }

    private fun refreshYouTubeToken(): String? {
        val refreshToken = pairingManager.youtubeRefreshToken
        val clientId = pairingManager.youtubeClientId
        val clientSecret = pairingManager.youtubeClientSecret
        if (refreshToken.isBlank() || clientId.isBlank() || clientSecret.isBlank()) return null

        val formBody = FormBody.Builder()
            .add("client_id", clientId)
            .add("client_secret", clientSecret)
            .add("refresh_token", refreshToken)
            .add("grant_type", "refresh_token")
            .build()

        val req = Request.Builder()
            .url("https://oauth2.googleapis.com/token")
            .post(formBody)
            .build()

        return try {
            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return null
                val body = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                val newAccessToken = body.get("access_token")?.asString
                if (!newAccessToken.isNullOrBlank()) {
                    pairingManager.youtubeAccessToken = newAccessToken
                    newAccessToken
                } else null
            }
        } catch (_: Exception) {
            null
        }
    }

    private suspend fun recoverInterruptedSessions(context: Context) {
        try {
            val interrupted = dao.getInterruptedSessions()
            val recordingsDir = File(context.getExternalFilesDir(null) ?: context.filesDir, "recordings")

            for (sess in interrupted) {
                if (!shouldRecoverRecordingSession(sess.sessionId, RecordingForegroundService.activeSessionId)) {
                    android.util.Log.i("YouTubeUploader", "Skipping recovery for live recording session ${sess.sessionId}")
                    continue
                }

                val unfinalized = dao.getUnfinalizedSegments(sess.sessionId)
                for (seg in unfinalized) {
                    val tmp = File(recordingsDir, "${seg.segmentId}.tmp")
                    val finalMp4 = File(recordingsDir, "${seg.segmentId}.mp4")
                    val recoveryFile = findRecoveryCandidate(tmp, finalMp4)

                    if (recoveryFile != null) {
                        var hasValidVideo = false
                        var extractedDurationMs = 0L
                        val retriever = MediaMetadataRetriever()
                        try {
                            retriever.setDataSource(recoveryFile.absolutePath)
                            val hasVideo = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_HAS_VIDEO)
                            val duration = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L
                            val width = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull() ?: 0
                            val height = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull() ?: 0
                            if (hasVideo == "yes" && duration > 0L && width > 0 && height > 0) {
                                val frame = retriever.getFrameAtTime(0)
                                if (frame != null) {
                                    hasValidVideo = true
                                    extractedDurationMs = duration
                                    try { frame.recycle() } catch (_: Exception) {}
                                }
                            }
                        } catch (e: Exception) {
                            android.util.Log.w("YouTubeUploader", "Corrupt .tmp segment ${seg.segmentId}: ${e.message}")
                            hasValidVideo = false
                        } finally {
                            try { retriever.release() } catch (_: Exception) {}
                        }

                        if (hasValidVideo) {
                            val size = recoveryFile.length()
                            val sha = calculateSha256(recoveryFile)
                            if (recoveryFile != finalMp4 && !recoveryFile.renameTo(finalMp4)) {
                                recoveryFile.copyTo(finalMp4, overwrite = true)
                                recoveryFile.delete()
                            }
                            val accurateFinalizedAt = if (extractedDurationMs > 0L) {
                                seg.createdAt + extractedDurationMs
                            } else {
                                System.currentTimeMillis()
                            }
                            db.withTransaction {
                                dao.updateSegment(
                                    seg.copy(
                                        filepath = finalMp4.absolutePath,
                                        fileSizeBytes = size,
                                        sha256Hash = sha,
                                        status = "QUEUED_FOR_UPLOAD",
                                        finalizedAt = accurateFinalizedAt
                                    )
                                )
                                dao.insertOutbox(
                                    com.resolvia.dispatch.data.OutboxEntity(
                                        segmentId = seg.segmentId,
                                        sessionId = sess.sessionId,
                                        status = "QUEUED_FOR_UPLOAD"
                                    )
                                )
                            }
                            android.util.Log.i("YouTubeUploader", "Recovered crash segment ${seg.segmentId} as valid mp4.")
                        } else {
                            android.util.Log.w("YouTubeUploader", "Preserving unplayable crash segment ${seg.segmentId} for recovery")
                            // Non-empty partial media is evidence; keep it available for repair/export.
                            dao.updateSegmentStatus(seg.segmentId, "FAILED_CORRUPT")
                        }
                    } else if (tmp.exists() && tmp.length() == 0L) {
                        try { tmp.delete() } catch (_: Exception) {}
                        dao.updateSegmentStatus(seg.segmentId, "FAILED_EMPTY")
                    } else if (!tmp.exists()) {
                        dao.updateSegmentStatus(seg.segmentId, "FAILED_MISSING_FILE")
                    }
                }
                dao.updateSession(sess.copy(status = "CRASH_RECOVERED"))
            }
        } catch (e: Exception) {
            android.util.Log.e("YouTubeUploader", "Session recovery error: ${e.message}", e)
        }
    }

    private fun calculateSha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { fis ->
            val buf = ByteArray(65536)
            var n: Int
            while (fis.read(buf).also { n = it } != -1) {
                digest.update(buf, 0, n)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun verifyHmacReceipt(
        secret: String,
        canonicalId: String,
        sha256: String,
        status: String,
        receivedReceipt: String
    ): Boolean {
        if (secret.isBlank() || receivedReceipt.isBlank() || sha256.isBlank()) return false
        return try {
            val mac = Mac.getInstance("HmacSHA256")
            val keySpec = SecretKeySpec(secret.toByteArray(Charsets.UTF_8), "HmacSHA256")
            mac.init(keySpec)
            val msg = "$canonicalId:${sha256.lowercase()}:$status:VERIFIED".toByteArray(Charsets.UTF_8)
            val expectedBytes = mac.doFinal(msg)
            val expectedHex = expectedBytes.joinToString("") { "%02x".format(it) }
            MessageDigest.isEqual(
                expectedHex.toByteArray(Charsets.UTF_8),
                receivedReceipt.trim().lowercase().toByteArray(Charsets.UTF_8)
            )
        } catch (e: Exception) {
            android.util.Log.e("YouTubeUploader", "HMAC verification exception: ${e.message}")
            false
        }
    }

    private suspend fun checkPcAcknowledgement(
        pcHost: String,
        videoId: String?,
        dispatchId: String?,
        segmentId: String,
        sha256Hash: String = "",
        fileSizeBytes: Long = 0L
    ): Boolean = withContext(Dispatchers.IO) {
        if (pcHost.isBlank()) return@withContext false
        val token = pairingManager.pcAuthToken
        if (token.isBlank()) {
            android.util.Log.w("YouTubeUploader", "Cannot check PC acknowledgement: pcAuthToken is blank")
            return@withContext false
        }
        try {
            val cleanHost = pcHost.trim().removePrefix("http://").removePrefix("https://").trimEnd('/')
            val queryParams = mutableListOf<String>()
            if (!videoId.isNullOrBlank()) queryParams.add("video_id=$videoId")
            if (!dispatchId.isNullOrBlank()) queryParams.add("dispatch_id=$dispatchId")
            queryParams.add("segment_id=$segmentId")
            if (sha256Hash.isNotBlank()) queryParams.add("sha256=$sha256Hash")
            if (fileSizeBytes > 0L) queryParams.add("file_size=$fileSizeBytes")

            val url = "http://$cleanHost/api/sync/youtube-ack?${queryParams.joinToString("&")}"

            val reqBuilder = Request.Builder().url(url).get()
            reqBuilder.header("x-auth-token", token)

            client.newCall(reqBuilder.build()).execute().use { resp ->
                if (!resp.isSuccessful) {
                    android.util.Log.w("YouTubeUploader", "PC ack returned HTTP ${resp.code} for $segmentId")
                    return@withContext false
                }
                val bodyStr = resp.body?.string() ?: return@withContext false
                val json = gson.fromJson(bodyStr, JsonObject::class.java)
                val acknowledged = json.get("acknowledged")?.asBoolean == true
                val ingested = json.get("ingested")?.asBoolean == true
                if (!acknowledged || !ingested) return@withContext false

                // Cryptographic Receipt & Hash Validation
                val returnedSha = json.get("sha256")?.asString?.trim()?.lowercase() ?: ""
                if (sha256Hash.isNotBlank()) {
                    if (returnedSha.isBlank() || returnedSha != sha256Hash.trim().lowercase()) {
                        android.util.Log.e("YouTubeUploader", "PC ack hash mismatch for $segmentId: expected $sha256Hash, got $returnedSha")
                        return@withContext false
                    }
                }

                val receipt = json.get("receipt")?.asString ?: ""
                val canonicalId = json.get("canonical_id")?.asString
                    ?: json.get("segment_id")?.asString
                    ?: json.get("dispatch_id")?.asString
                    ?: json.get("video_id")?.asString
                    ?: segmentId
                val status = json.get("status")?.asString ?: "DOWNLOADED"

                val isReceiptValid = verifyHmacReceipt(token, canonicalId, returnedSha.ifBlank { sha256Hash }, status, receipt)
                if (!isReceiptValid) {
                    android.util.Log.e("YouTubeUploader", "PC ack signature verification FAILED for $segmentId! Refusing to acknowledge.")
                    return@withContext false
                }

                android.util.Log.i("YouTubeUploader", "PC ack cryptographically verified for $segmentId (receipt: ${receipt.take(8)}...)")
                true
            }
        } catch (e: Exception) {
            android.util.Log.w("YouTubeUploader", "Failed to verify PC acknowledgement from $pcHost: ${e.message}")
            false
        }
    }

    private suspend fun reconcilePendingPcAcknowledgements() {
        val pcHost = pairingManager.pcHost
        if (pcHost.isBlank()) return

        try {
            val awaiting = dao.getSegmentsAwaitingPcAck()
            if (awaiting.isEmpty()) return

            for (seg in awaiting) {
                val videoId = seg.youtubeVideoId
                val dispatchId = "dsp_${seg.finalizedAt ?: seg.createdAt}_${seg.segmentId.takeLast(6)}"
                val f = File(seg.filepath)
                val sha = if (seg.sha256Hash.isNotBlank()) seg.sha256Hash else (if (f.exists()) calculateSha256(f) else "")
                val fSize = if (seg.fileSizeBytes > 0L) seg.fileSizeBytes else (if (f.exists()) f.length() else 0L)
                val pcAck = checkPcAcknowledgement(pcHost, videoId, dispatchId, seg.segmentId, sha, fSize)
                if (pcAck) {
                    dao.updateSegmentStatus(seg.segmentId, "VERIFIED_BY_LAPTOP")
                    dao.deleteOutboxItem(seg.segmentId)
                    if (f.exists()) {
                        try { f.delete() } catch (_: Exception) {}
                    }
                    android.util.Log.i("YouTubeUploader", "Segment ${seg.segmentId} verified by PC. Local footage safely removed.")
                }
            }
        } catch (e: Exception) {
            android.util.Log.w("YouTubeUploader", "Error during PC acknowledgement reconciliation: ${e.message}")
        }
    }

    /**
     * Stranded-Status fix: segments that reached UPLOADED_AWAITING_PROCESSING
     * previously stayed there forever because the outbox row was deleted right
     * after upload and YouTube was only polled once. This pass re-checks the
     * video's processingDetails/status on every worker run until YouTube
     * returns a definitive verdict, and requires an explicit FAILED state for
     * processing failures instead of a stale "Queued" badge.
     */
    private suspend fun reconcileYouTubeProcessingStatuses(): Boolean {
        val awaiting = dao.getSegmentsUploadedAwaitingProcessing()
        if (awaiting.isEmpty()) return false

        for (seg in awaiting) {
            // Origin-aware credential selection; a legacy upload keeps using the
            // locally-stored OAuth from the PC pairing flow. Cloud-origin is
            // hard-pinned: the verdict must come from the vault account that
            // owns the video (resolveReconcileMode), never from whichever
            // credentials happen to be present at poll time.
            val useCloud = UploadModePolicy.resolveReconcileMode(seg.uploadOrigin)
            if (!useCloud && !pairingManager.isYouTubeConfigured) continue

            val now = System.currentTimeMillis()
            // Throttle: never hit the YouTube API twice for one segment within
            // the recheck window, no matter how often the worker is triggered.
            if (seg.youtubeCheckAt != 0L && now - seg.youtubeCheckAt < processingRecheckIntervalMs) continue

            val attempts = seg.youtubeCheckAttempts + 1
            if (attempts > maxProcessingChecks) {
                android.util.Log.e(
                    "YouTubeUploader",
                    "Segment ${seg.segmentId}: YouTube processing unresolved after $maxProcessingChecks checks; failing explicitly."
                )
                dao.updateSegmentStatus(seg.segmentId, "FAILED_YOUTUBE_PROCESSING")
                continue
            }
            dao.updateProcessingCheck(seg.segmentId, attempts, now)

            when (val verdict = checkYouTubeProcessingStatus("", seg.youtubeVideoId ?: "", useCloud)) {
                "succeeded" -> dao.updateSegmentStatus(seg.segmentId, "PROCESSED_ON_YOUTUBE")
                "failed" -> {
                    android.util.Log.e("YouTubeUploader", "Segment ${seg.segmentId} video ${seg.youtubeVideoId} failed processing on YouTube.")
                    dao.updateSegmentStatus(seg.segmentId, "FAILED_YOUTUBE_PROCESSING")
                }
                else -> {
                    // Not a definitive verdict; this segment stays in
                    // UPLOADED_AWAITING_PROCESSING and the recheck continues.
                    android.util.Log.i("YouTubeUploader", "Segment ${seg.segmentId} processing verdict: $verdict (${maxProcessingChecks - attempts} checks left)")
                }
            }
        }
        return dao.getSegmentsUploadedAwaitingProcessing().isNotEmpty()
    }

    /**
     * Parses and strictly validates YouTube Resumable Upload Range header.
     * Expected format: "bytes=0-<end>" where 0 <= end < totalBytes.
     * Returns (end + 1L) as the next byte offset to send, or null if missing, invalid, or out of bounds.
     */
    private fun parseConfirmedRangeOffset(rangeHeader: String?, totalBytes: Long): Long? {
        if (rangeHeader.isNullOrBlank()) return null
        val match = Regex("""bytes=(\d+)-(\d+)""").find(rangeHeader.trim()) ?: return null
        val (startStr, endStr) = match.destructured
        val start = startStr.toLongOrNull() ?: return null
        val end = endStr.toLongOrNull() ?: return null

        if (start != 0L) {
            android.util.Log.w("YouTubeUploader", "Invalid Range start $start in '$rangeHeader'")
            return null
        }
        if (end < 0L || (totalBytes > 0L && end >= totalBytes)) {
            android.util.Log.w("YouTubeUploader", "Range end $end out of bounds for total $totalBytes")
            return null
        }
        return end + 1L
    }

    /**
     * Probes upload session status via Content-Range: bytes * / totalBytes.
     * Handles 401 with refreshed access token.
     * Returns:
     * - next confirmed byte offset (>= 0L)
     * - -1L if session is permanently expired (404 / 410)
     * - null if network or ambiguous error occurred
     */
    private fun queryUploadStatus(uploadUrl: String, accessToken: String, totalBytes: Long): Long? {
        var token = accessToken
        val hasToken = token.isNotBlank()
        for (attempt in 0..1) {
            val queryReqBuilder = Request.Builder()
                .url(uploadUrl)
                .header("Content-Range", "bytes */$totalBytes")
                .header("Content-Length", "0")
            // Cloud vault sessions are authorized by the worker-minted URL itself.
            if (hasToken) queryReqBuilder.header("Authorization", "Bearer $token")
            val queryReq = queryReqBuilder
                .put("".toRequestBody("video/mp4".toMediaType()))
                .build()

            try {
                val resp = client.newCall(queryReq).execute()
                resp.use {
                    if (it.code == 401 && hasToken && attempt == 0) {
                        val freshToken = refreshYouTubeToken()
                        if (freshToken != null) {
                            token = freshToken
                            return@use // retry with refreshed token in next iteration
                        }
                    }
                    if (it.code == 308) {
                        val rangeHeader = it.header("Range")
                        return if (rangeHeader.isNullOrBlank()) {
                            0L
                        } else {
                            parseConfirmedRangeOffset(rangeHeader, totalBytes)
                        }
                    } else if (it.isSuccessful) {
                        return totalBytes
                    } else if (it.code == 404 || it.code == 410 || (it.code == 401 && !hasToken)) {
                        // For cloud sessions a 401 marks an invalidated URL: treat
                        // as expired so the caller re-opens a session via the worker.
                        return -1L
                    } else {
                        return null
                    }
                }
            } catch (e: Exception) {
                return null
            }
        }
        return null
    }
}


package com.resolvia.dispatch.sync

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.PairingManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.RandomAccessFile
import java.text.SimpleDateFormat
import java.util.*
import java.util.concurrent.TimeUnit

/**
 * Autonomous Direct YouTube Cloud Uploader for Dispatch Mobile.
 * Uploads finalized video recordings directly to YouTube Data API v3 as Unlisted,
 * using chunked resumable PUT (8 MB chunks) with byte offset tracking in SQLite Room.
 * If network drops, queries Content-Range: bytes * /total and resumes without repeating data.
 */
data class DirectUploadResult(
    val success: Boolean,
    val youtubeVideoId: String? = null
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

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        var hasFailures = false
        val failedInThisRun = mutableSetOf<String>()

        // Reliable drain loop: processes all pending segments including ones added during upload
        while (true) {
            val pendingSegments = dao.getPendingUploadSegments().filter { it.segmentId !in failedInThisRun }
            if (pendingSegments.isEmpty()) break

            // 1. Check if direct YouTube OAuth credentials exist
            var accessToken = pairingManager.youtubeAccessToken
            if (accessToken.isBlank() && pairingManager.youtubeRefreshToken.isNotBlank()) {
                accessToken = refreshYouTubeToken() ?: ""
            }

            if (accessToken.isBlank()) {
                android.util.Log.w("YouTubeUploader", "YouTube credentials missing; marking segments waiting")
                for (seg in pendingSegments) {
                    dao.updateSegmentStatus(seg.segmentId, "WAITING_FOR_YOUTUBE")
                }
                return@withContext Result.failure()
            }

            // 2. Iterate and upload recordings directly to YouTube (Non-blocking batch)
            for (seg in pendingSegments) {
                // If already uploaded or already has YouTube ID, clean outbox and skip
                if (seg.status == "UPLOADED_TO_YOUTUBE" || !seg.youtubeVideoId.isNullOrBlank()) {
                    dao.deleteOutboxItem(seg.segmentId)
                    continue
                }

                val file = File(seg.filepath)
                if (!file.exists() || file.length() == 0L) {
                    android.util.Log.w("YouTubeUploader", "Skipping missing or empty file for ${seg.segmentId}")
                    dao.updateSegmentStatus(seg.segmentId, "FAILED_MISSING_FILE")
                    continue
                }

                val dispatchId = "dsp_${seg.finalizedAt ?: System.currentTimeMillis()}_${seg.segmentId.takeLast(6)}"
                val result = uploadToYouTubeDirect(accessToken, file, dispatchId, seg)

                if (result.success) {
                    val updated = seg.copy(
                        status = "UPLOADED_TO_YOUTUBE",
                        youtubeVideoId = result.youtubeVideoId
                    )
                    dao.updateSegment(updated)
                    dao.deleteOutboxItem(seg.segmentId)
                } else {
                    hasFailures = true
                    failedInThisRun.add(seg.segmentId)
                    dao.updateSegmentStatus(seg.segmentId, "FAILED_RETRY")
                    // Do NOT abort entire loop; continue with remaining segments
                    continue
                }
            }
        }

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
        seg: com.resolvia.dispatch.data.SegmentEntity
    ): DirectUploadResult {
        val segId = seg.segmentId
        val totalBytes = file.length()
        if (totalBytes == 0L) return DirectUploadResult(false)

        // 0. Idempotency protection: check if video was already published to YouTube during previous attempt
        val existingVideoId = findExistingUploadByDispatchId(accessToken, dispatchId)
        if (!existingVideoId.isNullOrBlank()) {
            android.util.Log.i("YouTubeUploader", "Upload already confirmed on YouTube for $dispatchId: $existingVideoId")
            return DirectUploadResult(success = true, youtubeVideoId = existingVideoId)
        }

        val timestampStr = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(Date())
        val title = "[DISPATCH] Session ${seg.sessionId.takeLast(6)} - Part ${seg.sequenceNumber} ($timestampStr)"
        val description = "dispatch_id: $dispatchId\nsession_id: ${seg.sessionId}\nsegment_id: ${seg.segmentId}\nsequence_number: ${seg.sequenceNumber}\nAutonomous mobile recording uploaded by Dispatch."

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
            .header("Authorization", "Bearer $accessToken")
            .header("X-Upload-Content-Type", "video/mp4")
            .header("X-Upload-Content-Length", totalBytes.toString())
            .post(metadataJson.toString().toRequestBody("application/json; charset=UTF-8".toMediaType()))
            .build()

        var uploadUrl: String? = null
        try {
            client.newCall(initReq).execute().use { resp ->
                if (resp.code == 401) {
                    val freshToken = refreshYouTubeToken()
                    if (freshToken != null) {
                        return uploadToYouTubeDirect(freshToken, file, dispatchId, seg)
                    }
                    return DirectUploadResult(false)
                }
                if (!resp.isSuccessful) {
                    android.util.Log.e("YouTubeUploader", "Init failed: HTTP ${resp.code}")
                    return DirectUploadResult(false)
                }
                uploadUrl = resp.header("Location")
            }
        } catch (e: Exception) {
            android.util.Log.e("YouTubeUploader", "Failed to initiate resumable upload session: ${e.message}")
            return DirectUploadResult(false)
        }

        val destination = uploadUrl ?: return DirectUploadResult(false)

        // 2. Chunked Resumable Upload (8 MB chunks) with persistent byte progress
        val chunkSize = 8 * 1024 * 1024L // 8MB chunks
        var currentOffset = dao.getOutboxOffset(segId) ?: 0L

        // Query session status if resuming after disconnect
        if (currentOffset > 0L) {
            val queryReq = Request.Builder()
                .url(destination)
                .header("Content-Range", "bytes */$totalBytes")
                .header("Content-Length", "0")
                .put("".toRequestBody("video/mp4".toMediaType()))
                .build()

            try {
                client.newCall(queryReq).execute().use { resp ->
                    if (resp.code == 308) {
                        val rangeHeader = resp.header("Range")
                        if (!rangeHeader.isNullOrBlank()) {
                            val lastByte = rangeHeader.substringAfter("-").toLongOrNull()
                            if (lastByte != null) {
                                currentOffset = lastByte + 1L
                            }
                        }
                    } else if (resp.isSuccessful) {
                        currentOffset = totalBytes
                    }
                }
            } catch (e: Exception) {
                android.util.Log.w("YouTubeUploader", "Offset query failed, falling back to local: $currentOffset")
            }
        }

        // Upload in 8MB slices
        var raf: RandomAccessFile? = null
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
                val chunkReq = Request.Builder()
                    .url(destination)
                    .header("Authorization", "Bearer $accessToken")
                    .header("Content-Length", chunkLen.toString())
                    .header("Content-Range", "bytes $start-$end/$totalBytes")
                    .put(chunkBody)
                    .build()

                var retryWithNewToken = false
                client.newCall(chunkReq).execute().use { resp ->
                    when (resp.code) {
                        308 -> {
                            currentOffset = end + 1L
                            dao.updateOutboxOffset(segId, currentOffset)
                            android.util.Log.i("YouTubeUploader", "Chunk uploaded for $segId: $start-$end ($currentOffset/$totalBytes)")
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
                            } ?: findExistingUploadByDispatchId(accessToken, dispatchId)
                            android.util.Log.i("YouTubeUploader", "Upload 100% complete for $segId (Video ID: $videoId)")
                            return DirectUploadResult(success = true, youtubeVideoId = videoId)
                        }
                        401 -> {
                            val freshToken = refreshYouTubeToken()
                            if (freshToken != null) {
                                retryWithNewToken = true
                            } else {
                                return DirectUploadResult(false)
                            }
                        }
                        else -> {
                            android.util.Log.e("YouTubeUploader", "Chunk error: HTTP ${resp.code} ${resp.message}")
                            return DirectUploadResult(false)
                        }
                    }
                }

                if (retryWithNewToken) {
                    continue
                }
            }
        } catch (e: Exception) {
            android.util.Log.e("YouTubeUploader", "Exception during chunked upload: ${e.message}", e)
            return DirectUploadResult(false)
        } finally {
            try { raf?.close() } catch (_: Exception) {}
        }

        val finalCheckId = if (currentOffset >= totalBytes) findExistingUploadByDispatchId(accessToken, dispatchId) else null
        return DirectUploadResult(
            success = currentOffset >= totalBytes,
            youtubeVideoId = finalCheckId
        )
    }

    private suspend fun findExistingUploadByDispatchId(accessToken: String, dispatchId: String): String? {
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
}

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
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.text.SimpleDateFormat
import java.util.*
import java.util.concurrent.TimeUnit

/**
 * Autonomous Direct YouTube Cloud Uploader for Dispatch Mobile.
 * Uploads finalized video recordings directly to YouTube Data API v3 as Private,
 * tagging them with unique dispatch_id metadata for zero-touch laptop catching.
 * If YouTube credentials are not yet configured, cleanly delegates to ResumableSyncWorker.
 */
class YouTubeDirectUploadWorker(
    appContext: Context,
    private val params: WorkerParameters
) : CoroutineWorker(appContext, params) {

    private val db = AppDatabase.getDatabase(appContext)
    private val dao = db.recordingDao()
    private val pairingManager = PairingManager(appContext)
    private val gson = Gson()
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val pendingSegments = dao.getPendingUploadSegments()
        if (pendingSegments.isEmpty()) return@withContext Result.success()

        // 1. Check if direct YouTube OAuth credentials exist
        var accessToken = pairingManager.youtubeAccessToken
        if (accessToken.isBlank() && pairingManager.youtubeRefreshToken.isNotBlank()) {
            accessToken = refreshYouTubeToken() ?: ""
        }

        // If no YouTube token configured on phone yet, fallback to local network/Tailscale sync worker
        if (accessToken.isBlank()) {
            val fallbackWorker = ResumableSyncWorker(applicationContext, params)
            return@withContext fallbackWorker.doWork()
        }

        // 2. Iterate and upload recordings directly to YouTube
        for (seg in pendingSegments) {
            val file = File(seg.filepath)
            if (!file.exists() || file.length() == 0L) {
                continue
            }

            val dispatchId = "dsp_${seg.finalizedAt}_${seg.segmentId.takeLast(6)}"
            val success = uploadToYouTubeDirect(accessToken, file, dispatchId)

            if (success) {
                // Update local Room database
                val updated = seg.copy(status = "UPLOADED_TO_YOUTUBE")
                dao.updateSegment(updated)
                dao.deleteOutboxItem(seg.segmentId)
            } else {
                return@withContext Result.retry()
            }
        }

        Result.success()
    }

    private fun uploadToYouTubeDirect(accessToken: String, file: File, dispatchId: String): Boolean {
        val timestampStr = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(Date())
        val title = "[DISPATCH] $timestampStr"
        val description = "dispatch_id: $dispatchId\nAutonomous mobile recording uploaded by Dispatch."

        // 1. Resumable Upload Initiation Request
        val metadataJson = JsonObject().apply {
            val snippet = JsonObject().apply {
                addProperty("title", title)
                addProperty("description", description)
                val tags = com.google.gson.JsonArray().apply {
                    add("dispatch")
                    add("dispatch_id_$dispatchId")
                }
                add("tags", tags)
                addProperty("categoryId", "22")
            }
            val status = JsonObject().apply {
                addProperty("privacyStatus", "private")
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
            .header("X-Upload-Content-Length", file.length().toString())
            .post(metadataJson.toString().toRequestBody("application/json; charset=UTF-8".toMediaType()))
            .build()

        var uploadUrl: String? = null
        try {
            client.newCall(initReq).execute().use { resp ->
                if (!resp.isSuccessful) return false
                uploadUrl = resp.header("Location")
            }
        } catch (_: Exception) {
            return false
        }

        val destination = uploadUrl ?: return false

        // 2. Stream Video Content (PUT)
        val videoBody = file.asRequestBody("video/mp4".toMediaType())
        val uploadReq = Request.Builder()
            .url(destination)
            .header("Authorization", "Bearer $accessToken")
            .header("Content-Length", file.length().toString())
            .put(videoBody)
            .build()

        try {
            client.newCall(uploadReq).execute().use { resp ->
                return resp.isSuccessful
            }
        } catch (_: Exception) {
            return false
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
}

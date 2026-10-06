package com.resolvia.dispatch.sync

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.resolvia.dispatch.data.AppDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.RandomAccessFile

class ResumableSyncWorker(
    appContext: Context,
    params: WorkerParameters
) : CoroutineWorker(appContext, params) {

    private val db = AppDatabase.getDatabase(appContext)
    private val dao = db.recordingDao()
    private val client = OkHttpClient()
    private val gson = Gson()
    private val pairingManager = com.resolvia.dispatch.data.PairingManager(appContext)

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val activeUrl = findReachableServer() ?: return@withContext Result.retry()
        if (pairingManager.authToken.isBlank()) {
            try {
                val configReq = Request.Builder().url("$activeUrl/api/sync/pairing/config").build()
                client.newCall(configReq).execute().use { resp ->
                    if (resp.isSuccessful) {
                        val body = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                        val token = body.get("auth_token")?.asString ?: ""
                        if (token.isNotBlank()) {
                            pairingManager.authToken = token
                        }
                    }
                }
            } catch (_: Exception) {}
        }
        val pendingItems = dao.getPendingOutboxItems()

        if (pendingItems.isEmpty()) return@withContext Result.success()

        for (item in pendingItems) {
            val file = File(applicationContext.getExternalFilesDir(null), "recordings/${item.segmentId}.mp4")
            if (!file.exists()) {
                dao.deleteOutboxItem(item.segmentId)
                continue
            }

            val success = uploadSegmentResumable(activeUrl, item.segmentId, item.sessionId, file)
            if (!success) {
                return@withContext Result.retry()
            }
        }

        Result.success()
    }

    private fun findReachableServer(): String? {
        val endpoints = pairingManager.getCandidateEndpoints()
        for (url in endpoints) {
            val req = Request.Builder()
                .url("$url/api/sync/ping")
                .header("X-Dispatch-Device-Token", pairingManager.authToken)
                .build()
            try {
                client.newCall(req).execute().use { resp ->
                    if (resp.isSuccessful) return url
                }
            } catch (_: Exception) {}
        }
        return null
    }

    private suspend fun uploadSegmentResumable(baseUrl: String, segmentId: String, sessionId: String, file: File): Boolean {
        val totalBytes = file.length()
        val sha256 = calculateSha256(file)

        // 1. Init upload & query confirmed remote offset
        val initJson = JsonObject().apply {
            addProperty("session_id", sessionId)
            addProperty("segment_id", segmentId)
            addProperty("filename", file.name)
            addProperty("file_size_bytes", totalBytes)
            addProperty("sha256_hash", sha256)
            addProperty("auth_token", pairingManager.authToken)
        }

        val initReq = Request.Builder()
            .url("$baseUrl/api/sync/upload/init")
            .post(initJson.toString().toRequestBody("application/json".toMediaType()))
            .build()

        var remoteOffset = 0L
        try {
            client.newCall(initReq).execute().use { resp ->
                if (!resp.isSuccessful) return false
                val body = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                if (body.get("status")?.asString == "already_completed") {
                    cleanupVerifiedFile(baseUrl, segmentId, file, sha256, totalBytes)
                    return true
                }
                remoteOffset = body.get("remote_offset")?.asLong ?: 0L
            }
        } catch (e: Exception) {
            return false
        }

        dao.updateOutboxOffset(segmentId, remoteOffset)

        // 2. Stream chunked bytes starting strictly from remoteOffset
        val chunkSize = 256 * 1024 // 256 KB slices
        val buffer = ByteArray(chunkSize)
        var bytesSinceSave = 0L

        RandomAccessFile(file, "r").use { raf ->
            raf.seek(remoteOffset)

            while (remoteOffset < totalBytes) {
                val bytesToRead = minOf(chunkSize.toLong(), totalBytes - remoteOffset).toInt()
                raf.readFully(buffer, 0, bytesToRead)

                val chunkReq = Request.Builder()
                    .url("$baseUrl/api/sync/upload/chunk")
                    .patch(buffer.copyOf(bytesToRead).toRequestBody("application/octet-stream".toMediaType()))
                    .addHeader("x-segment-id", segmentId)
                    .addHeader("x-upload-offset", remoteOffset.toString())
                    .addHeader("x-file-size", totalBytes.toString())
                    .addHeader("x-sha256", sha256)
                    .addHeader("x-session-id", sessionId)
                    .addHeader("x-auth-token", pairingManager.authToken)
                    .build()

                try {
                    client.newCall(chunkReq).execute().use { chunkResp ->
                        if (!chunkResp.isSuccessful) return false
                        val chunkBody = gson.fromJson(chunkResp.body?.string(), JsonObject::class.java)
                        remoteOffset = chunkBody.get("remote_offset")?.asLong ?: (remoteOffset + bytesToRead)

                        bytesSinceSave += bytesToRead
                        if (bytesSinceSave >= 2 * 1024 * 1024) {
                            dao.updateOutboxOffset(segmentId, remoteOffset)
                            bytesSinceSave = 0L
                        }

                        if (chunkBody.get("status")?.asString == "completed" && chunkBody.get("verified")?.asBoolean == true) {
                            cleanupVerifiedFile(baseUrl, segmentId, file, sha256, totalBytes)
                            return true
                        }
                    }
                } catch (e: Exception) {
                    return false
                }
            }
        }

        return true
    }

    private suspend fun cleanupVerifiedFile(baseUrl: String, segmentId: String, file: File, sha256: String, totalBytes: Long) {
        val verified = verifyWithServer(baseUrl, segmentId, sha256, totalBytes)
        if (verified) {
            file.delete()
            dao.updateSegmentStatus(segmentId, "UPLOADED_TO_PC")
            dao.deleteOutboxItem(segmentId)
            android.util.Log.i("ResumableSyncWorker", "Segment $segmentId verified by server and deleted locally.")
        } else {
            android.util.Log.w("ResumableSyncWorker", "Server verification check failed for $segmentId. Retaining local file.")
        }
    }

    private fun verifyWithServer(baseUrl: String, segmentId: String, sha256: String, fileSize: Long): Boolean {
        val url = "$baseUrl/api/sync/verify-chunk?segment_id=$segmentId&sha256=$sha256&file_size=$fileSize"
        val req = Request.Builder()
            .url(url)
            .addHeader("x-auth-token", pairingManager.authToken)
            .get()
            .build()
        return try {
            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return false
                val body = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                body?.get("verified")?.asBoolean == true
            }
        } catch (e: Exception) {
            false
        }
    }

    private fun calculateSha256(file: File): String {
        val digest = java.security.MessageDigest.getInstance("SHA-256")
        java.io.FileInputStream(file).use { fis ->
            val buf = ByteArray(65536)
            var n: Int
            while (fis.read(buf).also { n = it } != -1) {
                digest.update(buf, 0, n)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}

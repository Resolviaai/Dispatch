package com.resolvia.dispatch.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.work.*
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.sync.YouTubeDirectUploadWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.io.File
import java.io.FileInputStream
import java.security.MessageDigest

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            val pendingResult = goAsync()
            CoroutineScope(Dispatchers.IO).launch {
                try {
                    recoverInterruptedSessions(context)
                    scheduleYouTubeUpload(context)
                } finally {
                    pendingResult.finish()
                }
            }
        }
    }

    private suspend fun recoverInterruptedSessions(context: Context) {
        val db = AppDatabase.getDatabase(context)
        val dao = db.recordingDao()
        val interrupted = dao.getInterruptedSessions()
        val recordingsDir = File(context.getExternalFilesDir(null), "recordings")

        for (sess in interrupted) {
            val unfinalized = dao.getUnfinalizedSegments(sess.sessionId)
            for (seg in unfinalized) {
                val tmp = File(recordingsDir, "${seg.segmentId}.tmp")
                val finalMp4 = File(recordingsDir, "${seg.segmentId}.mp4")

                if (tmp.exists() && tmp.length() > 0L) {
                    val size = tmp.length()
                    val sha = calculateSha256(tmp)
                    tmp.renameTo(finalMp4)

                    dao.updateSegment(
                        seg.copy(
                            fileSizeBytes = size,
                            sha256Hash = sha,
                            status = "QUEUED_FOR_UPLOAD",
                            finalizedAt = System.currentTimeMillis()
                        )
                    )
                    dao.insertOutbox(
                        com.resolvia.dispatch.data.OutboxEntity(
                            segmentId = seg.segmentId,
                            sessionId = sess.sessionId,
                            status = "QUEUED_FOR_UPLOAD"
                        )
                    )
                } else if (tmp.exists() && tmp.length() == 0L) {
                    tmp.delete()
                }
            }

            dao.updateSession(sess.copy(status = "CRASH_RECOVERED"))
        }
    }

    private fun scheduleYouTubeUpload(context: Context) {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        val uploadReq = OneTimeWorkRequestBuilder<YouTubeDirectUploadWorker>()
            .setConstraints(constraints)
            .build()

        WorkManager.getInstance(context).enqueueUniqueWork(
            "dispatch_youtube_uploads_after_boot",
            ExistingWorkPolicy.REPLACE,
            uploadReq
        )
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
}

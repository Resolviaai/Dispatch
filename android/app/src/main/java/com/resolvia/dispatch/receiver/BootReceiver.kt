package com.resolvia.dispatch.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.work.*
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.renderer.RenderForegroundService
import com.resolvia.dispatch.sync.YouTubeDirectUploadWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.util.concurrent.TimeUnit

/**
 * Lightweight BroadcastReceiver for BOOT_COMPLETED.
 * Enqueues recovery and upload work into WorkManager immediately to prevent 10s ANR,
 * and resumes any interrupted video rendering/upload jobs.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            val pairing = PairingManager(context)
            val networkType = if (pairing.isWifiOnlyEnabled) NetworkType.UNMETERED else NetworkType.CONNECTED
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(networkType)
                .build()

            val uploadReq = OneTimeWorkRequestBuilder<YouTubeDirectUploadWorker>()
                .setConstraints(constraints)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()

            WorkManager.getInstance(context).enqueueUniqueWork(
                "dispatch_youtube_uploads",
                ExistingWorkPolicy.REPLACE,
                uploadReq
            )

            val pendingWork = goAsync()
            CoroutineScope(Dispatchers.IO).launch {
                try {
                    val db = AppDatabase.getDatabase(context)
                    val pendingJobs = db.recordingDao().getPendingRenderJobs()
                    if (pendingJobs.isNotEmpty()) {
                        val firstJob = pendingJobs.first()
                        android.util.Log.i("BootReceiver", "Resuming pending render job ${firstJob.jobId} after reboot")
                        RenderForegroundService.start(context, firstJob.jobId, firstJob.clipId)
                    }
                } catch (e: Exception) {
                    android.util.Log.e("BootReceiver", "Failed to resume render jobs on boot: ${e.message}")
                } finally {
                    pendingWork.finish()
                }
            }
        }
    }
}

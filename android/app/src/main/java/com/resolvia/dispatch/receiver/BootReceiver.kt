package com.resolvia.dispatch.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.work.*
import com.resolvia.dispatch.data.PairingManager
import com.resolvia.dispatch.sync.YouTubeDirectUploadWorker

/**
 * Lightweight BroadcastReceiver for BOOT_COMPLETED.
 * Enqueues recovery and upload work into WorkManager immediately to prevent 10s ANR.
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
                .build()

            WorkManager.getInstance(context).enqueueUniqueWork(
                "dispatch_youtube_uploads_after_boot",
                ExistingWorkPolicy.REPLACE,
                uploadReq
            )
        }
    }
}

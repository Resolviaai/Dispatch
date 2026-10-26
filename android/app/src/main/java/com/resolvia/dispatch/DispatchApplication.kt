package com.resolvia.dispatch

import android.app.Application
import androidx.work.*
import com.resolvia.dispatch.sync.YouTubeDirectUploadWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import java.util.concurrent.TimeUnit

class DispatchApplication : Application() {
    val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onCreate() {
        super.onCreate()
        schedulePeriodicYouTubeUpload()
    }

    private fun schedulePeriodicYouTubeUpload() {
        val pairing = com.resolvia.dispatch.data.PairingManager(this)
        val networkType = if (pairing.isWifiOnlyEnabled) NetworkType.UNMETERED else NetworkType.CONNECTED
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(networkType)
            .build()

        val periodicUpload = PeriodicWorkRequestBuilder<YouTubeDirectUploadWorker>(15, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .build()

        WorkManager.getInstance(this).enqueueUniquePeriodicWork(
            "dispatch_youtube_uploads_periodic",
            ExistingPeriodicWorkPolicy.KEEP,
            periodicUpload
        )
    }
}

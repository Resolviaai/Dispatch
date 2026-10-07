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
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        val periodicUpload = PeriodicWorkRequestBuilder<YouTubeDirectUploadWorker>(15, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .build()

        WorkManager.getInstance(this).enqueueUniquePeriodicWork(
            "dispatch_youtube_uploads",
            ExistingPeriodicWorkPolicy.KEEP,
            periodicUpload
        )
    }
}

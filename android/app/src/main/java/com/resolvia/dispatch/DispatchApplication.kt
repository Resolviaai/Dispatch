package com.resolvia.dispatch

import android.app.Application
import androidx.work.WorkManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

class DispatchApplication : Application() {
    val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onCreate() {
        super.onCreate()
        // The duplicate periodic drain name raced SegmenterEngine's
        // dispatch_youtube_reconcile pulse on the same rows. Cancel any
        // instance persisted by previous installs (WorkManager state survives
        // app updates); the engine registers the single remaining periodic.
        WorkManager.getInstance(this).cancelUniqueWork("dispatch_youtube_uploads_periodic")
    }
}

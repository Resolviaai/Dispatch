package com.resolvia.dispatch

import android.app.Application
import androidx.work.WorkManager
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.renderer.RenderForegroundService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class DispatchApplication : Application() {
    val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onCreate() {
        super.onCreate()
        // The duplicate periodic drain name raced SegmenterEngine's
        // dispatch_youtube_reconcile pulse on the same rows. Cancel any
        // instance persisted by previous installs (WorkManager state survives
        // app updates); the engine registers the single remaining periodic.
        WorkManager.getInstance(this).cancelUniqueWork("dispatch_youtube_uploads_periodic")

        // Resume any pending/interrupted on-device render or cloud upload jobs
        applicationScope.launch(Dispatchers.IO) {
            try {
                val db = AppDatabase.getDatabase(this@DispatchApplication)
                val pendingJobs = db.recordingDao().getPendingRenderJobs()
                if (pendingJobs.isNotEmpty()) {
                    val firstJob = pendingJobs.first()
                    android.util.Log.i("DispatchApplication", "Resuming pending render job ${firstJob.jobId} for clip ${firstJob.clipId}")
                    RenderForegroundService.start(this@DispatchApplication, firstJob.jobId, firstJob.clipId)
                }
            } catch (e: Exception) {
                android.util.Log.e("DispatchApplication", "Failed to check pending render jobs on startup: ${e.message}")
            }
        }
    }
}

package com.resolvia.dispatch

import android.app.Application
import androidx.work.WorkManager
import com.resolvia.dispatch.data.AppDatabase
import com.resolvia.dispatch.data.PairingManager
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

        // Warm EncryptedSharedPreferences on a background thread. PairingManager's
        // constructor otherwise runs two synchronous EncryptedSharedPreferences.create
        // calls on whichever thread constructs it first (the UI thread, in
        // MainActivity.onCreate). Prewarming here on IO makes every later constructor
        // reuse the cached instance. Idempotent; race-guarded inside PairingManager.
        applicationScope.launch(Dispatchers.IO) {
            try {
                PairingManager.prewarm(this@DispatchApplication)
            } catch (e: Exception) {
                android.util.Log.e("DispatchApplication", "PairingManager prewarm failed: ${e.message}")
            }
        }

        // Resume all eligible pending/interrupted on-device render or cloud upload jobs
        applicationScope.launch(Dispatchers.IO) {
            try {
                val db = AppDatabase.getDatabase(this@DispatchApplication)
                val eligibleJobs = RenderForegroundService.loadEligiblePendingRenderJobs(db)
                if (eligibleJobs.isNotEmpty()) {
                    android.util.Log.i(
                        "DispatchApplication",
                        "Resuming ${eligibleJobs.size} pending render job(s): ${eligibleJobs.joinToString { it.jobId }}"
                    )
                    RenderForegroundService.startPendingJobs(this@DispatchApplication, eligibleJobs)
                }
            } catch (e: Exception) {
                android.util.Log.e("DispatchApplication", "Failed to check pending render jobs on startup: ${e.message}")
            }
        }
    }
}

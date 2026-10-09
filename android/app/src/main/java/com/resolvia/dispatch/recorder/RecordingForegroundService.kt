package com.resolvia.dispatch.recorder

import android.app.*
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.lifecycle.LifecycleService
import com.resolvia.dispatch.MainActivity
import kotlin.coroutines.resume

/**
 * Android Lifecycle Foreground Service holding Camera and Microphone while user records.
 * Binds CameraX VideoCapture to the SERVICE lifecycle so recording continues uninterrupted
 * when the phone screen is turned off or locked.
 */
class RecordingForegroundService : LifecycleService() {

    private var wakeLock: android.os.PowerManager.WakeLock? = null

    companion object {
        private const val CHANNEL_ID = "dispatch_recording_channel"
        private const val NOTIFICATION_ID = 1001

        @Volatile
        var instance: RecordingForegroundService? = null
            private set

        @Volatile
        var cameraManager: CameraCaptureManager? = null

        @Volatile
        private var onBoundCallback: ((Boolean) -> Unit)? = null

        fun start(context: Context) {
            start(context, null)
        }

        fun start(context: Context, manager: CameraCaptureManager? = null) {
            cameraManager = manager
            val intent = Intent(context, RecordingForegroundService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        suspend fun startAndAwaitBind(context: Context, manager: CameraCaptureManager): Boolean {
            val current = instance
            if (current != null) {
                cameraManager = manager
                return manager.bindToServiceAsync(current)
            }

            return kotlinx.coroutines.suspendCancellableCoroutine { cont ->
                onBoundCallback = { success ->
                    if (cont.isActive) cont.resume(success)
                }
                start(context, manager)
            }
        }

        fun stop(context: Context) {
            val intent = Intent(context, RecordingForegroundService::class.java)
            context.stopService(intent)
        }
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        createNotificationChannel()

        // P0 fix: acquire WakeLock with NO timeout (does not die after 1 hour)
        val powerManager = getSystemService(Context.POWER_SERVICE) as? android.os.PowerManager
        wakeLock = powerManager?.newWakeLock(
            android.os.PowerManager.PARTIAL_WAKE_LOCK,
            "Dispatch:RecordingWakeLock"
        )
        wakeLock?.acquire()

        // Bind CameraX VideoCapture to this service's lifecycle if manager available
        cameraManager?.let { mgr ->
            mgr.bindToService(this) { success ->
                onBoundCallback?.invoke(success)
                onBoundCallback = null
            }
        } ?: run {
            onBoundCallback?.invoke(false)
            onBoundCallback = null
        }
    }

    override fun onDestroy() {
        instance = null
        onBoundCallback?.invoke(false)
        onBoundCallback = null
        try {
            if (wakeLock?.isHeld == true) {
                wakeLock?.release()
            }
        } catch (_: Exception) {}
        super.onDestroy()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        val notification = createNotification()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            var serviceType = ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                serviceType = serviceType or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            }
            startForeground(NOTIFICATION_ID, notification, serviceType)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }

        return START_STICKY
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Dispatch Continuous Recording",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Keeps camera recording alive in background"
                setSound(null, null)
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager?.createNotificationChannel(channel)
        }
    }

    private fun createNotification(): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Dispatch: Recording Active")
            .setContentText("Continuous segments saving to durable outbox")
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }
}

package com.resolvia.dispatch.data

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/** Stores YouTube credentials for direct phone-to-YouTube cloud uploads. Starts empty on fresh install. */
class PairingManager(context: Context) {
    private val prefs: SharedPreferences = createEncryptedOrFallbackPrefs(context)

    companion object {
        private const val TAG = "PairingManager"
        private const val SECURE_PREFS_NAME = "dispatch_youtube_secure"
        private const val LEGACY_PREFS_NAME = "dispatch_youtube"

        private fun createEncryptedOrFallbackPrefs(context: Context): SharedPreferences {
            val legacyPrefs = context.getSharedPreferences(LEGACY_PREFS_NAME, Context.MODE_PRIVATE)
            return try {
                val masterKey = MasterKey.Builder(context)
                    .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                    .build()

                val securePrefs = EncryptedSharedPreferences.create(
                    context,
                    SECURE_PREFS_NAME,
                    masterKey,
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
                )

                // Migrate legacy plaintext keys if present
                val legacyAll = legacyPrefs.all
                if (legacyAll.isNotEmpty()) {
                    val editor = securePrefs.edit()
                    for ((k, v) in legacyAll) {
                        when (v) {
                            is String -> editor.putString(k, v)
                            is Boolean -> editor.putBoolean(k, v)
                            is Int -> editor.putInt(k, v)
                            is Long -> editor.putLong(k, v)
                            is Float -> editor.putFloat(k, v)
                        }
                    }
                    editor.apply()
                    legacyPrefs.edit().clear().apply()
                    Log.i(TAG, "Migrated legacy plaintext credentials to EncryptedSharedPreferences.")
                }
                securePrefs
            } catch (e: Throwable) {
                Log.w(TAG, "EncryptedSharedPreferences initialization failed (${e.message}). Falling back to standard SharedPreferences.")
                legacyPrefs
            }
        }
    }

    var youtubeAccessToken: String
        get() = prefs.getString("access_token", "") ?: ""
        set(value) = prefs.edit().putString("access_token", value).apply()

    var youtubeRefreshToken: String
        get() = prefs.getString("refresh_token", "") ?: ""
        set(value) = prefs.edit().putString("refresh_token", value).apply()

    var youtubeClientId: String
        get() = prefs.getString("client_id", "") ?: ""
        set(value) = prefs.edit().putString("client_id", value).apply()

    var youtubeClientSecret: String
        get() = prefs.getString("client_secret", "") ?: ""
        set(value) = prefs.edit().putString("client_secret", value).apply()

    val isYouTubeConfigured: Boolean
        get() = youtubeRefreshToken.isNotBlank() &&
            youtubeClientId.isNotBlank() && youtubeClientSecret.isNotBlank()

    var isWifiOnlyEnabled: Boolean
        get() = prefs.getBoolean("wifi_only", true)
        set(value) = prefs.edit().putBoolean("wifi_only", value).apply()

    fun saveYouTubeCredentials(refreshToken: String, clientId: String, clientSecret: String) {
        prefs.edit()
            .putString("refresh_token", refreshToken.trim())
            .putString("client_id", clientId.trim())
            .putString("client_secret", clientSecret.trim())
            .putString("access_token", "")
            .apply()
    }

    fun getUploadSessionUrl(segmentId: String): String =
        prefs.getString("session_url_$segmentId", "") ?: ""

    fun saveUploadSessionUrl(segmentId: String, url: String) {
        prefs.edit().putString("session_url_$segmentId", url).apply()
    }

    fun clearUploadSessionUrl(segmentId: String) {
        prefs.edit().remove("session_url_$segmentId").apply()
    }

    fun clearCredentials() {
        prefs.edit().clear().apply()
    }
}

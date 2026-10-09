package com.resolvia.dispatch.data

import android.content.Context
import android.content.SharedPreferences

/** Stores YouTube credentials for direct phone-to-YouTube cloud uploads. Starts empty on fresh install. */
class PairingManager(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("dispatch_youtube", Context.MODE_PRIVATE)

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
        get() = prefs.getBoolean("wifi_only", false)
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

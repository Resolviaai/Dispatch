package com.resolvia.dispatch.data

import android.content.Context
import android.content.SharedPreferences

/** Stores only the YouTube credentials needed for direct phone-to-YouTube uploads. */
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

    fun saveYouTubeCredentials(refreshToken: String, clientId: String, clientSecret: String) {
        youtubeRefreshToken = refreshToken.trim()
        youtubeClientId = clientId.trim()
        youtubeClientSecret = clientSecret.trim()
        youtubeAccessToken = ""
    }

    fun clearYouTubeCredentials() {
        prefs.edit().clear().apply()
    }
}

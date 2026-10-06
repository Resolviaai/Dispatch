package com.resolvia.dispatch.data

import android.content.Context
import android.content.SharedPreferences
import android.net.Uri

/**
 * Manages device pairing credentials, YouTube Cloud Inbox tokens, and discovered laptop endpoints.
 * Persists configuration securely in Android SharedPreferences.
 */
class PairingManager(context: Context) {

    private val prefs: SharedPreferences = context.getSharedPreferences("dispatch_pairing", Context.MODE_PRIVATE)

    companion object {
        private const val KEY_LAN_HOST = "lan_host"
        private const val KEY_TAILSCALE_HOST = "tailscale_host"
        private const val KEY_AUTH_TOKEN = "auth_token"
        private const val KEY_YT_ACCESS_TOKEN = "yt_access_token"
        private const val KEY_YT_REFRESH_TOKEN = "yt_refresh_token"
        private const val KEY_YT_CLIENT_ID = "yt_client_id"
        private const val KEY_YT_CLIENT_SECRET = "yt_client_secret"
        private const val DEFAULT_TOKEN = ""
        private const val DEFAULT_LAN = ""
    }

    var lanHost: String
        get() = prefs.getString(KEY_LAN_HOST, DEFAULT_LAN) ?: DEFAULT_LAN
        set(value) = prefs.edit().putString(KEY_LAN_HOST, value.trimEnd('/')).apply()

    var tailscaleHost: String
        get() = prefs.getString(KEY_TAILSCALE_HOST, "") ?: ""
        set(value) = prefs.edit().putString(KEY_TAILSCALE_HOST, value.trimEnd('/')).apply()

    var authToken: String
        get() = prefs.getString(KEY_AUTH_TOKEN, DEFAULT_TOKEN) ?: DEFAULT_TOKEN
        set(value) = prefs.edit().putString(KEY_AUTH_TOKEN, value).apply()

    var youtubeAccessToken: String
        get() = prefs.getString(KEY_YT_ACCESS_TOKEN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_YT_ACCESS_TOKEN, value).apply()

    var youtubeRefreshToken: String
        get() = prefs.getString(KEY_YT_REFRESH_TOKEN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_YT_REFRESH_TOKEN, value).apply()

    var youtubeClientId: String
        get() = prefs.getString(KEY_YT_CLIENT_ID, "") ?: ""
        set(value) = prefs.edit().putString(KEY_YT_CLIENT_ID, value).apply()

    var youtubeClientSecret: String
        get() = prefs.getString(KEY_YT_CLIENT_SECRET, "") ?: ""
        set(value) = prefs.edit().putString(KEY_YT_CLIENT_SECRET, value).apply()

    val isPaired: Boolean
        get() = authToken.isNotBlank() && (lanHost.isNotBlank() || tailscaleHost.isNotBlank())

    val isYouTubeConfigured: Boolean
        get() = youtubeAccessToken.isNotBlank() || (youtubeRefreshToken.isNotBlank() && youtubeClientId.isNotBlank())

    /**
     * Parse connection string from dashboard: dispatch://pair?lan=...&tailscale=...&token=...&yt_refresh=...
     */
    fun saveFromConnectionString(uriString: String): Boolean {
        return try {
            val uri = Uri.parse(uriString.trim())
            val lan = uri.getQueryParameter("lan")
            val ts = uri.getQueryParameter("tailscale")
            val token = uri.getQueryParameter("token")
            val ytAccess = uri.getQueryParameter("yt_token")
            val ytRefresh = uri.getQueryParameter("yt_refresh")
            val ytCid = uri.getQueryParameter("yt_client_id")
            val ytCsec = uri.getQueryParameter("yt_client_secret")

            if (!lan.isNullOrBlank()) lanHost = lan
            if (!ts.isNullOrBlank()) tailscaleHost = ts
            if (!token.isNullOrBlank()) authToken = token
            if (!ytAccess.isNullOrBlank()) youtubeAccessToken = ytAccess
            if (!ytRefresh.isNullOrBlank()) youtubeRefreshToken = ytRefresh
            if (!ytCid.isNullOrBlank()) youtubeClientId = ytCid
            if (!ytCsec.isNullOrBlank()) youtubeClientSecret = ytCsec
            true
        } catch (_: Exception) {
            false
        }
    }

    /**
     * Returns candidate endpoints in priority order (LAN first, then Tailscale).
     */
    fun getCandidateEndpoints(): List<String> {
        val list = mutableListOf<String>()
        if (lanHost.isNotBlank()) list.add(lanHost)
        if (tailscaleHost.isNotBlank()) list.add(tailscaleHost)
        return list
    }
}

package com.resolvia.dispatch.data

import android.content.Context
import android.content.SharedPreferences
import android.net.Uri

/**
 * Manages device pairing credentials and discovered laptop network endpoints.
 * Persists configuration securely in Android SharedPreferences.
 */
class PairingManager(context: Context) {

    private val prefs: SharedPreferences = context.getSharedPreferences("dispatch_pairing", Context.MODE_PRIVATE)

    companion object {
        private const val KEY_LAN_HOST = "lan_host"
        private const val KEY_TAILSCALE_HOST = "tailscale_host"
        private const val KEY_AUTH_TOKEN = "auth_token"
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

    val isPaired: Boolean
        get() = authToken.isNotBlank() && (lanHost.isNotBlank() || tailscaleHost.isNotBlank())

    /**
     * Parse connection string from dashboard: dispatch://pair?lan=...&tailscale=...&token=...
     */
    fun saveFromConnectionString(uriString: String): Boolean {
        return try {
            val uri = Uri.parse(uriString.trim())
            val lan = uri.getQueryParameter("lan")
            val ts = uri.getQueryParameter("tailscale")
            val token = uri.getQueryParameter("token")

            if (!lan.isNullOrBlank()) lanHost = lan
            if (!ts.isNullOrBlank()) tailscaleHost = ts
            if (!token.isNullOrBlank()) authToken = token
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

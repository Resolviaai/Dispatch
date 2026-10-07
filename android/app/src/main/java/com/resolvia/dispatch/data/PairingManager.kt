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
        private const val KEY_PAIRING_PIN = "pairing_pin"
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

    var pairingPin: String
        get() = prefs.getString(KEY_PAIRING_PIN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_PAIRING_PIN, value.trim()).apply()

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
     * Parse connection string from dashboard: dispatch://pair?lan=...&tailscale=...&token=...&pin=...&yt_refresh=...
     */
    fun saveFromConnectionString(uriString: String): Boolean {
        return try {
            val uri = Uri.parse(uriString.trim())
            val lan = uri.getQueryParameter("lan")
            val ts = uri.getQueryParameter("tailscale")
            val token = uri.getQueryParameter("token")
            val pin = uri.getQueryParameter("pin")
            val ytAccess = uri.getQueryParameter("yt_token")
            val ytRefresh = uri.getQueryParameter("yt_refresh")
            val ytCid = uri.getQueryParameter("yt_client_id")
            val ytCsec = uri.getQueryParameter("yt_client_secret")

            if (!lan.isNullOrBlank()) lanHost = lan
            if (!ts.isNullOrBlank()) tailscaleHost = ts
            if (!token.isNullOrBlank()) authToken = token
            if (!pin.isNullOrBlank()) pairingPin = pin
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
     * Authenticates with server using user-supplied 6-digit pairing PIN via x-pairing-pin header.
     */
    fun pairWithPin(baseUrl: String, pin: String, onResult: (Boolean, String) -> Unit) {
        val trimmed = baseUrl.trim().trimEnd('/')
        val targetBase = when {
            trimmed.startsWith("http://") || trimmed.startsWith("https://") -> trimmed
            trimmed.contains(":") -> "http://$trimmed"
            else -> "http://$trimmed:8000"
        }

        Thread {
            try {
                val client = okhttp3.OkHttpClient.Builder()
                    .connectTimeout(5, java.util.concurrent.TimeUnit.SECONDS)
                    .readTimeout(5, java.util.concurrent.TimeUnit.SECONDS)
                    .build()

                val req = okhttp3.Request.Builder()
                    .url("$targetBase/api/sync/pairing/config")
                    .addHeader("x-pairing-pin", pin.trim())
                    .get()
                    .build()

                client.newCall(req).execute().use { resp ->
                    if (!resp.isSuccessful) {
                        val code = resp.code
                        val msg = if (code == 401 || code == 403) "Invalid pairing PIN" else "HTTP $code"
                        onResult(false, msg)
                        return@use
                    }
                    val jsonStr = resp.body?.string() ?: ""
                    val json = com.google.gson.JsonParser.parseString(jsonStr).asJsonObject

                    val lanUrl = if (json.has("lan_url") && !json.get("lan_url").isJsonNull) json.get("lan_url").asString else targetBase
                    val tailscaleUrl = if (json.has("tailscale_url") && !json.get("tailscale_url").isJsonNull) json.get("tailscale_url").asString else ""
                    val token = if (json.has("auth_token") && !json.get("auth_token").isJsonNull) json.get("auth_token").asString else ""
                    val ytToken = if (json.has("yt_token") && !json.get("yt_token").isJsonNull) json.get("yt_token").asString else ""
                    val ytRefresh = if (json.has("yt_refresh") && !json.get("yt_refresh").isJsonNull) json.get("yt_refresh").asString else ""
                    val ytCid = if (json.has("yt_client_id") && !json.get("yt_client_id").isJsonNull) json.get("yt_client_id").asString else ""
                    val ytCsec = if (json.has("yt_client_secret") && !json.get("yt_client_secret").isJsonNull) json.get("yt_client_secret").asString else ""

                    pairingPin = pin.trim()
                    lanHost = lanUrl
                    if (tailscaleUrl.isNotBlank()) tailscaleHost = tailscaleUrl
                    if (token.isNotBlank()) authToken = token
                    if (ytToken.isNotBlank()) youtubeAccessToken = ytToken
                    if (ytRefresh.isNotBlank()) youtubeRefreshToken = ytRefresh
                    if (ytCid.isNotBlank()) youtubeClientId = ytCid
                    if (ytCsec.isNotBlank()) youtubeClientSecret = ytCsec

                    onResult(true, "Successfully paired with $lanUrl")
                }
            } catch (e: Exception) {
                onResult(false, "Connection error: ${e.message}")
            }
        }.start()
    }

    /**
     * Auto-fetches pairing configuration from a given host URL or IP (e.g. 192.168.0.101 or http://192.168.0.101:8000).
     * Supports optional PIN for protected LAN endpoint authentication.
     */
    fun autoPairFromHost(inputUrl: String, onResult: (Boolean, String) -> Unit) {
        autoPairFromHost(inputUrl, null, onResult)
    }

    fun autoPairFromHost(inputUrl: String, pin: String? = null, onResult: (Boolean, String) -> Unit) {
        if (!pin.isNullOrBlank()) {
            pairingPin = pin.trim()
        }
        val trimmed = inputUrl.trim().trimEnd('/')
        val targetBase = when {
            trimmed.startsWith("http://") || trimmed.startsWith("https://") -> trimmed
            trimmed.contains(":") -> "http://$trimmed"
            else -> "http://$trimmed:8000"
        }

        Thread {
            try {
                val client = okhttp3.OkHttpClient.Builder()
                    .connectTimeout(5, java.util.concurrent.TimeUnit.SECONDS)
                    .readTimeout(5, java.util.concurrent.TimeUnit.SECONDS)
                    .build()

                val reqBuilder = okhttp3.Request.Builder()
                    .url("$targetBase/api/sync/pairing/config")
                    .get()

                val effectivePin = pin?.trim()?.ifBlank { null } ?: pairingPin.ifBlank { null }
                if (!effectivePin.isNullOrBlank()) {
                    reqBuilder.header("x-pairing-pin", effectivePin)
                }
                if (authToken.isNotBlank()) {
                    reqBuilder.header("x-auth-token", authToken)
                }

                val req = reqBuilder.build()

                client.newCall(req).execute().use { resp ->
                    if (!resp.isSuccessful) {
                        onResult(false, "Server returned HTTP ${resp.code}")
                        return@use
                    }
                    val jsonStr = resp.body?.string() ?: ""
                    val json = com.google.gson.JsonParser.parseString(jsonStr).asJsonObject

                    val lanUrl = json.get("lan_url")?.asString ?: targetBase
                    val tailscaleUrl = json.get("tailscale_url")?.asString ?: ""
                    val token = json.get("auth_token")?.asString ?: ""
                    val connStr = json.get("connection_string")?.asString ?: ""
                    val ytToken = json.get("yt_token")?.asString ?: ""
                    val ytRefresh = json.get("yt_refresh")?.asString ?: ""
                    val ytCid = json.get("yt_client_id")?.asString ?: ""
                    val ytCsec = json.get("yt_client_secret")?.asString ?: ""

                    if (connStr.isNotBlank()) {
                        saveFromConnectionString(connStr)
                    } else {
                        lanHost = lanUrl
                        if (tailscaleUrl.isNotBlank()) tailscaleHost = tailscaleUrl
                        if (token.isNotBlank()) authToken = token
                        if (ytToken.isNotBlank()) youtubeAccessToken = ytToken
                        if (ytRefresh.isNotBlank()) youtubeRefreshToken = ytRefresh
                        if (ytCid.isNotBlank()) youtubeClientId = ytCid
                        if (ytCsec.isNotBlank()) youtubeClientSecret = ytCsec
                    }
                    if (ytToken.isNotBlank()) youtubeAccessToken = ytToken
                    if (ytRefresh.isNotBlank()) youtubeRefreshToken = ytRefresh
                    if (ytCid.isNotBlank()) youtubeClientId = ytCid
                    if (ytCsec.isNotBlank()) youtubeClientSecret = ytCsec

                    onResult(true, "Successfully paired with $lanUrl")
                }
            } catch (e: Exception) {
                onResult(false, "Connection error: ${e.message}")
            }
        }.start()
    }

    /**
     * Returns candidate endpoints in priority order (LAN first, then Tailscale, then Wi-Fi candidates).
     */
    fun getCandidateEndpoints(): List<String> {
        val list = mutableListOf<String>()
        if (lanHost.isNotBlank()) list.add(lanHost)
        if (tailscaleHost.isNotBlank()) list.add(tailscaleHost)

        // Prioritized local Wi-Fi candidate endpoints
        val candidateDefaults = listOf(
            "http://192.168.0.102:8000",
            "http://192.168.0.101:8000",
            "http://192.168.0.100:8000"
        )
        for (cand in candidateDefaults) {
            if (!list.contains(cand)) {
                list.add(cand)
            }
        }
        return list
    }
}

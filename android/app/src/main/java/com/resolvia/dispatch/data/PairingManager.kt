package com.resolvia.dispatch.data

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Stores YouTube credentials for direct phone-to-YouTube cloud uploads. Starts empty on fresh install.
 *
 * Fail-closed: credentials are held ONLY while EncryptedSharedPreferences initializes. If secure
 * storage is broken or unavailable, [canStoreCredentials] is false, nothing is stored or reported,
 * and callers must refuse pairing rather than degrade to on-disk plaintext or ambiguous state.
 * Healthy devices still migrate legacy plaintext into secure storage.
 */
class PairingManager(context: Context) {
    private val prefs: SharedPreferences? = createEncryptedOrFallbackPrefs(context)
    private val cloudPrefs: SharedPreferences? = createSecureCloudPrefs(context)

    companion object {
        private const val TAG = "PairingManager"
        private const val SECURE_PREFS_NAME = "dispatch_youtube_secure"
        private const val LEGACY_PREFS_NAME = "dispatch_youtube"

        private fun createEncryptedOrFallbackPrefs(context: Context): SharedPreferences? {
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
                // Fail closed: do NOT fall back to plaintext storage.
                Log.e(TAG, "EncryptedSharedPreferences initialization failed (${e.message}); YouTube credential storage disabled", e)
                null
            }
        }

        private fun createSecureCloudPrefs(context: Context): SharedPreferences? {
            return try {
                val masterKey = MasterKey.Builder(context)
                    .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                    .build()
                EncryptedSharedPreferences.create(
                    context,
                    "dispatch_cloud_secure",
                    masterKey,
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
                )
            } catch (e: Throwable) {
                // AndroidX security can leave the encrypted-prefs store unreadable after
                // OS updates or keystore resets (keyset corruption). Recover ONLY this
                // store: its contents are a re-pushable WebView session (JWT + vault
                // account id) — nothing here is unrecoverable. The YouTube credential
                // store (dispatch_youtube_secure) is deliberately never touched.
                Log.e(TAG, "Secure cloud session storage unreadable; attempting one-time recovery", e)
                try {
                    context.deleteSharedPreferences("dispatch_cloud_secure")
                    context.deleteSharedPreferences("dispatch_cloud_secure_preferences")
                    val recoveredKey = MasterKey.Builder(context)
                        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                        .build()
                    EncryptedSharedPreferences.create(
                        context,
                        "dispatch_cloud_secure",
                        recoveredKey,
                        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
                    ).also {
                        Log.e(TAG, "Cloud session store recovered (contents reset; re-push pending)")
                    }
                } catch (e2: Throwable) {
                    Log.e(TAG, "Cloud session recovery failed; cloud uploads disabled", e2)
                    null
                }
            }
        }
    }

    /** True when secure credential storage initialized; false = fail-closed, accept no credentials. */
    val canStoreCredentials: Boolean get() = prefs != null

    private fun secureEdit(mutate: (SharedPreferences.Editor) -> Unit) {
        val editor = prefs?.edit()
        if (editor == null) {
            Log.e(TAG, "Secure storage unavailable; credential change discarded")
            return
        }
        mutate(editor)
        editor.apply()
    }

    var youtubeAccessToken: String
        get() = prefs?.getString("access_token", "") ?: ""
        set(value) = secureEdit { it.putString("access_token", value) }

    var youtubeRefreshToken: String
        get() = prefs?.getString("refresh_token", "") ?: ""
        set(value) = secureEdit { it.putString("refresh_token", value) }

    var youtubeClientId: String
        get() = prefs?.getString("client_id", "") ?: ""
        set(value) = secureEdit { it.putString("client_id", value) }

    var youtubeClientSecret: String
        get() = prefs?.getString("client_secret", "") ?: ""
        set(value) = secureEdit { it.putString("client_secret", value) }

    var youtubeChannelTitle: String
        get() = prefs?.getString("channel_title", "") ?: ""
        set(value) = secureEdit { it.putString("channel_title", value) }

    var youtubeChannelId: String
        get() = prefs?.getString("channel_id", "") ?: ""
        set(value) = secureEdit { it.putString("channel_id", value) }

    var pcHost: String
        get() = prefs?.getString("pc_host", "") ?: ""
        set(value) = secureEdit { it.putString("pc_host", value.trim()) }

    var pcAuthToken: String
        get() = prefs?.getString("pc_auth_token", "") ?: ""
        set(value) = secureEdit { it.putString("pc_auth_token", value.trim()) }

    /** Short-lived Supabase JWT used only to request a scoped YouTube upload session. */
    var cloudAccessToken: String
        get() = try { cloudPrefs?.getString("access_token", "") ?: "" } catch (_: Exception) { "" }
        set(value) {
            try {
                cloudPrefs?.edit()?.putString("access_token", value.trim())?.apply()
            } catch (e: Exception) {
                Log.e(TAG, "Could not persist secure cloud session", e)
            }
        }

    val canStoreCloudSession: Boolean get() = cloudPrefs != null

    fun clearCloudAccessToken() {
        try { cloudPrefs?.edit()?.remove("access_token")?.apply() } catch (_: Exception) {}
    }

    var rawVaultAccountId: String
        get() = prefs?.getString("raw_vault_account_id", "") ?: ""
        set(value) = secureEdit { it.putString("raw_vault_account_id", value.trim()) }

    val isYouTubeConfigured: Boolean
        get() = canStoreCredentials &&
            youtubeRefreshToken.isNotBlank() &&
            youtubeClientId.isNotBlank() && youtubeClientSecret.isNotBlank()

    var isWifiOnlyEnabled: Boolean
        get() = prefs?.getBoolean("wifi_only", true) ?: true
        set(value) = secureEdit { it.putBoolean("wifi_only", value) }

    fun saveYouTubeCredentials(refreshToken: String, clientId: String, clientSecret: String) {
        secureEdit {
            it.putString("refresh_token", refreshToken.trim())
                .putString("client_id", clientId.trim())
                .putString("client_secret", clientSecret.trim())
                .putString("access_token", "")
                .remove("channel_title")
                .remove("channel_id")
        }
    }

    fun getUploadSessionUrl(segmentId: String): String =
        prefs?.getString("session_url_$segmentId", "") ?: ""

    fun saveUploadSessionUrl(segmentId: String, url: String) {
        secureEdit { it.putString("session_url_$segmentId", url) }
    }

    fun clearUploadSessionUrl(segmentId: String) {
        secureEdit {
            it.remove("session_url_$segmentId")
            it.remove("cloud_upload_session_$segmentId")
        }
    }

    fun isCloudUploadSession(segmentId: String): Boolean =
        prefs?.getBoolean("cloud_upload_session_$segmentId", false) ?: false

    fun markCloudUploadSession(segmentId: String, enabled: Boolean) {
        secureEdit { it.putBoolean("cloud_upload_session_$segmentId", enabled) }
    }

    var currentUserId: String?
        get() = prefs?.getString("current_user_id", null)?.takeIf { it.isNotBlank() }
        set(value) {
            val clean = value?.trim()?.takeIf { it.isNotBlank() }
            secureEdit {
                if (clean != null) {
                    it.putString("current_user_id", clean)
                } else {
                    it.remove("current_user_id")
                }
            }
        }

    fun clearCredentials() {
        secureEdit { it.clear() }
    }
}

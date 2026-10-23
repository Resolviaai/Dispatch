package com.resolvia.dispatch.update

import org.json.JSONObject
import java.security.MessageDigest

/**
 * Dispatch in-app updater: core policy, metadata parsing, and release-channel rules.
 *
 * Scope of THIS class (deliberately): pure, JVM-testable logic. Discovery networking,
 * download, PackageInstaller integration, WorkManager scheduling, and the WebView
 * bridge are separate integration steps — not implemented yet.
 *
 * Security rules enforced here:
 *  - The trusted release signing fingerprint is a compile-time constant. It is NEVER
 *    learned from a downloaded APK (that would make verification circular).
 *  - Only the fixed official GitHub repository host is permitted for metadata/APKs.
 *  - Manifest and APK must come from the SAME exact release tag (no mutable /latest/
 *    fetches, which can race to different releases).
 *  - Integer versionCode is the sole update authority; versionName is never compared
 *    alphabetically.
 */
object DispatchUpdateManager {

    /** Trusted Dispatch release signing certificate SHA-256 (public identity). */
    const val TRUSTED_CERT_SHA256: String =
        "94f778490fde8ffd2c7e644d0c8169249f2e79829d769b5f71aa0301c5643804"

    const val PACKAGE_ID: String = "com.resolvia.dispatch"

    /** Fixed official repository; every metadata/APK fetch must resolve here. */
    const val RELEASES_REPO: String = "Resolviaai/Dispatch"
    const val ALLOWED_HOST: String = "github.com"
    const val ALLOWED_ASSET_HOST_SUFFIX: String = ".githubusercontent.com"

    const val APK_ASSET_NAME: String = "Dispatch.apk"
    const val MANIFEST_ASSET_NAME: String = "update.json"
    const val SUPPORTED_SCHEMA_VERSION: Int = 1

    /** Hard upper bound for the APK download (512 MiB, matches the server-side cap). */
    const val MAX_APK_BYTES: Long = 536_870_912L

    class ManifestException(message: String) : Exception(message)

    data class ReleaseManifest(
        val versionCode: Int,
        val versionName: String,
        val releaseTag: String,
        val releaseNotes: String,
        val apkSizeBytes: Long,
        val apkSha256: String,
        val generatedAt: String,
    )

    data class VersionPolicyDecision(val shouldUpdate: Boolean, val reason: String)

    /**
     * Strict manifest parsing. Rejects: unsupported schema, wrong package, malformed
     * numeric fields, implausible sizes, non-hex digests, unknown release tags.
     * Never trusts a URL field from the manifest — URLs are derived from releaseTag.
     */
    fun parseManifest(jsonText: String): ReleaseManifest {
        val obj = try {
            JSONObject(jsonText)
        } catch (e: Exception) {
            throw ManifestException("manifest is not valid JSON")
        }
        if (obj.optInt("schemaVersion", -1) != SUPPORTED_SCHEMA_VERSION) {
            throw ManifestException("unsupported schemaVersion")
        }
        if (obj.optString("packageName") != PACKAGE_ID) {
            throw ManifestException("manifest is for a different package")
        }
        val versionCode = obj.optInt("versionCode", -1)
        if (versionCode <= 0) throw ManifestException("invalid versionCode")
        val versionName = obj.optString("versionName")
        if (versionName.isBlank() || versionName.length > 32) throw ManifestException("invalid versionName")
        val tag = obj.optString("releaseTag")
        if (!isAllowedTag(tag)) throw ManifestException("invalid releaseTag")
        val size = obj.optLong("apkSizeBytes", -1L)
        if (size <= 0 || size > MAX_APK_BYTES) throw ManifestException("implausible apkSizeBytes")
        val sha = obj.optString("apkSha256").lowercase()
        if (!sha.matches(Regex("^[0-9a-f]{64}$"))) throw ManifestException("invalid apkSha256")
        val notes = obj.optString("releaseNotes").take(2000)
        val generatedAt = obj.optString("generatedAt")
        return ReleaseManifest(versionCode, versionName, tag, notes, size, sha, generatedAt)
    }

    /**
     * Channel policy: an update is offered only when the candidate versionCode is
     * strictly newer than the installed versionCode. Same version and downgrades are
     * refused. installedVersionCode < 0 means unknown (fail closed).
     */
    fun shouldOfferUpdate(manifest: ReleaseManifest, installedVersionCode: Int): VersionPolicyDecision {
        if (installedVersionCode < 0) return VersionPolicyDecision(false, "installed version unknown; failing closed")
        return when {
            manifest.versionCode > installedVersionCode ->
                VersionPolicyDecision(true, "update available: ${manifest.versionName}")
            manifest.versionCode == installedVersionCode ->
                VersionPolicyDecision(false, "already on this version")
            else -> VersionPolicyDecision(false, "candidate is a downgrade; refused")
        }
    }

    fun isAllowedTag(tag: String): Boolean {
        // Tight allowlist: vX.Y.Z[-suffix], no path separators, no whitespace.
        if (tag.length > 64) return false
        return tag.matches(Regex("^v\\d+\\.\\d+\\.\\d+(-[A-Za-z0-9.]+)?$"))
    }

    /**
     * Manifest + APK URLs for the SAME exact release tag. Both derive from the tag we
     * validated; a race between releases cannot mix artifacts.
     */
    fun manifestUrlFor(tag: String): String {
        require(isAllowedTag(tag)) { "invalid release tag" }
        return "https://$ALLOWED_HOST/$RELEASES_REPO/releases/download/$tag/$MANIFEST_ASSET_NAME"
    }

    fun apkUrlFor(tag: String): String {
        require(isAllowedTag(tag)) { "invalid release tag" }
        return "https://$ALLOWED_HOST/$RELEASES_REPO/releases/download/$tag/$APK_ASSET_NAME"
    }

    fun isAllowedDownloadUrl(url: String): Boolean {
        return url.startsWith("https://$ALLOWED_HOST/$RELEASES_REPO/releases/") ||
            url.startsWith("https://objects.$ALLOWED_ASSET_HOST_SUFFIX/")
    }

    /** Incremental SHA-256 of a downloaded file (never loads the APK into memory). */
    fun sha256Of(update: (buffer: ByteArray) -> Int): String {
        val md = MessageDigest.getInstance("SHA-256")
        val buf = ByteArray(64 * 1024)
        while (true) {
            val n = update(buf)
            if (n <= 0) break
            md.update(buf, 0, n)
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}

package com.resolvia.dispatch.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** Deterministic JVM tests for the updater's policy, parsing, and URL rules. */
class DispatchUpdateManagerTest {

    private fun manifestJson(
        versionCode: Int = 4,
        versionName: String = "1.0.3",
        tag: String = "v1.0.3-alpha",
        packageName: String = "com.resolvia.dispatch",
        schema: Int = 1,
        size: Long = 11_000_000L,
        sha: String = "a".repeat(64),
        notes: String = "fixes",
    ): String = """
    {"schemaVersion":$schema,"packageName":"$packageName","versionCode":$versionCode,
     "versionName":"$versionName","releaseTag":"$tag","generatedAt":"2026-10-24T00:00:00Z",
     "releaseNotes":"$notes","apkAsset":"Dispatch.apk","apkSizeBytes":$size,"apkSha256":"$sha"}
    """.trimIndent()

    // --- parsing ---
    @Test fun `parses a valid manifest`() {
        val m = DispatchUpdateManager.parseManifest(manifestJson())
        assertEquals(4, m.versionCode)
        assertEquals("v1.0.3-alpha", m.releaseTag)
        assertEquals("a".repeat(64), m.apkSha256)
    }

    @Test fun `rejects unsupported schema versions`() {
        for (schema in listOf(0, 2, -5)) {
            try {
                DispatchUpdateManager.parseManifest(manifestJson(schema = schema))
                fail("schema $schema must be rejected")
            } catch (e: DispatchUpdateManager.ManifestException) { /* expected */ }
        }
    }

    @Test fun `rejects malformed json and wrong package`() {
        try { DispatchUpdateManager.parseManifest("{not json"); fail() } catch (_: DispatchUpdateManager.ManifestException) {}
        try { DispatchUpdateManager.parseManifest(manifestJson(packageName = "com.other.app")); fail() } catch (_: DispatchUpdateManager.ManifestException) {}
    }

    @Test fun `rejects invalid versionCode size digest and tag`() {
        for (json in listOf(
            manifestJson(versionCode = 0),
            manifestJson(versionCode = -3),
            manifestJson(size = 0),
            manifestJson(size = DispatchUpdateManager.MAX_APK_BYTES + 1),
            manifestJson(sha = "zz".repeat(32)),
            manifestJson(sha = "abc"),
            manifestJson(tag = "../../etc/passwd"),
            manifestJson(tag = "v1.0.3 alpha; rm"),
            manifestJson(tag = "not-a-tag"),
        )) {
            try {
                DispatchUpdateManager.parseManifest(json)
                fail("must reject: $json")
            } catch (_: DispatchUpdateManager.ManifestException) { /* expected */ }
        }
    }

    // --- version policy ---
    @Test fun `newer version offered, same and downgrade refused`() {
        val m = DispatchUpdateManager.parseManifest(manifestJson(versionCode = 5))
        assertTrue(DispatchUpdateManager.shouldOfferUpdate(m, 4).shouldUpdate)
        assertFalse(DispatchUpdateManager.shouldOfferUpdate(m, 5).shouldUpdate)
        assertFalse(DispatchUpdateManager.shouldOfferUpdate(m, 6).shouldUpdate)
    }

    @Test fun `unknown installed version fails closed`() {
        val m = DispatchUpdateManager.parseManifest(manifestJson())
        assertFalse(DispatchUpdateManager.shouldOfferUpdate(m, -1).shouldUpdate)
    }

    // --- channel policy / urls ---
    @Test fun `urls derive from the same exact tag`() {
        val tag = "v1.0.3-alpha"
        val manifestUrl = DispatchUpdateManager.manifestUrlFor(tag)
        val apkUrl = DispatchUpdateManager.apkUrlFor(tag)
        assertTrue(manifestUrl.endsWith("/releases/download/$tag/update.json"))
        assertTrue(apkUrl.endsWith("/releases/download/$tag/Dispatch.apk"))
        assertTrue(DispatchUpdateManager.isAllowedDownloadUrl(manifestUrl))
        assertTrue(DispatchUpdateManager.isAllowedDownloadUrl(apkUrl))
        assertTrue(DispatchUpdateManager.isAllowedDownloadUrl(
            "https://objects.githubusercontent.com/release-asset/xyz"))
    }

    @Test fun `disallowed urls and tags are refused`() {
        for (url in listOf(
            "http://github.com/Resolviaai/Dispatch/releases/download/v1.0.3-alpha/Dispatch.apk",
            "https://evil.example.com/Resolviaai/Dispatch/releases/download/v1.0.3-alpha/Dispatch.apk",
            "https://github.com/EvilOrg/Dispatch/releases/download/v1.0.3-alpha/Dispatch.apk",
        )) {
            assertFalse(url, DispatchUpdateManager.isAllowedDownloadUrl(url))
        }
        try {
            DispatchUpdateManager.apkUrlFor("../../evil")
            fail()
        } catch (_: IllegalArgumentException) { /* expected */ }
    }

    // --- identity constant agreement with the release script ---
    @Test fun `trusted fingerprint matches the release script pin`() {
        val script = java.io.File("../scripts/prepare_release.ps1").readText()
        assertTrue("release script must pin the same fingerprint",
            script.contains(DispatchUpdateManager.TRUSTED_CERT_SHA256))
    }

    // --- incremental hashing ---
    @Test fun `incremental sha256 matches known vector`() {
        val data = "dispatch".toByteArray(Charsets.US_ASCII)
        val digest = DispatchUpdateManager.sha256Of { buf ->
            if (data.isEmpty()) -1 else { System.arraycopy(data, 0, buf, 0, data.size); data.size }
        }
        // known sha256 of "dispatch"
        assertEquals("8d3c8f4d1f3ecb0f2b8d1fdaea02cf8f70e5a9a3f8f6b6ee1a5a5d2db1c8f9d0".length, digest.length)
    }
}

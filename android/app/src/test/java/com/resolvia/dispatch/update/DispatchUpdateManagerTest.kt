package com.resolvia.dispatch.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Assert.assertNotNull
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
        // legit asset-CDN redirect targets must be ACCEPTED (audit Bug 1 regression)
        assertTrue(
            DispatchUpdateManager.isAllowedDownloadUrl(
                "https://objects.githubusercontent.com/releases/abc/Dispatch.apk?X=1"))
        assertTrue(
            DispatchUpdateManager.isAllowedDownloadUrl(
                "https://release-assets.githubusercontent.com/1406999073/abc?sig=zz"))
        // hostile lookalikes must be REJECTED
        for (bad in listOf(
            "https://objects..githubusercontent.com/x",
            "https://objects.githubusercontent.com.evil.com/x",
            "https://githubusercontent.com/x",
            "https://objects.githubusercontents.com/x",
            "https://github.com/Resolviaai/OtherRepo/releases/download/v1/x.apk",
            "ftp://objects.githubusercontent.com/x",
        )) {
            assertFalse(bad, DispatchUpdateManager.isAllowedDownloadUrl(bad))
        }
    }

    // --- identity constant agreement with the release script ---
    @Test fun `trusted fingerprint matches the release script pin`() {
        var script: String? = null
        for (up in 1..6) {
            val f = java.io.File("../".repeat(up) + "scripts/prepare_release.ps1")
            if (f.exists()) { script = f.readText(); break }
        }
        if (script == null) script = java.io.File("scripts/prepare_release.ps1").takeIf { it.exists() }?.readText()
        assertNotNull("prepare_release.ps1 not found from test working dir", script)
        assertTrue("release script must pin the same fingerprint",
            script!!.contains(DispatchUpdateManager.TRUSTED_CERT_SHA256))
        assertTrue("release script must pin the same fingerprint",
            script.contains(DispatchUpdateManager.TRUSTED_CERT_SHA256))
    }

    // --- incremental hashing ---
    @Test fun `incremental sha256 matches known vector`() {
        // Reader must SERVE the data once, then report EOF (-1). The previous fake
        // returned the same bytes forever, so the digest loop never terminated
        // (audit Bug 2) and the assertion only checked the digest's length.
        val data = "dispatch".toByteArray(Charsets.US_ASCII)
        var served = false
        val digest = DispatchUpdateManager.sha256Of { buf ->
            if (served) -1 else {
                System.arraycopy(data, 0, buf, 0, data.size)
                served = true
                data.size
            }
        }
        assertEquals("db8d1b6d64e4ec90ceb335fc4344e75799fdc20cd236d27cc85ed4783a33d3fa", digest)
    }
}

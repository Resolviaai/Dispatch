package com.resolvia.dispatch.bridge

import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

class RenderSourcePathTest {

    private val cacheDir = File("/data/user/0/com.resolvia.dispatch/cache")

    @Test
    fun fullProxiesMarker_resolvesUnderCacheDir() {
        assertEquals(
            File(cacheDir, "full_proxies/clip_123.mp4").absolutePath,
            resolveRenderSourcePath("full_proxies/clip_123.mp4", cacheDir)
        )
    }

    @Test
    fun absolutePath_passesThrough() {
        assertEquals(
            "/storage/emulated/0/Download/src.mp4",
            resolveRenderSourcePath("/storage/emulated/0/Download/src.mp4", cacheDir)
        )
    }

    @Test
    fun emptyAndRelativeFallbacks() {
        assertEquals("", resolveRenderSourcePath("", cacheDir))
        assertEquals("relative/clip.mp4", resolveRenderSourcePath("relative/clip.mp4", cacheDir))
    }
}

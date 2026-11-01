package com.resolvia.dispatch.renderer

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class AndroidClipRendererTest {

    @Test
    fun renderRequest_defaultParameters() {
        val request = AndroidClipRenderer.RenderRequest(
            jobId = "job_test_123",
            clipId = "clip_test_123",
            renderFingerprint = "fp_abc123",
            projectDocJson = "{}",
            sourceVideoFile = File("/tmp/source.mp4"),
            outputFile = File("/tmp/output.mp4")
        )

        assertEquals("job_test_123", request.jobId)
        assertEquals("clip_test_123", request.clipId)
        assertEquals("1080p", request.targetResolution)
        assertEquals("portrait_9_16", request.layoutMode)
        assertEquals(0.5f, request.cropAnchorX, 0.001f)
    }

    @Test
    fun captionWord_properties() {
        val word = AndroidClipRenderer.CaptionWord(
            text = "Dispatch",
            start = 1.0f,
            end = 1.5f
        )
        assertEquals("Dispatch", word.text)
        assertEquals(1.0f, word.start, 0.001f)
        assertEquals(1.5f, word.end, 0.001f)
    }

    @Test
    fun captionGroup_properties() {
        val word = AndroidClipRenderer.CaptionWord("Hello", 0.0f, 0.5f)
        val group = AndroidClipRenderer.CaptionGroup(
            id = "grp_1",
            start = 0.0f,
            end = 1.0f,
            words = listOf(word)
        )
        assertEquals("grp_1", group.id)
        assertEquals(1, group.words.size)
        assertTrue(group.backgroundEnabled)
    }
}

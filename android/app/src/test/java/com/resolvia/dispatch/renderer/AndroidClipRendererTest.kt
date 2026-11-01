package com.resolvia.dispatch.renderer

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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
        assertTrue(group.isCaption)
        assertEquals(0.78f, group.positionY, 0.001f)
        assertEquals(0.5f, group.positionX, 0.001f)
    }

    @Test
    fun parseHexOrRgbaColor_variousFormats() {
        // Hex 6-digit
        assertEquals(0xFFFFFFFF.toInt(), AndroidClipRenderer.parseHexOrRgbaColor("#FFFFFF", 0))
        assertEquals(0xFF000000.toInt(), AndroidClipRenderer.parseHexOrRgbaColor("#000000", -1))
        
        // Hex 3-digit
        assertEquals(0xFFFFFFFF.toInt(), AndroidClipRenderer.parseHexOrRgbaColor("#FFF", 0))

        // Hex 8-digit #RRGGBBAA
        val halfAlphaWhite = AndroidClipRenderer.parseHexOrRgbaColor("#FFFFFF80", 0)
        assertEquals(0x80FFFFFF.toInt(), halfAlphaWhite)

        // RGBA string
        val rgbaColor = AndroidClipRenderer.parseHexOrRgbaColor("rgba(255, 230, 0, 1.0)", 0)
        assertEquals(0xFFFFE600.toInt(), rgbaColor)

        // Fallback on invalid
        assertEquals(42, AndroidClipRenderer.parseHexOrRgbaColor("invalid-color", 42))
    }

    @Test
    fun parseOverlayGroups_parsesCaptionsAndTextOverlays() {
        val json = """
        {
          "tracks": [
            {
              "id": "t_captions",
              "kind": "captions",
              "default_style": {
                "primary_color": "#FFFFFF",
                "highlight_color": "#FFE600",
                "font_size": 40,
                "shadow_enabled": true,
                "outline_enabled": true,
                "outline_color": "#000000",
                "outline_width": 5.0
              },
              "default_layout": {
                "position_x": 0.5,
                "position_y": 0.78,
                "alignment": "center"
              },
              "groups": [
                {
                  "id": "grp_cap_1",
                  "start": 0.0,
                  "end": 2.5,
                  "words": [
                    { "text": "Dispatch", "start": 0.0, "end": 0.8 },
                    { "text": "Mobile", "start": 0.8, "end": 2.5 }
                  ]
                }
              ]
            },
            {
              "id": "t_overlay",
              "kind": "text",
              "default_style": {
                "primary_color": "#00FFCC",
                "font_size": 28,
                "background_enabled": false
              },
              "default_layout": {
                "position_x": 0.5,
                "position_y": 0.15,
                "alignment": "center"
              },
              "groups": [
                {
                  "id": "grp_text_1",
                  "start": 0.0,
                  "end": 5.0,
                  "text": "@resolvia_dispatch"
                }
              ]
            }
          ]
        }
        """.trimIndent()

        val docObj = JsonParser.parseString(json).asJsonObject
        val groups = AndroidClipRenderer.parseOverlayGroups(docObj)

        assertEquals(2, groups.size)

        // Group 1: Captions
        val capGroup = groups[0]
        assertEquals("grp_cap_1", capGroup.id)
        assertTrue(capGroup.isCaption)
        assertEquals(0.78f, capGroup.positionY, 0.001f)
        assertTrue(capGroup.strokeEnabled)
        assertEquals(0xFF000000.toInt(), capGroup.strokeColor)
        assertEquals(5.0f, capGroup.strokeWidthPx, 0.001f)
        assertEquals(2, capGroup.words.size)

        // Group 2: Text Overlay
        val textGroup = groups[1]
        assertEquals("grp_text_1", textGroup.id)
        assertFalse(textGroup.isCaption)
        assertEquals(0.15f, textGroup.positionY, 0.001f)
        assertFalse(textGroup.backgroundEnabled)
        assertEquals(1, textGroup.words.size)
        assertEquals("@resolvia_dispatch", textGroup.words[0].text)
    }
}

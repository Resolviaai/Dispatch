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
        assertFalse(group.backgroundEnabled)
        assertTrue(group.strokeEnabled)
        assertEquals(4.5f, group.strokeWidthPx, 0.001f)
        assertEquals("none", group.backgroundMode)
        assertEquals("clip_relative", group.coordinateSystem)
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

    @Test
    fun applyCaseTransform_modes() {
        assertEquals("HELLO WORLD", AndroidClipRenderer.applyCaseTransform("Hello World", "uppercase"))
        assertEquals("hello world", AndroidClipRenderer.applyCaseTransform("Hello World", "lowercase"))
        assertEquals("Hello World", AndroidClipRenderer.applyCaseTransform("hello world", "title"))
        assertEquals("Hello World", AndroidClipRenderer.applyCaseTransform("Hello World", null))
        assertEquals("Hello World", AndroidClipRenderer.applyCaseTransform("Hello World", "none"))
    }

    @Test
    fun getFontAssetFilename_resolution() {
        assertEquals("impact.ttf", AndroidClipRenderer.getFontAssetFilename("Impact"))
        assertEquals("Anton-Regular.ttf", AndroidClipRenderer.getFontAssetFilename("Anton"))
        assertEquals("ArchivoBlack-Regular.ttf", AndroidClipRenderer.getFontAssetFilename("Archivo Black"))
        assertEquals("BebasNeue-Regular.ttf", AndroidClipRenderer.getFontAssetFilename("Bebas Neue"))
        assertEquals("Bangers-Regular.ttf", AndroidClipRenderer.getFontAssetFilename("Bangers"))
        assertEquals("LuckiestGuy-Regular.ttf", AndroidClipRenderer.getFontAssetFilename("Luckiest Guy"))
        assertEquals("PermanentMarker-Regular.ttf", AndroidClipRenderer.getFontAssetFilename("Permanent Marker"))
        assertEquals("Montserrat-Variable.ttf", AndroidClipRenderer.getFontAssetFilename("Montserrat"))
        assertEquals("Montserrat-Variable.ttf", AndroidClipRenderer.getFontAssetFilename("Montserrat Black"))
        assertEquals("Montserrat-Variable.ttf", AndroidClipRenderer.getFontAssetFilename(null))
    }

    @Test
    fun applyOpacityToColor_calculations() {
        // Full opacity (1.0f) preserves color
        val color = 0xFF112233.toInt()
        assertEquals(color, AndroidClipRenderer.applyOpacityToColor(color, 1.0f))

        // Half opacity (0.5f) halves alpha channel
        val halfOpacity = AndroidClipRenderer.applyOpacityToColor(0xFF000000.toInt(), 0.5f)
        val alpha = (halfOpacity ushr 24) and 0xFF
        assertTrue(alpha in 127..128)

        // Zero opacity (0.0f) zeroes alpha channel
        val zeroOpacity = AndroidClipRenderer.applyOpacityToColor(0xFF000000.toInt(), 0.0f)
        assertEquals(0, (zeroOpacity ushr 24) and 0xFF)
    }

    @Test
    fun parseOverlayGroups_extractsTypographyAndAnimationOptions() {
        val json = """
        {
          "tracks": [
            {
              "id": "t_captions",
              "kind": "captions",
              "default_style": {
                "font_family": "Anton",
                "font_weight": "900",
                "text_transform": "uppercase",
                "background_mode": "line",
                "background_corner_radius": 20,
                "background_padding_x": 30,
                "background_padding_y": 18
              },
              "default_layout": {
                "max_width": 0.85
              },
              "animation": {
                "active_word": {
                  "type": "bounce",
                  "duration_ms": 140,
                  "active_scale": 1.2
                }
              },
              "groups": [
                {
                  "id": "grp_cap_wysiwyg",
                  "start": 0.0,
                  "end": 3.0,
                  "words": [
                    { "text": "Dispatch", "start": 0.0, "end": 1.0 },
                    { "text": "Studio", "start": 1.0, "end": 2.0 }
                  ]
                }
              ]
            }
          ]
        }
        """.trimIndent()

        val docObj = JsonParser.parseString(json).asJsonObject
        val groups = AndroidClipRenderer.parseOverlayGroups(docObj)

        assertEquals(1, groups.size)
        val g = groups[0]
        assertEquals("Anton", g.fontFamily)
        assertEquals("900", g.fontWeight)
        assertEquals("uppercase", g.textTransform)
        assertEquals(0.85f, g.maxWidth, 0.001f)
        assertEquals("line", g.backgroundMode)
        assertEquals(20f, g.backgroundCornerRadius, 0.001f)
        assertEquals(30f, g.backgroundPaddingX, 0.001f)
        assertEquals(18f, g.backgroundPaddingY, 0.001f)
        assertEquals("bounce", g.activeAnimType)
        assertEquals(140, g.activeAnimDurationMs)
        assertEquals(1.2f, g.activeScaleMax, 0.001f)
    }

    @Test
    fun remapOverlayGroupsToTimeline_multiSegment_shiftsSurvivingWordsAndDropsGaps() {
        val w1 = AndroidClipRenderer.CaptionWord("PieceOne", 1.0f, 2.5f)
        val g1 = AndroidClipRenderer.CaptionGroup(id = "g1", start = 1.0f, end = 2.5f, words = listOf(w1))

        val w2 = AndroidClipRenderer.CaptionWord("DeletedGap", 7.0f, 8.5f)
        val g2 = AndroidClipRenderer.CaptionGroup(id = "g2", start = 7.0f, end = 8.5f, words = listOf(w2))

        val w3 = AndroidClipRenderer.CaptionWord("PieceTwo", 16.0f, 17.5f)
        val g3 = AndroidClipRenderer.CaptionGroup(id = "g3", start = 16.0f, end = 17.5f, words = listOf(w3))

        val groups = listOf(g1, g2, g3)
        // Segment 0: 10.0s - 15.0s (dur 5.0s = 5000ms)
        // Segment 1: 25.0s - 30.0s (dur 5.0s = 5000ms)
        val spans = listOf(Pair(10000L, 15000L), Pair(25000L, 30000L))
        val clipStartSec = 10.0f

        val remapped = AndroidClipRenderer.remapOverlayGroupsToTimeline(groups, spans, clipStartSec)

        assertEquals(2, remapped.size)
        // Group 1
        assertEquals("g1", remapped[0].id)
        assertEquals("PieceOne", remapped[0].words[0].text)
        assertEquals(1.0f, remapped[0].start, 0.01f)
        assertEquals(2.5f, remapped[0].end, 0.01f)

        // Group 3: shifted to 6.0s (5.0s offset + 1.0s into segment 1)
        assertEquals("g3", remapped[1].id)
        assertEquals("PieceTwo", remapped[1].words[0].text)
        assertEquals(6.0f, remapped[1].start, 0.01f)
        assertEquals(7.5f, remapped[1].end, 0.01f)
    }

    @Test
    fun computeLayoutPlan_fitBlurUsesDualLayerBlurredBackdrop_bothAspectDirections() {
        val portraitTo16x9 = AndroidClipRenderer.computeLayoutPlan(
            layoutMode = "fit_blur",
            targetResolution = "1080p",
            srcWidth = 1080,
            srcHeight = 1920,
            rotationDegrees = 0
        )
        assertEquals(1920, portraitTo16x9.outputWidth)
        assertEquals(1080, portraitTo16x9.outputHeight)
        assertEquals(AndroidClipRenderer.Companion.LayoutStrategy.FIT_BLUR, portraitTo16x9.strategy)
        assertEquals(1080, portraitTo16x9.fgHeight)
        assertTrue("Expected fitted foreground width < 1920, got ${portraitTo16x9.fgWidth}", portraitTo16x9.fgWidth in 600..616)

        val landscapeTo9x16 = AndroidClipRenderer.computeLayoutPlan(
            layoutMode = "fit_blur",
            targetResolution = "1080p",
            srcWidth = 1920,
            srcHeight = 1080,
            rotationDegrees = 0
        )
        assertEquals(1080, landscapeTo9x16.outputWidth)
        assertEquals(1920, landscapeTo9x16.outputHeight)
        assertEquals(AndroidClipRenderer.Companion.LayoutStrategy.FIT_BLUR, landscapeTo9x16.strategy)
        assertEquals(1080, landscapeTo9x16.fgWidth)
        assertTrue("Expected fitted foreground height < 1920, got ${landscapeTo9x16.fgHeight}", landscapeTo9x16.fgHeight in 600..616)

        val group = AndroidClipRenderer.CaptionGroup("g1", 0.5f, 1.5f, listOf(AndroidClipRenderer.CaptionWord("Hi", 0.5f, 1.5f)))
        val compPlan = AndroidClipRenderer.planComposition(
            layoutPlan = portraitTo16x9,
            segmentSpans = listOf(Pair(0L, 5000L)),
            overlayGroups = listOf(group)
        )
        assertEquals(2, compPlan.sequenceCount)
        assertTrue(compPlan.hasBlurredBackdropSequence)
        assertTrue(compPlan.backgroundAudioRemoved)
        assertTrue(compPlan.hasCompositionOverlayEffect)
    }

    @Test
    fun computeLayoutPlan_native169_handlesPortraitAndRotatedInputsWithoutClippingOrSquish() {
        val portraitPlan = AndroidClipRenderer.computeLayoutPlan(
            layoutMode = "native_169",
            targetResolution = "1080p",
            srcWidth = 1080,
            srcHeight = 1920,
            rotationDegrees = 0
        )
        assertEquals(1920, portraitPlan.outputWidth)
        assertEquals(1080, portraitPlan.outputHeight)
        assertEquals(AndroidClipRenderer.Companion.LayoutStrategy.FIT_BLACK, portraitPlan.strategy)
        assertEquals(null, portraitPlan.cropBounds)
        assertEquals(1080, portraitPlan.fgHeight)
        assertTrue("Expected 9:16 pillarboxed fgWidth ~608, got ${portraitPlan.fgWidth}", portraitPlan.fgWidth in 606..610)

        // Raw coded size 1920x1080 with 90deg EXIF/codec rotation is upright 1080x1920 portrait!
        val rotatedPortraitPlan = AndroidClipRenderer.computeLayoutPlan(
            layoutMode = "native_169",
            targetResolution = "1080p",
            srcWidth = 1920,
            srcHeight = 1080,
            rotationDegrees = 90
        )
        assertEquals(1080, rotatedPortraitPlan.uprightSourceWidth)
        assertEquals(1920, rotatedPortraitPlan.uprightSourceHeight)
        assertEquals(1080, rotatedPortraitPlan.fgHeight)
        assertTrue(rotatedPortraitPlan.fgWidth in 606..610)
    }

    @Test
    fun remapOverlayGroupsToTimeline_singlePieceEdl_respectsSegmentStartAndEnd() {
        val trimmedOut = AndroidClipRenderer.CaptionWord("Before", 0.5f, 1.5f) // 10.5..11.5s source -> cut!
        val kept = AndroidClipRenderer.CaptionWord("Inside", 3.0f, 4.0f) // 13.0..14.0s source -> kept!
        val after = AndroidClipRenderer.CaptionWord("After", 7.0f, 8.0f) // 17.0..18.0s source -> cut!

        val groups = listOf(
            AndroidClipRenderer.CaptionGroup("g0", 0.5f, 1.5f, listOf(trimmedOut)),
            AndroidClipRenderer.CaptionGroup("g1", 3.0f, 4.0f, listOf(kept)),
            AndroidClipRenderer.CaptionGroup("g2", 7.0f, 8.0f, listOf(after))
        )
        // Single-piece EDL [12.0s, 16.0s] while clip origin was 10.0s
        val spans = listOf(Pair(12000L, 16000L))
        val remapped = AndroidClipRenderer.remapOverlayGroupsToTimeline(groups, spans, clipStartSec = 10.0f)

        assertEquals(1, remapped.size)
        assertEquals("Inside", remapped[0].words[0].text)
        assertEquals(1.0f, remapped[0].start, 0.01f)
        assertEquals(2.0f, remapped[0].end, 0.01f)
    }

    @Test
    fun parseOverlayGroups_andRemap_sourceRelativeDoesNotDoubleOffset() {
        val json = """
        {
          "coordinate_system": "source_relative",
          "timeline": {
            "segments": [
              { "source_start": 10.0, "source_end": 15.0 },
              { "source_start": 25.0, "source_end": 30.0 }
            ]
          },
          "tracks": [
            {
              "id": "captions",
              "kind": "captions",
              "groups": [
                {
                  "id": "cap_src",
                  "start": 26.0,
                  "end": 27.5,
                  "coordinate_system": "source_relative",
                  "words": [
                    { "text": "SourceWord", "start": 26.0, "end": 27.5 }
                  ]
                }
              ]
            }
          ]
        }
        """.trimIndent()

        val docObj = JsonParser.parseString(json).asJsonObject
        val groups = AndroidClipRenderer.parseOverlayGroups(docObj)
        assertEquals(1, groups.size)
        assertEquals("source_relative", groups[0].coordinateSystem)

        val spans = AndroidClipRenderer.parseSegmentSpans(docObj)
        assertEquals(10.0f, AndroidClipRenderer.resolveCaptionOriginSec(docObj, spans), 0.001f)

        // clipStartSec=10.0f MUST NOT be added again onto source_relative timestamps (26.0..27.5s)
        val remapped = AndroidClipRenderer.remapOverlayGroupsToTimeline(groups, spans, clipStartSec = 10.0f)
        assertEquals(1, remapped.size)
        assertEquals("SourceWord", remapped[0].words[0].text)
        assertEquals(6.0f, remapped[0].start, 0.01f)
        assertEquals(7.5f, remapped[0].end, 0.01f)
    }

    @Test
    fun parseOverlayGroups_matchesPythonFrontendPresetDefaultsAndNoneAnimationScale() {
        val json = """
        {
          "tracks": [
            {
              "id": "captions",
              "kind": "captions",
              "groups": [
                {
                  "id": "cap_defaults",
                  "start": 1.0,
                  "end": 2.0,
                  "style": {
                    "animation": { "mode": "none" }
                  },
                  "words": [
                    { "text": "Clean", "start": 1.0, "end": 2.0 }
                  ]
                }
              ]
            }
          ]
        }
        """.trimIndent()

        val docObj = JsonParser.parseString(json).asJsonObject
        val groups = AndroidClipRenderer.parseOverlayGroups(docObj)
        assertEquals(1, groups.size)
        val g = groups[0]
        assertFalse(g.backgroundEnabled)
        assertTrue(g.strokeEnabled)
        assertEquals("none", g.backgroundMode)
        assertEquals("none", g.activeAnimType)
        assertEquals(1.0f, g.activeScaleMax, 0.001f)

        val noAnimTransform = AndroidClipRenderer.computeActiveWordTransform(
            isWordActive = true,
            activeAnimType = g.activeAnimType,
            activeAnimDurationMs = g.activeAnimDurationMs,
            activeScaleMax = g.activeScaleMax,
            elapsedSec = 0.02f,
            canvasScale = 1.0f
        )
        assertEquals(1.0f, noAnimTransform.scale, 0.0001f)
        assertEquals(0.0f, noAnimTransform.translateY, 0.0001f)

        val popTransform = AndroidClipRenderer.computeActiveWordTransform(
            isWordActive = true,
            activeAnimType = "pop",
            activeAnimDurationMs = 120,
            activeScaleMax = 1.15f,
            elapsedSec = 0.01f,
            canvasScale = 1.0f
        )
        assertTrue(popTransform.scale > 1.05f)
    }
}

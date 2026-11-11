package com.resolvia.dispatch.timeline

import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import java.io.File

class TimelineMapperTest {

    companion object {
        private lateinit var fixtures: JsonObject

        private fun resolveFixturesFile(): File {
            var dir: File? = File(".").absoluteFile
            while (dir != null) {
                val candidate = File(dir, "tests/fixtures/fingerprints_v1.json")
                if (candidate.isFile) {
                    return candidate
                }
                dir = dir.parentFile
            }
            throw IllegalStateException("Could not locate tests/fixtures/fingerprints_v1.json from ${File(".").absolutePath}")
        }

        @BeforeClass
        @JvmStatic
        fun loadFixtures() {
            val file = resolveFixturesFile()
            val text = file.readText(Charsets.UTF_8)
            fixtures = JsonParser.parseString(text).asJsonObject
        }

        private fun parseSegments(arr: JsonArray): List<TimelineSegment> {
            return arr.mapIndexed { idx, el ->
                if (el.isJsonArray) {
                    val pair = el.asJsonArray
                    TimelineSegment.of(pair[0].asDouble, pair[1].asDouble, "seg-${idx + 1}")
                } else {
                    val obj = el.asJsonObject
                    val start = (obj.get("source_start") ?: obj.get("start")).asDouble
                    val end = (obj.get("source_end") ?: obj.get("end")).asDouble
                    val id = obj.get("id")?.asString ?: "seg-${idx + 1}"
                    TimelineSegment.of(start, end, id)
                }
            }
        }

        private fun jsonToKotlin(el: JsonElement): Any? {
            return when {
                el.isJsonNull -> null
                el.isJsonPrimitive -> {
                    val p = el.asJsonPrimitive
                    when {
                        p.isBoolean -> p.asBoolean
                        p.isString -> p.asString
                        p.isNumber -> p.asDouble
                        else -> null
                    }
                }
                el.isJsonArray -> el.asJsonArray.map { jsonToKotlin(it) }
                el.isJsonObject -> el.asJsonObject.entrySet().associate { (k, v) -> k to jsonToKotlin(v) }
                else -> null
            }
        }
    }

    @Test
    fun roundMs_matchesAllGoldenVectors() {
        val vectors = fixtures.getAsJsonArray("round_ms_vectors")
        for (item in vectors) {
            val obj = item.asJsonObject
            val input = obj.get("input").asDouble
            val expected = obj.get("expected").asDouble
            assertEquals("roundMs($input)", expected, TimelineMapper.roundMs(input), 1e-6)
        }
        assertEquals(0.0, TimelineMapper.roundMs(-0.0), 0.0)
        assertEquals("+0.0", 0.0.toBits(), TimelineMapper.roundMs(-0.0001).toBits())
    }

    @Test
    fun canonicalSpansAndFingerprints_matchV1GoldenVectors() {
        val spanVectors = fixtures.getAsJsonArray("canonical_spans_vectors")
        for (item in spanVectors) {
            val obj = item.asJsonObject
            val name = obj.get("name").asString
            val ranges = parseSegments(obj.getAsJsonArray("ranges"))
            val expectedStr = obj.get("expected_str").asString
            val expectedSpans = obj.getAsJsonArray("expected_spans").map {
                val p = it.asJsonArray
                p[0].asDouble to p[1].asDouble
            }
            assertEquals("$name spans", expectedSpans, TimelineMapper.canonicalMediaSpans(ranges))
            assertEquals("$name str", expectedStr, TimelineMapper.canonicalSpansStr(ranges))
        }

        val fpVectors = fixtures.getAsJsonArray("fingerprint_vectors")
        for (item in fpVectors) {
            val obj = item.asJsonObject
            val name = obj.get("name").asString
            val sourceAssetId = obj.get("source_asset_id").asString
            val ranges = parseSegments(obj.getAsJsonArray("ranges"))
            val layoutMode = obj.get("layout_mode").asString
            val cropAnchorX = obj.get("crop_anchor_x").asDouble
            val layers = obj.getAsJsonArray("layers")

            val actualMediaFp = TimelineMapper.computeMediaFingerprint(sourceAssetId, ranges)
            assertEquals("$name media_fp", obj.get("expected_media_fingerprint").asString, actualMediaFp)

            val actualRenderFp = TimelineMapper.computeRenderFingerprintFromJson(
                actualMediaFp,
                layoutMode,
                cropAnchorX,
                layers,
                null
            )
            assertEquals("$name render_fp", obj.get("expected_render_fingerprint").asString, actualRenderFp)
        }
    }

    @Test
    fun sourceToConcat_matchesGoldenVectorsAndNeverSilentlySnapsGaps() {
        val vec = fixtures.getAsJsonObject("timeline_mapper_vectors")
        val segments = parseSegments(vec.getAsJsonArray("segments"))
        assertEquals(vec.get("expected_kept_duration").asDouble, TimelineMapper.keptDuration(segments), 1e-6)

        for (item in vec.getAsJsonArray("source_to_concat_cases")) {
            val c = item.asJsonObject
            val name = c.get("name").asString
            val src = SourceTime(c.get("source_time").asDouble)
            val exp = c.getAsJsonObject("expected")
            val res = TimelineMapper.sourceToConcat(src, segments)

            if (exp.get("kind").asString == "ok") {
                assertTrue("$name expected Ok", res is SourceToConcatResult.Ok)
                val ok = res as SourceToConcatResult.Ok
                assertEquals("$name time", exp.get("time").asDouble, ok.time.seconds, 1e-6)
                assertEquals("$name segmentIndex", exp.get("segment_index").asInt, ok.segmentIndex)
                assertFalse("$name snapped", ok.snapped)
            } else {
                assertTrue("$name expected Gap", res is SourceToConcatResult.Gap)
                val gap = res as SourceToConcatResult.Gap
                assertEquals("$name reason", exp.get("reason").asString, gap.reason)
                assertEquals("$name nearestConcat", exp.get("nearest_concat_time").asDouble, gap.nearestConcatTime.seconds, 1e-6)
                val expStart = exp.get("nearest_segment_start")
                if (expStart.isJsonNull) {
                    assertNull("$name nearestStart", gap.nearestSegmentStart)
                } else {
                    assertEquals("$name nearestStart", expStart.asDouble, gap.nearestSegmentStart!!.seconds, 1e-6)
                }
                val expEnd = exp.get("nearest_segment_end")
                if (expEnd.isJsonNull) {
                    assertNull("$name nearestEnd", gap.nearestSegmentEnd)
                } else {
                    assertEquals("$name nearestEnd", expEnd.asDouble, gap.nearestSegmentEnd!!.seconds, 1e-6)
                }
            }
        }
    }

    @Test
    fun sourceToConcat_explicitSnapOptInWorks() {
        val vec = fixtures.getAsJsonObject("timeline_mapper_vectors")
        val segments = parseSegments(vec.getAsJsonArray("segments"))
        for (item in vec.getAsJsonArray("snap_gap_opt_in_cases")) {
            val c = item.asJsonObject
            val name = c.get("name").asString
            val mode = SnapGapMode.valueOf(c.get("snap_gap_to").asString.uppercase())
            val res = TimelineMapper.sourceToConcat(SourceTime(c.get("source_time").asDouble), segments, mode)
            assertTrue("$name expected Ok", res is SourceToConcatResult.Ok)
            val ok = res as SourceToConcatResult.Ok
            assertTrue(ok.snapped)
            assertEquals(c.get("expected_time").asDouble, ok.time.seconds, 1e-6)
            assertEquals(c.get("expected_segment_index").asInt, ok.segmentIndex)
        }
    }

    @Test
    fun concatToSource_andRoundtripInvertibility() {
        val vec = fixtures.getAsJsonObject("timeline_mapper_vectors")
        val segments = parseSegments(vec.getAsJsonArray("segments"))

        for (item in vec.getAsJsonArray("concat_to_source_cases")) {
            val c = item.asJsonObject
            val name = c.get("name").asString
            val ct = ConcatTime(c.get("concat_time").asDouble)
            val exp = c.getAsJsonObject("expected")
            val res = TimelineMapper.concatToSource(ct, segments)

            if (exp.get("kind").asString == "ok") {
                assertTrue("$name expected Ok", res is ConcatToSourceResult.Ok)
                val ok = res as ConcatToSourceResult.Ok
                assertEquals(exp.get("time").asDouble, ok.time.seconds, 1e-6)
                assertEquals(exp.get("segment_index").asInt, ok.segmentIndex)
                assertFalse(ok.clamped)
            } else {
                assertTrue("$name expected OutOfRange", res is ConcatToSourceResult.OutOfRange)
                val oor = res as ConcatToSourceResult.OutOfRange
                assertEquals(exp.get("reason").asString, oor.reason)
                assertEquals(exp.get("clamped_time").asDouble, oor.clampedTime.seconds, 1e-6)

                val clamped = TimelineMapper.concatToSource(ct, segments, clamp = true)
                assertTrue(clamped is ConcatToSourceResult.Ok)
                val clampedOk = clamped as ConcatToSourceResult.Ok
                assertTrue(clampedOk.clamped)
                assertEquals(exp.get("clamped_time").asDouble, clampedOk.time.seconds, 1e-6)
            }
        }

        for (pt in vec.getAsJsonArray("roundtrip_concat_points")) {
            val cVal = pt.asDouble
            val srcRes = TimelineMapper.concatToSource(ConcatTime(cVal), segments)
            assertTrue(srcRes is ConcatToSourceResult.Ok)
            val backRes = TimelineMapper.sourceToConcat((srcRes as ConcatToSourceResult.Ok).time, segments)
            assertTrue(backRes is SourceToConcatResult.Ok)
            assertEquals("roundtrip($cVal)", cVal, (backRes as SourceToConcatResult.Ok).time.seconds, 1e-6)
        }
    }

    @Test
    fun editWindow_andC2C3BoundsChecks() {
        val ew = fixtures.getAsJsonObject("timeline_mapper_vectors").getAsJsonObject("edit_window_cases")
        val ws = SourceTime(ew.get("window_start").asDouble)
        val we = SourceTime(ew.get("window_end").asDouble)

        for (item in ew.getAsJsonArray("source_to_proxy")) {
            val c = item.asJsonObject
            val exp = c.getAsJsonObject("expected")
            val res = TimelineMapper.sourceToProxy(SourceTime(c.get("source_time").asDouble), ws, we)
            if (exp.get("kind").asString == "ok") {
                assertTrue(res is WindowMappingResult.Ok)
                assertEquals(exp.get("time").asDouble, (res as WindowMappingResult.Ok).time.seconds, 1e-6)
            } else {
                assertTrue(res is WindowMappingResult.OutOfWindow)
                val oow = res as WindowMappingResult.OutOfWindow
                assertEquals(exp.get("reason").asString, oow.reason)
                assertEquals(exp.get("clamped_time").asDouble, oow.clampedTime.seconds, 1e-6)
            }
        }

        for (item in ew.getAsJsonArray("proxy_to_source")) {
            val c = item.asJsonObject
            val exp = c.getAsJsonObject("expected")
            val res = TimelineMapper.proxyToSource(ProxyTime(c.get("proxy_time").asDouble), ws, we)
            if (exp.get("kind").asString == "ok") {
                assertTrue(res is WindowMappingResult.Ok)
                assertEquals(exp.get("time").asDouble, (res as WindowMappingResult.Ok).time.seconds, 1e-6)
            } else {
                assertTrue(res is WindowMappingResult.OutOfWindow)
                val oow = res as WindowMappingResult.OutOfWindow
                assertEquals(exp.get("reason").asString, oow.reason)
                assertEquals(exp.get("clamped_time").asDouble, oow.clampedTime.seconds, 1e-6)
            }
        }

        val c3 = TimelineMapper.c2ToC3Time(ProxyTime(14.2), ws, we)
        assertEquals(714.7, c3.seconds, 1e-6)
        assertEquals(14.2, TimelineMapper.c3ToC2Time(c3, ws, we).seconds, 1e-6)

        assertThrowsIllegalArg { TimelineMapper.c2ToC3Time(ProxyTime(-0.1), ws, we) }
        assertThrowsIllegalArg { TimelineMapper.c2ToC3Time(ProxyTime(241.0), ws, we) }
        assertThrowsIllegalArg { TimelineMapper.c3ToC2Time(SourceTime(700.0), ws, we) }
        assertThrowsIllegalArg { TimelineMapper.c3ToC2Time(SourceTime(941.0), ws, we) }
    }

    @Test
    fun playbackDescriptor_roundtripsAndRejectsInvalidBounds() {
        val cases = fixtures.getAsJsonObject("timeline_mapper_vectors").getAsJsonArray("playback_descriptor_cases")
        for (item in cases) {
            val c = item.asJsonObject
            val dObj = c.getAsJsonObject("descriptor")
            val descriptor: PlaybackDescriptor = when (dObj.get("kind").asString) {
                "source_video" -> PlaybackDescriptor.SourceVideo(dObj.get("sourceDuration")?.asDouble)
                "edit_window" -> PlaybackDescriptor.EditWindow(
                    SourceTime(dObj.get("windowStart").asDouble),
                    SourceTime(dObj.get("windowEnd").asDouble)
                )
                "rendered_clip" -> PlaybackDescriptor.RenderedClip(parseSegments(dObj.getAsJsonArray("segments")))
                else -> throw IllegalArgumentException("Unknown descriptor")
            }

            val srcTime = SourceTime(c.get("source_time").asDouble)
            val res = TimelineMapper.sourceToPlaybackTime(srcTime, descriptor)
            if (c.get("expected_kind").asString == "ok") {
                assertTrue(res is PlaybackMappingResult.Ok)
                val ok = res as PlaybackMappingResult.Ok
                assertEquals(c.get("expected_playback_time").asDouble, ok.timeSeconds, 1e-6)

                val rev = TimelineMapper.playbackTimeToSource(ok.timeSeconds, descriptor)
                assertTrue(rev is PlaybackMappingResult.Ok)
                assertEquals(srcTime.seconds, (rev as PlaybackMappingResult.Ok).timeSeconds, 1e-6)
            } else {
                assertTrue(res is PlaybackMappingResult.Gap)
            }
        }
    }

    @Test
    fun rfc8785CanonicalizerAndRenderFingerprint_matchGoldenVectors() {
        val vectors = fixtures.getAsJsonArray("rfc8785_fingerprint_vectors")
        for (item in vectors) {
            val obj = item.asJsonObject
            val name = obj.get("name").asString
            val layersArr = obj.getAsJsonArray("layers")
            val settingsObj = obj.getAsJsonObject("settings")

            assertEquals(
                "$name layers JCS (Gson)",
                obj.get("expected_canonical_layers_json").asString,
                TimelineMapper.canonicalJsonStringify(layersArr)
            )
            assertEquals(
                "$name settings JCS (Gson)",
                obj.get("expected_canonical_settings_json").asString,
                TimelineMapper.canonicalJsonStringify(settingsObj)
            )

            @Suppress("UNCHECKED_CAST")
            val layersKotlin = jsonToKotlin(layersArr) as List<Any?>
            @Suppress("UNCHECKED_CAST")
            val settingsKotlin = jsonToKotlin(settingsObj) as Map<String, Any?>
            assertEquals(
                "$name layers JCS (Kotlin)",
                obj.get("expected_canonical_layers_json").asString,
                TimelineMapper.canonicalJsonStringify(layersKotlin)
            )
            assertEquals(
                "$name settings JCS (Kotlin)",
                obj.get("expected_canonical_settings_json").asString,
                TimelineMapper.canonicalJsonStringify(settingsKotlin)
            )

            val ranges = parseSegments(obj.getAsJsonArray("ranges"))
            val mfp = TimelineMapper.computeMediaFingerprint(obj.get("source_asset_id").asString, ranges)
            assertEquals("$name media_fp", obj.get("expected_media_fingerprint").asString, mfp)

            val rfpFromJson = TimelineMapper.computeRenderFingerprintFromJson(
                mfp,
                obj.get("layout_mode").asString,
                obj.get("crop_anchor_x").asDouble,
                layersArr,
                settingsObj
            )
            assertEquals("$name render_fp (Json)", obj.get("expected_render_fingerprint").asString, rfpFromJson)

            val rfpFromKotlin = TimelineMapper.computeRenderFingerprint(
                mfp,
                obj.get("layout_mode").asString,
                obj.get("crop_anchor_x").asDouble,
                layersKotlin,
                settingsKotlin
            )
            assertEquals("$name render_fp (Kotlin)", obj.get("expected_render_fingerprint").asString, rfpFromKotlin)
        }

        // org.json parity + negative zero + float normalization
        val orgObj = JSONObject().put("b", -0.0).put("a", 1.0).put("arr", JSONArray().put(1.0).put(-0.0))
        assertEquals("{\"a\":1,\"arr\":[1,0],\"b\":0}", TimelineMapper.canonicalJsonStringify(orgObj))

        assertThrowsIllegalArg { TimelineMapper.canonicalJsonStringify(mapOf("bad" to Double.NaN)) }
        assertThrowsIllegalArg { TimelineMapper.canonicalJsonStringify(mapOf("bad" to Double.POSITIVE_INFINITY)) }
    }

    @Test
    fun adjacentSegments_arePreservedWithoutMerging() {
        val segs = TimelineMapper.normalizeSegments(
            listOf(TimelineSegment.of(10.0, 20.0), TimelineSegment.of(20.0, 30.0))
        )
        assertEquals(2, segs.size)
        val seam = TimelineMapper.concatToSource(ConcatTime(10.0), segs)
        assertTrue(seam is ConcatToSourceResult.Ok)
        val ok = seam as ConcatToSourceResult.Ok
        assertEquals(1, ok.segmentIndex)
        assertEquals(20.0, ok.time.seconds, 1e-6)
    }

    private fun assertThrowsIllegalArg(block: () -> Unit) {
        var threw = false
        try {
            block()
        } catch (_: IllegalArgumentException) {
            threw = true
        }
        assertTrue("Expected IllegalArgumentException", threw)
    }
}

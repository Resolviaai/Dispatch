package com.resolvia.dispatch.recorder

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CameraZoomMapperTest {

    @Test
    fun testWideLensMapping_places1XAtMidpoint() {
        val minZoom = 0.6f
        val maxZoom = 5.0f

        // 1.0x should map to visual midpoint (0.5f)
        val fractionAt1x = CameraZoomMapper.ratioToFraction(1.0f, minZoom, maxZoom)
        assertEquals(0.5f, fractionAt1x, 0.001f)

        // Midpoint fraction (0.5f) should convert back to exactly 1.0x
        val ratioAtMidpoint = CameraZoomMapper.fractionToRatio(0.5f, minZoom, maxZoom)
        assertEquals(1.0f, ratioAtMidpoint, 0.001f)

        // Min zoom maps to 0.0f
        assertEquals(0.0f, CameraZoomMapper.ratioToFraction(minZoom, minZoom, maxZoom), 0.001f)
        assertEquals(minZoom, CameraZoomMapper.fractionToRatio(0.0f, minZoom, maxZoom), 0.001f)

        // Max zoom maps to 1.0f
        assertEquals(1.0f, CameraZoomMapper.ratioToFraction(maxZoom, minZoom, maxZoom), 0.001f)
        assertEquals(maxZoom, CameraZoomMapper.fractionToRatio(1.0f, minZoom, maxZoom), 0.001f)

        // Tick fraction is at 0.5f
        assertEquals(0.5f, CameraZoomMapper.getTickFraction(minZoom, maxZoom)!!, 0.001f)
    }

    @Test
    fun testStandardLensMapping_linearRangeFrom1X() {
        val minZoom = 1.0f
        val maxZoom = 8.0f

        // 1.0x maps to 0.0f (far left)
        assertEquals(0.0f, CameraZoomMapper.ratioToFraction(1.0f, minZoom, maxZoom), 0.001f)
        assertEquals(1.0f, CameraZoomMapper.fractionToRatio(0.0f, minZoom, maxZoom), 0.001f)

        // Midpoint fraction 0.5f maps to 4.5x
        assertEquals(4.5f, CameraZoomMapper.fractionToRatio(0.5f, minZoom, maxZoom), 0.001f)
        assertEquals(0.5f, CameraZoomMapper.ratioToFraction(4.5f, minZoom, maxZoom), 0.001f)

        // Max zoom maps to 1.0f
        assertEquals(1.0f, CameraZoomMapper.ratioToFraction(8.0f, minZoom, maxZoom), 0.001f)
        assertEquals(8.0f, CameraZoomMapper.fractionToRatio(1.0f, minZoom, maxZoom), 0.001f)

        // No midpoint tick for 1.0x when starting at 1.0x
        assertNull(CameraZoomMapper.getTickFraction(minZoom, maxZoom))
    }

    @Test
    fun testExactInvertibility_acrossSamples() {
        // Test wide lens invertibility
        val wideMin = 0.5f
        val wideMax = 6.0f
        for (i in 0..100) {
            val fraction = i / 100.0f
            val ratio = CameraZoomMapper.fractionToRatio(fraction, wideMin, wideMax)
            val recoveredFraction = CameraZoomMapper.ratioToFraction(ratio, wideMin, wideMax)
            assertEquals("Wide lens invertibility failed at fraction $fraction", fraction, recoveredFraction, 0.001f)
        }

        // Test standard lens invertibility
        val stdMin = 1.0f
        val stdMax = 10.0f
        for (i in 0..100) {
            val fraction = i / 100.0f
            val ratio = CameraZoomMapper.fractionToRatio(fraction, stdMin, stdMax)
            val recoveredFraction = CameraZoomMapper.ratioToFraction(ratio, stdMin, stdMax)
            assertEquals("Standard lens invertibility failed at fraction $fraction", fraction, recoveredFraction, 0.001f)
        }
    }

    @Test
    fun testClamping_outOfBoundsValues() {
        val minZoom = 1.0f
        val maxZoom = 5.0f

        // Ratios beyond range are clamped
        assertEquals(0.0f, CameraZoomMapper.ratioToFraction(0.1f, minZoom, maxZoom), 0.001f)
        assertEquals(1.0f, CameraZoomMapper.ratioToFraction(15.0f, minZoom, maxZoom), 0.001f)

        // Fractions beyond [0..1] are clamped
        assertEquals(minZoom, CameraZoomMapper.fractionToRatio(-0.5f, minZoom, maxZoom), 0.001f)
        assertEquals(maxZoom, CameraZoomMapper.fractionToRatio(1.5f, minZoom, maxZoom), 0.001f)
    }

    @Test
    fun testStepRatio() {
        val minZoom = 1.0f
        val maxZoom = 5.0f

        // Step up
        assertEquals(1.2f, CameraZoomMapper.stepRatio(1.0f, 0.2f, minZoom, maxZoom), 0.001f)
        // Step down
        assertEquals(1.0f, CameraZoomMapper.stepRatio(1.2f, -0.2f, minZoom, maxZoom), 0.001f)
        // Step down below min clamps to min
        assertEquals(1.0f, CameraZoomMapper.stepRatio(1.0f, -0.5f, minZoom, maxZoom), 0.001f)
        // Step up above max clamps to max
        assertEquals(5.0f, CameraZoomMapper.stepRatio(4.9f, 0.5f, minZoom, maxZoom), 0.001f)
    }

    @Test
    fun testDegenerateCameraRange() {
        // Fixed-ratio camera where max <= min
        val minZoom = 1.0f
        val maxZoom = 1.0f

        assertEquals(0.0f, CameraZoomMapper.ratioToFraction(1.0f, minZoom, maxZoom), 0.001f)
        assertEquals(1.0f, CameraZoomMapper.fractionToRatio(0.5f, minZoom, maxZoom), 0.001f)
        assertNull(CameraZoomMapper.getTickFraction(minZoom, maxZoom))
    }
}

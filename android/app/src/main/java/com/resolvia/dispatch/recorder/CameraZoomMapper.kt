package com.resolvia.dispatch.recorder

/**
 * Maps between continuous camera zoom ratios and normalized slider fractions [0.0f..1.0f].
 *
 * Design Invariants:
 * 1. Truthful Camera Range: Always uses the active camera's reported [minZoom] and [maxZoom].
 * 2. Visual Midpoint for Wide Lenses: If [minZoom] < 1.0f and [maxZoom] > 1.0f (e.g. 0.6x to 5.0x),
 *    1.0x is mapped to [PIVOT_FRACTION] (0.5f), with wide zoom (<1.0x) on the left half and
 *    telephoto (>1.0x) on the right half.
 * 3. Range-Bounded for Standard Lenses: If [minZoom] >= 1.0f (e.g. 1.0x to 10.0x),
 *    the slider maps [minZoom..maxZoom] directly to [0.0..1.0], never pretending wide zoom exists.
 * 4. Exact Invertibility: [fractionToRatio] is the exact, continuous mathematical inverse of [ratioToFraction].
 */
object CameraZoomMapper {
    const val PIVOT_FRACTION = 0.5f

    /**
     * Converts a zoom ratio into a normalized slider fraction [0.0f..1.0f].
     */
    fun ratioToFraction(ratio: Float, minZoom: Float, maxZoom: Float): Float {
        if (maxZoom <= minZoom) return 0f
        val clamped = ratio.coerceIn(minZoom, maxZoom)

        return if (minZoom < 1.0f && maxZoom > 1.0f) {
            if (clamped <= 1.0f) {
                ((clamped - minZoom) / (1.0f - minZoom)) * PIVOT_FRACTION
            } else {
                PIVOT_FRACTION + ((clamped - 1.0f) / (maxZoom - 1.0f)) * (1.0f - PIVOT_FRACTION)
            }
        } else {
            (clamped - minZoom) / (maxZoom - minZoom)
        }.coerceIn(0f, 1f)
    }

    /**
     * Converts a normalized slider fraction [0.0f..1.0f] into a camera zoom ratio.
     */
    fun fractionToRatio(fraction: Float, minZoom: Float, maxZoom: Float): Float {
        if (maxZoom <= minZoom) return minZoom
        val f = fraction.coerceIn(0f, 1f)

        val ratio = if (minZoom < 1.0f && maxZoom > 1.0f) {
            if (f <= PIVOT_FRACTION) {
                minZoom + (f / PIVOT_FRACTION) * (1.0f - minZoom)
            } else {
                1.0f + ((f - PIVOT_FRACTION) / (1.0f - PIVOT_FRACTION)) * (maxZoom - 1.0f)
            }
        } else {
            minZoom + f * (maxZoom - minZoom)
        }
        return ratio.coerceIn(minZoom, maxZoom)
    }

    /**
     * Returns the slider fraction where the 1.0x reference tick mark should be drawn,
     * or null if 1.0x is outside the camera range or at the very start (minZoom >= 1.0f).
     */
    fun getTickFraction(minZoom: Float, maxZoom: Float): Float? {
        if (minZoom < 1.0f && maxZoom > 1.0f) {
            return PIVOT_FRACTION
        }
        return null
    }

    /**
     * Steps the zoom ratio by [delta] (positive for +, negative for -), clamped to the camera's supported range.
     */
    fun stepRatio(currentRatio: Float, delta: Float, minZoom: Float, maxZoom: Float): Float {
        if (maxZoom <= minZoom) return minZoom
        val target = (currentRatio + delta)
        return target.coerceIn(minZoom, maxZoom)
    }
}

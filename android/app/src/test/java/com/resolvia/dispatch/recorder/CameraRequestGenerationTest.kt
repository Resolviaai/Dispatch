package com.resolvia.dispatch.recorder

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CameraRequestGenerationTest {
    @Test
    fun newerRequestMakesOlderCameraFutureStale() {
        val requests = CameraRequestGeneration()
        val first = requests.begin()
        val second = requests.begin()

        assertFalse(requests.isCurrent(first))
        assertTrue(requests.isCurrent(second))
    }

    @Test
    fun lifecycleInvalidatesPendingCameraFuture() {
        val requests = CameraRequestGeneration()
        val request = requests.begin()

        requests.invalidate()

        assertFalse(requests.isCurrent(request))
    }
}

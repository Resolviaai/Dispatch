package com.resolvia.dispatch.recorder

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CameraCaptureManagerLockTest {
    @Test
    fun aeOnlySupportConfirmsLockWithoutAutofocusSuccess() {
        assertTrue(isAeAfLockConfirmed(hasAf = false, hasAe = true, focusSuccessful = false))
    }

    @Test
    fun autofocusOnlySupportRequiresSuccessfulFocus() {
        assertTrue(isAeAfLockConfirmed(hasAf = true, hasAe = false, focusSuccessful = true))
        assertFalse(isAeAfLockConfirmed(hasAf = true, hasAe = false, focusSuccessful = false))
    }

    @Test
    fun unsupportedMeteringCannotConfirmLock() {
        assertFalse(isAeAfLockConfirmed(hasAf = false, hasAe = false, focusSuccessful = true))
    }
}

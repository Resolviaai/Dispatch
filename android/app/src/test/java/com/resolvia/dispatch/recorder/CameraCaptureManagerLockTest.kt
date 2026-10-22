package com.resolvia.dispatch.recorder

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CameraCaptureManagerLockTest {
    @Test
    fun confirmedAeOnlyLockDoesNotRequireAutofocusSuccess() {
        assertTrue(isAeAfLockConfirmed(afLockConfirmed = false, aeLockConfirmed = true))
    }

    @Test
    fun autofocusOnlyLockRequiresConfirmedFocusResult() {
        assertTrue(isAeAfLockConfirmed(afLockConfirmed = true, aeLockConfirmed = false))
        assertFalse(isAeAfLockConfirmed(afLockConfirmed = false, aeLockConfirmed = false))
    }

    @Test
    fun channelCapabilityAloneIsNotAConfirmedLock() {
        // Capability is not passed to the production confirmation function; only successful
        // hardware results can mark an individual lock as confirmed.
        assertFalse(isAeAfLockConfirmed(afLockConfirmed = false, aeLockConfirmed = false))
    }
}

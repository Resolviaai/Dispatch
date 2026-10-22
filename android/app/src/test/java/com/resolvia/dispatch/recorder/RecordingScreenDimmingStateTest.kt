package com.resolvia.dispatch.recorder

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RecordingScreenDimmingStateTest {
    @Test
    fun onlyActiveForegroundRecordingCanDimAfterInactivity() {
        val state = RecordingScreenDimmingState()
        assertFalse(state.onInactivityTimeout())

        state.setRecordingEnabled(true)
        assertFalse(state.onInactivityTimeout())
        state.onActivityResumed()
        assertTrue(state.shouldRunInactivityTimer)
        assertTrue(state.onInactivityTimeout())
        assertTrue(state.isDimmed)
        assertFalse(state.shouldRunInactivityTimer)
    }

    @Test
    fun firstWakeGestureIsConsumedThroughItsEndAndDoesNotCountAsCameraInput() {
        val state = RecordingScreenDimmingState()
        state.setRecordingEnabled(true)
        state.onActivityResumed()
        assertTrue(state.onInactivityTimeout())

        val down = state.onTouch(RecordingTouchAction.DOWN)
        assertTrue(down.consume)
        assertTrue(down.wokeScreen)
        assertFalse(down.userActivity)
        assertTrue(state.onTouch(RecordingTouchAction.MOVE).consume)
        assertTrue(state.onTouch(RecordingTouchAction.UP).consume)
        assertTrue(state.shouldRunInactivityTimer)

        val nextTap = state.onTouch(RecordingTouchAction.DOWN)
        assertFalse(nextTap.consume)
        assertTrue(nextTap.userActivity)
    }

    @Test
    fun stoppingOrPausingRecordingRestoresVisibleStateAndDisarmsTimer() {
        val state = RecordingScreenDimmingState()
        state.setRecordingEnabled(true)
        state.onActivityResumed()
        assertTrue(state.onInactivityTimeout())

        state.setRecordingEnabled(false)
        assertFalse(state.isDimmed)
        assertFalse(state.shouldRunInactivityTimer)

        state.setRecordingEnabled(true)
        assertTrue(state.shouldRunInactivityTimer)
        assertTrue(state.onInactivityTimeout())
        state.onActivityPaused()
        assertFalse(state.isDimmed)
        assertFalse(state.shouldRunInactivityTimer)
    }
}

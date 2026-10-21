package com.resolvia.dispatch.recorder

internal enum class RecordingTouchAction { DOWN, POINTER_DOWN, MOVE, UP, CANCEL, OTHER }

internal data class RecordingTouchOutcome(
    val consume: Boolean = false,
    val wokeScreen: Boolean = false,
    val userActivity: Boolean = false
)

/** Production state machine for recording-only idle dimming and its consumed wake gesture. */
internal class RecordingScreenDimmingState {
    var recordingEnabled: Boolean = false
        private set
    var activityResumed: Boolean = false
        private set
    var isDimmed: Boolean = false
        private set

    private var consumingWakeGesture = false

    val shouldRunInactivityTimer: Boolean
        get() = recordingEnabled && activityResumed && !isDimmed

    fun setRecordingEnabled(enabled: Boolean) {
        recordingEnabled = enabled
        if (!enabled) {
            isDimmed = false
            consumingWakeGesture = false
        }
    }

    fun onActivityResumed() {
        activityResumed = true
    }

    fun onActivityPaused() {
        activityResumed = false
        isDimmed = false
        consumingWakeGesture = false
    }

    fun onInactivityTimeout(): Boolean {
        if (!shouldRunInactivityTimer) return false
        isDimmed = true
        return true
    }

    fun onTouch(action: RecordingTouchAction): RecordingTouchOutcome {
        if (consumingWakeGesture) {
            if (action == RecordingTouchAction.UP || action == RecordingTouchAction.CANCEL) {
                consumingWakeGesture = false
            }
            return RecordingTouchOutcome(consume = true)
        }

        if (!recordingEnabled || !activityResumed) return RecordingTouchOutcome()

        if (isDimmed && action == RecordingTouchAction.DOWN) {
            isDimmed = false
            consumingWakeGesture = true
            return RecordingTouchOutcome(consume = true, wokeScreen = true)
        }

        val userActivity = action == RecordingTouchAction.DOWN ||
            action == RecordingTouchAction.POINTER_DOWN ||
            action == RecordingTouchAction.MOVE
        return RecordingTouchOutcome(userActivity = userActivity)
    }
}

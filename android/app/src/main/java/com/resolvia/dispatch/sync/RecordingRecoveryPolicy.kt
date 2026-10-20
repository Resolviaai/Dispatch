package com.resolvia.dispatch.sync

import java.io.File

/** Small production decisions used by interrupted-recording recovery. */
internal fun shouldRecoverRecordingSession(sessionId: String, activeSessionId: String?): Boolean =
    sessionId != activeSessionId

internal fun findRecoveryCandidate(tmpFile: File, finalizedFile: File): File? = when {
    tmpFile.isFile && tmpFile.length() > 0L -> tmpFile
    finalizedFile.isFile && finalizedFile.length() > 0L -> finalizedFile
    else -> null
}

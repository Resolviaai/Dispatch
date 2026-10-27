package com.resolvia.dispatch.sync

/**
 * Durable upload-route policy for YouTube uploads.
 *
 * A segment's upload origin is a durable property: once a recording is assigned
 * to the cloud (Route A) vault session it can NEVER silently downgrade to the
 * legacy locally-stored OAuth credentials, even while the cloud session is
 * temporarily unavailable or after the connected account changes on the phone.
 * Mixing credentials would silently move someone's recording to the wrong
 * YouTube channel.
 *
 * Routing resolution, in precedence order:
 *  1. A saved resumable session URL wins — the mode that created it must
 *     finish it (its endpoint/token semantics are already bound).
 *  2. `uploadOrigin == "cloud"` is hard-pinned: cloud-origin segments stay
 *     cloud. Missing credentials produce a paused WAITING state handled by the
 *     worker, never a legacy fallback.
 *  3. `uploadOrigin == "legacy"` stays legacy (PC-paired OAuth).
 *  4. Brand-new segments (empty origin) prefer the cloud vault session and use
 *     legacy OAuth only when no cloud session exists.
 */
object UploadModePolicy {

    /** Route decision for brand-new, unassigned segments. */
    fun preferCloudForNew(uploadOrigin: String?, cloudSessionActive: Boolean): Boolean {
        return when (uploadOrigin) {
            "cloud" -> true
            "legacy" -> false
            else -> cloudSessionActive
        }
    }

    /**
     * Resolve whether this segment must upload via the cloud control plane.
     *
     * @param uploadOrigin        persisted per-segment origin ("cloud"/"legacy"/"")
     * @param savedSessionUrl     saved resumable session URL ("" when absent)
     * @param savedSessionIsCloud null when unknown, otherwise the recorded kind
     * @param cloudSessionActive  fresh cloud credential availability
     */
    fun resolveUploadMode(
        uploadOrigin: String?,
        savedSessionUrl: String?,
        savedSessionIsCloud: Boolean?,
        cloudSessionActive: Boolean
    ): Boolean {
        if (!savedSessionUrl.isNullOrBlank()) {
            return savedSessionIsCloud == true
        }
        return when (uploadOrigin) {
            // Hard pin: a cloud-origin segment never downgrades to legacy OAuth,
            // even when the vault session is temporarily unavailable. Missing
            // credentials must surface as WAITING_FOR_CREDENTIALS.
            "cloud" -> true
            // Hard pin the other way: legacy-uploaded recordings keep using the
            // locally-stored OAuth; never redirect them to a different account.
            "legacy" -> false
            else -> cloudSessionActive
        }
    }

    /**
     * Reconciliation variant used while advancing segments that already hold a
     * YouTube video id (processing-status polling). Cloud-origin stays cloud
     * ("succeeded"/"failed" verdicts must be fetched with the account that
     * actually owns the video), legacy stays legacy.
     */
    fun resolveReconcileMode(uploadOrigin: String?): Boolean = uploadOrigin == "cloud"
}

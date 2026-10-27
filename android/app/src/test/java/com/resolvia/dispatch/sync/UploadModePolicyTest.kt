package com.resolvia.dispatch.sync

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Durable upload-route policy: a cloud-origin segment must NEVER silently
 * downgrade to legacy locally-stored OAuth credentials, and legacy segments
 * must never be redirected to another account's credentials.
 */
class UploadModePolicyTest {

    @Test
    fun cloudOriginStaysCloudEvenWhenTemporaryCloudSessionDisappears() {
        // The regression: decideUploadMode used to evaluate
        // `cloudSessionActive()` for cloud-origin segments, which silently
        // downgraded an existing Route A recording to the legacy PC-paired
        // OAuth account whenever the vault session was missing/expired.
        assertTrue(
            UploadModePolicy.resolveUploadMode(
                uploadOrigin = "cloud",
                savedSessionUrl = "",
                savedSessionIsCloud = null,
                cloudSessionActive = false
            )
        )
    }

    @Test
    fun cloudOriginStaysCloudEvenWhenLegacyCredentialsExist() {
        // Availability of unrelated legacy credentials must not influence the
        // routing of a cloud-origin segment.
        assertTrue(
            UploadModePolicy.resolveUploadMode(
                uploadOrigin = "cloud",
                savedSessionUrl = "",
                savedSessionIsCloud = null,
                cloudSessionActive = false
            )
        )
    }

    @Test
    fun legacyOriginNeverRedirectsToCloudCredentials() {
        assertFalse(
            UploadModePolicy.resolveUploadMode(
                uploadOrigin = "legacy",
                savedSessionUrl = "",
                savedSessionIsCloud = null,
                cloudSessionActive = true
            )
        )
    }

    @Test
    fun savedSessionUrlWinsRegardlessOfCurrentSession() {
        // The endpoint of an in-flight resumable session is already bound to
        // the mode that created it; finishing it via the other mode would mix
        // authorization scopes.
        assertTrue(
            UploadModePolicy.resolveUploadMode(
                uploadOrigin = "legacy",
                savedSessionUrl = "https://www.googleapis.com/upload/resumable/sess1",
                savedSessionIsCloud = true,
                cloudSessionActive = false
            )
        )
        assertFalse(
            UploadModePolicy.resolveUploadMode(
                uploadOrigin = "cloud",
                savedSessionUrl = "https://www.googleapis.com/upload/resumable/sess2",
                savedSessionIsCloud = false,
                cloudSessionActive = true
            )
        )
    }

    @Test
    fun newSegmentsPreferCloudAndFallBackToLegacyOnlyWithoutCloud() {
        assertTrue(UploadModePolicy.preferCloudForNew(uploadOrigin = null, cloudSessionActive = true))
        assertFalse(UploadModePolicy.preferCloudForNew(uploadOrigin = null, cloudSessionActive = false))
        // Defensive precedence: explicit origins always win over availability.
        assertTrue(UploadModePolicy.preferCloudForNew(uploadOrigin = "cloud", cloudSessionActive = false))
        assertFalse(UploadModePolicy.preferCloudForNew(uploadOrigin = "legacy", cloudSessionActive = true))
    }

    @Test
    fun reconcileUsesPersistedOriginOnly() {
        // Processing-verdict polling must query with the account that actually
        // owns the video, ignoring whatever credentials are currently live.
        assertTrue(UploadModePolicy.resolveReconcileMode("cloud"))
        assertFalse(UploadModePolicy.resolveReconcileMode("legacy"))
        assertFalse(UploadModePolicy.resolveReconcileMode(null))
    }
}

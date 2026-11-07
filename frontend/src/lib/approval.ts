import { roundMs } from '../captions/timelineEngine';

export interface ApprovalExecutionParams {
  clip: {
    id: string;
    title?: string;
    description?: string;
    status?: string;
    range_revision?: number;
    captions_revision?: number;
    start_time?: number | null;
    end_time?: number | null;
    source_duration?: number | null;
    project_document?: any;
    rendered_fingerprint?: string;
    local_path?: string;
    video_path?: string;
    [key: string]: any;
  };
  optimisticTitle: string;
  optimisticDescription: string;
  optimisticLayout: string;
  optimisticMode: string;
  platformsToPublish: string;
  accountsMap: Record<string, string>;
  captionTrack?: any;
  pcHost?: string;
  token?: string | null;
  bridge?: any;
  workerBase?: string;
  isCloudMode?: boolean;
  getAuthHeaders?: () => Record<string, string>;
  apiUrl?: (path: string, host?: string) => string;
  localCaptionsRevision?: number;
}

export interface ApprovalExecutionResult {
  success: boolean;
  message: string;
  error?: string;
  actionTaken?: 'native_bridge' | 'pc_workstation' | 'cloud_queue' | 'republish';
}

export async function executeClipApproval(
  params: ApprovalExecutionParams,
  fetchFn: typeof fetch = (typeof window !== 'undefined' ? window.fetch.bind(window) : fetch)
): Promise<ApprovalExecutionResult> {
  const {
    clip,
    optimisticTitle,
    optimisticDescription,
    optimisticLayout,
    optimisticMode,
    platformsToPublish,
    accountsMap,
    captionTrack,
    pcHost,
    token,
    bridge,
    workerBase = 'https://dispatch-cloud.resolviaai.workers.dev',
    isCloudMode = false,
    getAuthHeaders = () => ({}),
    apiUrl = (path: string, host?: string) => host ? `${host.replace(/\/+$/, '')}${path}` : path,
    localCaptionsRevision,
  } = params;

  const rev = clip.range_revision || 0;

  try {
    // 1. If running natively on Android, trigger on-device Media3 rendering & background upload
    if (bridge?.startRenderAndPublish) {
      const rawDoc = clip.project_document;
      const parsedDoc = typeof rawDoc === 'string' ? JSON.parse(rawDoc) : (rawDoc && Object.keys(rawDoc).length > 0 ? rawDoc : null);
      const clipStart = Number(clip.start_time || 0);
      const clipEnd = Number(clip.end_time || (clipStart + 30));
      const sourceDur = Number(clip.source_duration || clipEnd || 60);

      const effectiveDoc = parsedDoc ? { ...parsedDoc } : {
        version: 1,
        timeline: {
          source_duration: sourceDur,
          segments: [{
            id: 'seg_1',
            source_start: clipStart,
            source_end: clipEnd,
          }],
          kept_duration: roundMs(clipEnd - clipStart),
        },
        tracks: [],
      };

      if (captionTrack && (!effectiveDoc.tracks || effectiveDoc.tracks.length === 0)) {
        effectiveDoc.tracks = [{
          ...captionTrack,
          kind: 'captions',
          id: captionTrack.id || 'caption_primary',
        }];
      }

      const renderJobPayload = {
        clip_id: clip.id,
        project_document: effectiveDoc,
        source_video_path: clip.local_path || clip.video_path || '',
        layout_mode: optimisticLayout,
        target_resolution: '1080p',
        render_fingerprint: clip.render_fingerprint || '',
        title: optimisticTitle,
        description: optimisticDescription,
        platforms: platformsToPublish,
        publish_mode: optimisticMode,
      };

      const bridgeResRaw = bridge.startRenderAndPublish(JSON.stringify(renderJobPayload));
      let bridgeSuccess = true;
      let bridgeError = '';

      if (typeof bridgeResRaw === 'string') {
        try {
          const bridgeJson = JSON.parse(bridgeResRaw);
          if (bridgeJson?.success === false) {
            bridgeSuccess = false;
            bridgeError = bridgeJson.error || 'Native render queue operation failed';
          }
        } catch {
          // Response is non-JSON text; if truthy, assume accepted
        }
      } else if (bridgeResRaw && typeof bridgeResRaw === 'object' && bridgeResRaw.success === false) {
        bridgeSuccess = false;
        bridgeError = bridgeResRaw.error || 'Native render queue operation failed';
      }

      if (!bridgeSuccess) {
        return {
          success: false,
          error: `On-device render failed: ${bridgeError}`,
          message: bridgeError,
          actionTaken: 'native_bridge',
        };
      }

      return {
        success: true,
        message: 'Started on-device video rendering (Media3 Transformer)…',
        actionTaken: 'native_bridge',
      };
    }

    // If clip was already published, directly submit to cloud publishing queue without re-rendering
    if (clip.status === 'published') {
      if (!token) {
        return {
          success: false,
          error: 'Cannot re-publish: authentication required.',
          message: 'Authentication required',
          actionTaken: 'republish',
        };
      }
      const cloudRes = await fetchFn(`${workerBase}/publish/queue`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          clip_id: clip.id,
          title: optimisticTitle,
          description: optimisticDescription,
          platforms: platformsToPublish,
          publish_mode: optimisticMode,
          rendered_fingerprint: clip.rendered_fingerprint,
        }),
      });

      if (!cloudRes.ok) {
        let errorDetail = `Publish failed (${cloudRes.status})`;
        try {
          const errJson = await cloudRes.json();
          if (errJson?.error || errJson?.message) errorDetail = errJson.error || errJson.message;
        } catch {}
        return {
          success: false,
          error: errorDetail,
          message: errorDetail,
          actionTaken: 'republish',
        };
      }

      return {
        success: true,
        message: `Publishing to ${platformsToPublish}…`,
        actionTaken: 'republish',
      };
    }

    // 2. PC Workstation approval (if connected to PC)
    let pcSuccess = false;
    if (pcHost) {
      if (captionTrack) {
        const capBaseRevision = localCaptionsRevision ?? Number(clip.captions_revision ?? 0);
        try {
          await fetchFn(apiUrl(`/api/clips/${clip.id}/captions`, pcHost), {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ ...captionTrack, base_revision: capBaseRevision }),
          });
        } catch (capErr) {
          console.warn('Caption pre-save non-fatal error:', capErr);
        }
      }

      const pcRes = await fetchFn(apiUrl(`/api/clips/${clip.id}/approve`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          title: optimisticTitle,
          hashtags: optimisticDescription,
          publish_mode: optimisticMode,
          platforms: platformsToPublish,
          account_ids: accountsMap,
          layout_mode: optimisticLayout,
          range_revision: rev,
        }),
      });

      if (!pcRes.ok) {
        let errorDetail = `PC approval failed (${pcRes.status})`;
        try {
          const errJson = await pcRes.json();
          if (errJson?.detail) errorDetail = errJson.detail;
        } catch {}
        return {
          success: false,
          error: errorDetail,
          message: errorDetail,
          actionTaken: 'pc_workstation',
        };
      }
      pcSuccess = true;
    }

    // 3. Cloud queue submission (if in cloud mode or authenticated)
    let cloudSuccess = false;
    if (token && (isCloudMode || !pcHost)) {
      const cloudRes = await fetchFn(`${workerBase}/publish/queue`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          clip_id: clip.id,
          title: optimisticTitle,
          description: optimisticDescription,
          platforms: platformsToPublish,
          publish_mode: optimisticMode,
          rendered_fingerprint: clip.rendered_fingerprint,
        }),
      });

      if (!cloudRes.ok) {
        let errorDetail = `Cloud publish failed (${cloudRes.status})`;
        try {
          const errJson = await cloudRes.json();
          if (errJson?.error || errJson?.message) errorDetail = errJson.error || errJson.message;
        } catch {}
        if (!pcSuccess) {
          return {
            success: false,
            error: errorDetail,
            message: errorDetail,
            actionTaken: 'cloud_queue',
          };
        }
      } else {
        cloudSuccess = true;
      }
    }

    if (!pcSuccess && !cloudSuccess) {
      return {
        success: false,
        error: 'Cannot approve: No PC connection or cloud authentication available.',
        message: 'No PC connection or cloud authentication available',
      };
    }

    return {
      success: true,
      message: 'Queued for rendering & publishing…',
      actionTaken: pcSuccess ? 'pc_workstation' : 'cloud_queue',
    };
  } catch (err: any) {
    console.error('Approval execution error:', err);
    return {
      success: false,
      error: `Approval failed: ${err?.message || err}`,
      message: err?.message || 'Approval execution failed',
    };
  }
}

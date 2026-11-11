import { roundMs } from '../captions/timelineEngine';
import type { ConnectedAccount } from '../accounts/accountsService';

export type PublishRoute = 'pc_workstation' | 'mobile_render_cloud' | 'cloud_queue';

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
    crop_anchor_x?: number | null;
    project_document?: any;
    render_fingerprint?: string;
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
  isPcOnline?: boolean;
  idempotencyKey?: string;
  getAuthHeaders?: () => Record<string, string>;
  apiUrl?: (path: string, host?: string) => string;
  localCaptionsRevision?: number;
}

export interface ApprovalExecutionResult {
  success: boolean;
  message: string;
  error?: string;
  route?: PublishRoute;
  idempotencyKey?: string;
  actionTaken?: 'native_bridge' | 'pc_workstation' | 'cloud_queue' | 'republish';
}

export interface PublishRouteResolution {
  route: PublishRoute | null;
  actionTaken?: 'native_bridge' | 'pc_workstation' | 'cloud_queue' | 'republish';
  reason?: string;
}

export const PUBLISH_PLATFORM_SLOTS: ReadonlyArray<{
  id: 'youtube_shorts' | 'instagram' | 'linkedin' | 'x';
  provider: ConnectedAccount['provider'];
}> = [
  { id: 'youtube_shorts', provider: 'youtube' },
  { id: 'instagram', provider: 'instagram' },
  { id: 'linkedin', provider: 'linkedin' },
  { id: 'x', provider: 'twitter' },
];

export function normalizePlatformIdForPublish(platformId: string): string {
  const norm = platformId.trim().toLowerCase();
  if (norm === 'youtube_shorts') return 'youtube';
  return norm;
}

function normalizePlatformIdToUiSlot(platform: string): string {
  const norm = platform.trim().toLowerCase();
  if (norm === 'youtube') return 'youtube_shorts';
  if (norm === 'twitter') return 'x';
  return norm;
}

/**
 * Selects the primary connected account per UI platform slot.
 * For YouTube, prefers publisher-enabled channels (`is_publisher !== false`)
 * over source-vault-only channels.
 */
export function buildPrimaryAccountMap(accounts: ConnectedAccount[]): Record<string, string> {
  const map: Record<string, string> = {};
  if (!Array.isArray(accounts) || accounts.length === 0) return map;

  for (const slot of PUBLISH_PLATFORM_SLOTS) {
    const matches = accounts.filter((acc) => acc.provider === slot.provider && Boolean(acc.account_id));
    if (matches.length === 0) continue;
    const primary = slot.provider === 'youtube'
      ? (matches.find((acc) => acc.metadata?.is_publisher !== false) || matches[0])
      : matches[0];
    map[slot.id] = primary.account_id;
  }
  return map;
}

/**
 * Computes fresh `selectedPlatforms` and `selectedAccountMap` when switching
 * clips or hydrating connected accounts so clip A's overrides never leak into clip B.
 */
export function deriveDefaultPublishSelection(
  accounts: ConnectedAccount[],
  clip?: { status?: string; platform_targets?: string; publications?: Array<{ platform: string }> } | null
): { selectedPlatforms: string[]; selectedAccountMap: Record<string, string> } {
  const selectedAccountMap = buildPrimaryAccountMap(accounts);
  if (accounts.length === 0) {
    return { selectedPlatforms: ['youtube_shorts'], selectedAccountMap };
  }

  const connectedSlots = PUBLISH_PLATFORM_SLOTS
    .filter((slot) => Boolean(selectedAccountMap[slot.id]))
    .map((slot) => slot.id);

  if (clip?.status === 'published') {
    const published = new Set(
      (clip.publications || []).map((pub) => normalizePlatformIdToUiSlot(pub.platform))
    );
    const remaining = connectedSlots.filter((slotId) => !published.has(slotId));
    if (remaining.length > 0) {
      return { selectedPlatforms: remaining, selectedAccountMap };
    }
  }

  if (clip?.platform_targets) {
    const requested = clip.platform_targets
      .split(',')
      .map((p) => normalizePlatformIdToUiSlot(p))
      .filter((slotId) => Boolean(selectedAccountMap[slotId]));
    if (requested.length > 0) {
      return { selectedPlatforms: Array.from(new Set(requested)), selectedAccountMap };
    }
  }

  const defaults: string[] = [];
  if (selectedAccountMap.youtube_shorts) defaults.push('youtube_shorts');
  if (selectedAccountMap.instagram) defaults.push('instagram');
  if (defaults.length === 0 && connectedSlots.length > 0) {
    defaults.push(connectedSlots[0]);
  }
  return {
    selectedPlatforms: defaults.length > 0 ? defaults : ['youtube_shorts'],
    selectedAccountMap,
  };
}

/**
 * Ensures every selected platform carries an explicit account_id in `account_ids`
 * (using the user's explicit selection or falling back to the primary connected
 * account for that provider).
 */
export function resolveSelectedAccountsMap(
  selectedPlatforms: string[],
  selectedAccountMap: Record<string, string>,
  accounts: ConnectedAccount[]
): Record<string, string> {
  const primary = buildPrimaryAccountMap(accounts);
  const resolved: Record<string, string> = {};

  for (const rawPlatform of selectedPlatforms) {
    const slotId = normalizePlatformIdToUiSlot(rawPlatform);
    const slotAccounts = accounts.filter((acc) => {
      const slot = PUBLISH_PLATFORM_SLOTS.find((s) => s.id === slotId);
      return slot ? acc.provider === slot.provider : acc.provider === slotId;
    });
    const explicit = selectedAccountMap[slotId] || selectedAccountMap[rawPlatform];
    const explicitValid = explicit && (slotAccounts.length === 0 || slotAccounts.some((a) => a.account_id === explicit));
    const chosen = explicitValid ? explicit : primary[slotId];
    if (chosen) {
      resolved[slotId] = chosen;
    }
  }
  return resolved;
}

/**
 * Expands UI slot aliases (`youtube_shorts` <-> `youtube`, `x` <-> `twitter`) so
 * both PC SQLite (`dispatch/db.py`) and Cloud Worker match every platform key.
 */
export function normalizeAccountIdsForPayload(accountsMap: Record<string, string> | undefined): Record<string, string> {
  const normalized: Record<string, string> = {};
  if (!accountsMap) return normalized;

  for (const [key, value] of Object.entries(accountsMap)) {
    const cleanKey = key.trim();
    const cleanVal = typeof value === 'string' ? value.trim() : '';
    if (!cleanKey || !cleanVal) continue;
    normalized[cleanKey] = cleanVal;
  }

  if (normalized.youtube_shorts && !normalized.youtube) {
    normalized.youtube = normalized.youtube_shorts;
  } else if (normalized.youtube && !normalized.youtube_shorts) {
    normalized.youtube_shorts = normalized.youtube;
  }

  if (normalized.x && !normalized.twitter) {
    normalized.twitter = normalized.x;
  } else if (normalized.twitter && !normalized.x) {
    normalized.x = normalized.twitter;
  }

  return normalized;
}

function stableHashHex(input: string): string {
  let h1 = 0x811c9dc5;
  let h2 = 0x9e3779b9;
  for (let i = 0; i < input.length; i++) {
    const ch = input.charCodeAt(i);
    h1 ^= ch;
    h1 = Math.imul(h1, 0x01000193);
    h2 ^= ch + ((i + 1) & 0xff);
    h2 = Math.imul(h2, 0x85ebca6b);
  }
  return `${(h1 >>> 0).toString(16).padStart(8, '0')}${(h2 >>> 0).toString(16).padStart(8, '0')}`;
}

export function buildApprovalIdempotencyKey(input: {
  clipId: string;
  rangeRevision: number;
  status?: string;
  platforms: string;
  publishMode: string;
  layoutMode: string;
  accountsMap: Record<string, string>;
}): string {
  const sortedAccounts = Object.entries(normalizeAccountIdsForPayload(input.accountsMap))
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join('&');
  const sortedPlatforms = input.platforms
    .split(',')
    .map((p) => p.trim().toLowerCase())
    .filter(Boolean)
    .sort()
    .join(',');
  const signature = [
    input.clipId,
    String(input.rangeRevision || 0),
    input.status || 'ready_review',
    sortedPlatforms,
    input.publishMode || 'private',
    input.layoutMode || '',
    sortedAccounts,
  ].join('|');
  return `idem_${input.clipId}_r${input.rangeRevision || 0}_${stableHashHex(signature)}`;
}

/**
 * Selects the single mutually-exclusive publish route for an approval.
 * Never selects both PC and Cloud, and never lets a stale unreachable `pcHost`
 * hijack Cloud Mode or offline PC scenarios.
 */
export function resolvePublishRoute(params: ApprovalExecutionParams): PublishRouteResolution {
  const { clip, pcHost, token, bridge, isCloudMode = false, isPcOnline } = params;
  const hasPcHost = Boolean(pcHost && pcHost.trim().length > 0);
  const hasBridge = typeof bridge?.startRenderAndPublish === 'function';
  const hasToken = Boolean(token && token.trim().length > 0);

  if (clip.status === 'published') {
    return { route: 'cloud_queue', actionTaken: 'republish' };
  }

  // Active PC Workstation owns the approval only when NOT in cloud mode and PC is reachable.
  if (!isCloudMode && hasPcHost && isPcOnline !== false) {
    return { route: 'pc_workstation', actionTaken: 'pc_workstation' };
  }

  // On Android WebView without an active PC connection, render on-device and upload to Cloud.
  if (hasBridge) {
    return { route: 'mobile_render_cloud', actionTaken: 'native_bridge' };
  }

  if (hasToken) {
    return { route: 'cloud_queue', actionTaken: 'cloud_queue' };
  }

  return {
    route: null,
    reason: isCloudMode
      ? 'Cannot approve in Cloud mode: authentication required.'
      : 'Cannot approve: No PC connection or cloud authentication available.',
  };
}

async function readResponseError(res: Response, fallback: string): Promise<string> {
  try {
    const errJson = await res.json();
    if (errJson?.detail) return String(errJson.detail);
    if (errJson?.error) return String(errJson.error);
    if (errJson?.message) return String(errJson.message);
  } catch {
    // Non-JSON error body
  }
  return fallback;
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
    getAuthHeaders = () => ({}),
    apiUrl = (path: string, host?: string) => (host ? `${host.replace(/\/+$/, '')}${path}` : path),
    localCaptionsRevision,
  } = params;

  const rev = clip.range_revision || 0;
  const normalizedAccountIds = normalizeAccountIdsForPayload(accountsMap);
  const idempotencyKey = params.idempotencyKey?.trim() || buildApprovalIdempotencyKey({
    clipId: clip.id,
    rangeRevision: rev,
    status: clip.status,
    platforms: platformsToPublish,
    publishMode: optimisticMode,
    layoutMode: optimisticLayout,
    accountsMap: normalizedAccountIds,
  });

  const resolution = resolvePublishRoute(params);
  if (!resolution.route) {
    return {
      success: false,
      error: resolution.reason || 'No PC connection or cloud authentication available.',
      message: resolution.reason || 'No PC connection or cloud authentication available',
      idempotencyKey,
    };
  }

  const normalizedPlatforms = platformsToPublish
    .split(',')
    .map((p) => p.trim())
    .filter(Boolean);
  if (normalizedPlatforms.length === 0) {
    return {
      success: false,
      error: 'Select at least one publishing destination.',
      message: 'No publishing destination selected',
      route: resolution.route,
      actionTaken: resolution.actionTaken,
      idempotencyKey,
    };
  }

  try {
    switch (resolution.route) {
      case 'mobile_render_cloud': {
        const rawDoc = clip.project_document;
        let parsedDoc: any = null;
        if (typeof rawDoc === 'string' && rawDoc.trim().length > 0) {
          try {
            parsedDoc = JSON.parse(rawDoc);
          } catch {
            return {
              success: false,
              error: 'On-device render failed: invalid project_document JSON.',
              message: 'Invalid project document',
              route: 'mobile_render_cloud',
              actionTaken: 'native_bridge',
              idempotencyKey,
            };
          }
        } else if (rawDoc && typeof rawDoc === 'object' && Object.keys(rawDoc).length > 0) {
          parsedDoc = rawDoc;
        }

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
          crop_anchor_x: typeof clip.crop_anchor_x === 'number' ? clip.crop_anchor_x : 0.5,
          target_resolution: '1080p',
          render_fingerprint: clip.render_fingerprint || clip.rendered_fingerprint || '',
          title: optimisticTitle,
          description: optimisticDescription,
          platforms: normalizedPlatforms.join(','),
          publish_mode: optimisticMode,
          account_ids: normalizedAccountIds,
          idempotency_key: idempotencyKey,
        };

        const bridgeResRaw = bridge.startRenderAndPublish(JSON.stringify(renderJobPayload));
        let bridgeSuccess = false;
        let bridgeError = '';

        if (typeof bridgeResRaw === 'string') {
          if (!bridgeResRaw.trim()) {
            bridgeError = 'Native render bridge returned empty response';
          } else {
            try {
              const bridgeJson = JSON.parse(bridgeResRaw);
              if (bridgeJson?.success === true) {
                bridgeSuccess = true;
              } else {
                bridgeError = bridgeJson?.error || 'Native render queue operation failed';
              }
            } catch {
              bridgeError = 'Native render bridge returned invalid JSON response';
            }
          }
        } else if (bridgeResRaw && typeof bridgeResRaw === 'object') {
          if (bridgeResRaw.success === true) {
            bridgeSuccess = true;
          } else {
            bridgeError = bridgeResRaw.error || 'Native render queue operation failed';
          }
        } else {
          bridgeError = 'Native render bridge did not confirm job enqueue';
        }

        if (!bridgeSuccess) {
          return {
            success: false,
            error: `On-device render failed: ${bridgeError}`,
            message: bridgeError,
            route: 'mobile_render_cloud',
            actionTaken: 'native_bridge',
            idempotencyKey,
          };
        }

        return {
          success: true,
          message: 'Started on-device video rendering (Media3 Transformer)…',
          route: 'mobile_render_cloud',
          actionTaken: 'native_bridge',
          idempotencyKey,
        };
      }

      case 'pc_workstation': {
        if (captionTrack) {
          if (captionTrack.clip_id && captionTrack.clip_id !== clip.id) {
            return {
              success: false,
              error: `Caption track mismatch (${captionTrack.clip_id} !== ${clip.id})`,
              message: 'Caption track mismatch',
              route: 'pc_workstation',
              actionTaken: 'pc_workstation',
              idempotencyKey,
            };
          }
          const capBaseRevision = localCaptionsRevision ?? Number(clip.captions_revision ?? 0);
          const capUrl = apiUrl(`/api/clips/${encodeURIComponent(clip.id)}/captions`, pcHost);
          if (!capUrl) {
            return {
              success: false,
              error: 'PC workstation URL is unavailable for saving captions.',
              message: 'PC workstation URL unavailable',
              route: 'pc_workstation',
              actionTaken: 'pc_workstation',
              idempotencyKey,
            };
          }
          const capRes = await fetchFn(capUrl, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ ...captionTrack, base_revision: capBaseRevision }),
          });
          if (!capRes.ok) {
            const capError = await readResponseError(capRes, `Caption save failed (${capRes.status})`);
            return {
              success: false,
              error: capError,
              message: capError,
              route: 'pc_workstation',
              actionTaken: 'pc_workstation',
              idempotencyKey,
            };
          }
        }

        const approveUrl = apiUrl(`/api/clips/${encodeURIComponent(clip.id)}/approve`, pcHost);
        if (!approveUrl) {
          return {
            success: false,
            error: 'PC workstation URL is unavailable for approval.',
            message: 'PC workstation URL unavailable',
            route: 'pc_workstation',
            actionTaken: 'pc_workstation',
            idempotencyKey,
          };
        }

        const pcRes = await fetchFn(approveUrl, {
          method: 'POST',
          headers: getAuthHeaders(),
          body: JSON.stringify({
            title: optimisticTitle,
            hashtags: optimisticDescription,
            publish_mode: optimisticMode,
            platforms: normalizedPlatforms.join(','),
            account_ids: normalizedAccountIds,
            layout_mode: optimisticLayout,
            range_revision: rev,
            idempotency_key: idempotencyKey,
          }),
        });

        if (!pcRes.ok) {
          const errorDetail = await readResponseError(pcRes, `PC approval failed (${pcRes.status})`);
          return {
            success: false,
            error: errorDetail,
            message: errorDetail,
            route: 'pc_workstation',
            actionTaken: 'pc_workstation',
            idempotencyKey,
          };
        }

        const pcData = await pcRes.json().catch(() => null);
        if (pcData && pcData.status === 'error') {
          const detail = String(pcData.detail || pcData.error || pcData.message || 'PC approval failed');
          return {
            success: false,
            error: detail,
            message: detail,
            route: 'pc_workstation',
            actionTaken: 'pc_workstation',
            idempotencyKey,
          };
        }

        return {
          success: true,
          message: pcData?.idempotent
            ? (pcData.message || 'Clip is already approved.')
            : 'Queued for rendering & publishing on PC…',
          route: 'pc_workstation',
          actionTaken: 'pc_workstation',
          idempotencyKey,
        };
      }

      case 'cloud_queue': {
        const isRepublish = clip.status === 'published';
        const actionTaken = isRepublish ? 'republish' : 'cloud_queue';
        if (!token || !token.trim()) {
          return {
            success: false,
            error: isRepublish
              ? 'Cannot re-publish: authentication required.'
              : 'Cannot publish to Dispatch Cloud: authentication required.',
            message: 'Authentication required',
            route: 'cloud_queue',
            actionTaken,
            idempotencyKey,
          };
        }

        const cloudRes = await fetchFn(`${workerBase}/publish/queue`, {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${token.trim()}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            clip_id: clip.id,
            title: optimisticTitle,
            description: optimisticDescription,
            platforms: normalizedPlatforms.join(','),
            publish_mode: optimisticMode,
            rendered_fingerprint: clip.rendered_fingerprint || clip.render_fingerprint,
            account_ids: normalizedAccountIds,
            idempotency_key: idempotencyKey,
          }),
        });

        if (!cloudRes.ok) {
          const errorDetail = await readResponseError(
            cloudRes,
            isRepublish ? `Publish failed (${cloudRes.status})` : `Cloud publish failed (${cloudRes.status})`
          );
          return {
            success: false,
            error: errorDetail,
            message: errorDetail,
            route: 'cloud_queue',
            actionTaken,
            idempotencyKey,
          };
        }

        const cloudData = await cloudRes.json().catch(() => null);
        if (cloudData && (cloudData.ok === false || cloudData.success === false)) {
          const detail = String(cloudData.error || cloudData.message || 'Cloud publish enqueue failed');
          return {
            success: false,
            error: detail,
            message: detail,
            route: 'cloud_queue',
            actionTaken,
            idempotencyKey,
          };
        }

        return {
          success: true,
          message: isRepublish
            ? `Queued publishing to ${normalizedPlatforms.join(', ')}…`
            : 'Queued for cloud publishing…',
          route: 'cloud_queue',
          actionTaken,
          idempotencyKey,
        };
      }
    }
  } catch (err: any) {
    console.error('Approval execution error:', err);
    return {
      success: false,
      error: `Approval failed: ${err?.message || err}`,
      message: err?.message || 'Approval execution failed',
      route: resolution.route,
      actionTaken: resolution.actionTaken,
      idempotencyKey,
    };
  }
}

export interface TimelineCommitParams {
  clipId: string;
  cloudId?: string | null;
  baseRevision: number;
  projectDocument: Record<string, any>;
  operationId: string;
  actionType: 'split' | 'delete_piece' | 'trim_edge' | 'auto_cut';
  isCloudMode: boolean;
  isPcOnline?: boolean;
  pcHost?: string;
  token?: string | null;
  workerBase?: string;
  getAuthHeaders?: () => Record<string, string>;
  apiUrl?: (path: string, host?: string) => string;
}

export interface TimelineCommitResult {
  success: boolean;
  route?: 'pc_workstation' | 'cloud';
  revision?: number;
  error?: string;
}

/**
 * Fail-closed persistence helper for timeline mutations (split, delete_piece, trim_edge, auto_cut).
 * Never swallows HTTP errors or missing-auth conditions and never returns success on failure.
 */
export async function executeTimelineCommit(
  params: TimelineCommitParams,
  fetchFn: typeof fetch = (typeof window !== 'undefined' ? window.fetch.bind(window) : fetch)
): Promise<TimelineCommitResult> {
  const {
    clipId,
    cloudId,
    baseRevision,
    projectDocument,
    operationId,
    actionType,
    isCloudMode,
    isPcOnline,
    pcHost,
    token,
    workerBase = 'https://dispatch-cloud.resolviaai.workers.dev',
    getAuthHeaders = () => ({}),
    apiUrl = (path: string, host?: string) => (host ? `${host.replace(/\/+$/, '')}${path}` : path),
  } = params;

  const canUsePc = !isCloudMode && Boolean(pcHost && pcHost.trim()) && isPcOnline !== false;
  let tryCloudFallback = false;

  if (canUsePc) {
    const pcUrl = apiUrl(`/api/clips/${encodeURIComponent(clipId)}/timeline`, pcHost);
    if (!pcUrl) {
      return {
        success: false,
        route: 'pc_workstation',
        error: 'PC workstation URL is unavailable.',
      };
    }

    try {
      const pcRes = await fetchFn(pcUrl, {
        method: 'PUT',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          expected_revision: baseRevision,
          base_revision: baseRevision,
          project_document: projectDocument,
          operation_id: operationId,
          action_type: actionType,
        }),
      });
      if (!pcRes.ok) {
        const errDetail = await readResponseError(pcRes, `PC timeline save failed (HTTP ${pcRes.status})`);
        return {
          success: false,
          route: 'pc_workstation',
          error: errDetail,
        };
      }
      const pcData = await pcRes.json().catch(() => ({}));
      const rev = Number(pcData?.revision ?? pcData?.segments_revision ?? (baseRevision + 1));
      return {
        success: true,
        route: 'pc_workstation',
        revision: Number.isFinite(rev) ? rev : (baseRevision + 1),
      };
    } catch (pcErr: any) {
      if (token && token.trim() && cloudId) {
        tryCloudFallback = true;
      } else {
        return {
          success: false,
          route: 'pc_workstation',
          error: pcErr?.message || 'PC workstation unreachable',
        };
      }
    }
  }

  const shouldUseCloud = isCloudMode || !canUsePc || tryCloudFallback;
  if (!shouldUseCloud) {
    return {
      success: false,
      error: 'No PC connection or cloud authentication available.',
    };
  }

  if (!token || !token.trim()) {
    return {
      success: false,
      route: 'cloud',
      error: 'Sign in to save timeline edits to Dispatch Cloud.',
    };
  }

  try {
    const res = await fetchFn(`${workerBase}/clips/${encodeURIComponent(clipId)}/timeline`, {
      method: 'PUT',
      headers: {
        'Authorization': `Bearer ${token.trim()}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        expected_revision: baseRevision,
        base_revision: baseRevision,
        document: projectDocument,
        project_document: projectDocument,
        operation_id: operationId,
        action_type: actionType,
      }),
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      const conflictRev = typeof errData?.revision === 'number'
        ? errData.revision
        : (typeof errData?.segments_revision === 'number' ? errData.segments_revision : null);

      if (res.status === 409 && conflictRev !== null) {
        const retryRes = await fetchFn(`${workerBase}/clips/${encodeURIComponent(clipId)}/timeline`, {
          method: 'PUT',
          headers: {
            'Authorization': `Bearer ${token.trim()}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            expected_revision: conflictRev,
            base_revision: conflictRev,
            document: projectDocument,
            project_document: projectDocument,
            operation_id: `op_${actionType}_retry_${Date.now()}`,
            action_type: actionType,
          }),
        });
        if (retryRes.ok) {
          const retryData = await retryRes.json().catch(() => ({}));
          const rev = Number(retryData?.revision ?? retryData?.segments_revision ?? (conflictRev + 1));
          return {
            success: true,
            route: 'cloud',
            revision: Number.isFinite(rev) ? rev : (conflictRev + 1),
          };
        }
        const retryErr = await readResponseError(retryRes, `Cloud save conflict (${retryRes.status})`);
        return {
          success: false,
          route: 'cloud',
          error: retryErr,
        };
      }

      const detail = String(errData?.detail || errData?.error || errData?.message || `Cloud save failed (HTTP ${res.status})`);
      return {
        success: false,
        route: 'cloud',
        error: detail,
      };
    }

    const cloudData = await res.json().catch(() => ({}));
    const rev = Number(cloudData?.revision ?? cloudData?.segments_revision ?? (baseRevision + 1));
    return {
      success: true,
      route: 'cloud',
      revision: Number.isFinite(rev) ? rev : (baseRevision + 1),
    };
  } catch (cloudErr: any) {
    return {
      success: false,
      route: 'cloud',
      error: cloudErr?.message || 'Cloud timeline save failed',
    };
  }
}

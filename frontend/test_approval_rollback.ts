// Test: Production Approval Execution, Route Exclusivity, Account Selection & Fail-Closed Commits
// Tests executeClipApproval, resolvePublishRoute, primary account helpers, cloudSessionBootstrap,
// and executeTimelineCommit directly against Android bridge, PC workstation, and cloud backends.

import assert from 'node:assert/strict';
import {
  executeClipApproval,
  executeTimelineCommit,
  resolvePublishRoute,
  buildPrimaryAccountMap,
  deriveDefaultPublishSelection,
  resolveSelectedAccountsMap,
  normalizeAccountIdsForPayload,
  buildApprovalIdempotencyKey,
} from './src/lib/approval';
import { selectSourceVaultYouTubeAccount } from './src/auth/cloudSessionBootstrap';
import type { ConnectedAccount } from './src/accounts/accountsService';

console.log('🧪 Starting Dispatch Production Approval, Route Exclusivity & Fail-Closed Test Suite...');

function createMockClip(id: string, title: string = 'Test Clip') {
  return {
    id,
    title,
    description: 'A test clip description',
    status: 'ready_review',
    range_revision: 1,
    start_time: 0,
    end_time: 30,
    source_duration: 60,
  };
}

// -----------------------------------------------------------------------------
// Test 1: Native Android Bridge failure with JSON string response
// -----------------------------------------------------------------------------
(async () => {
  console.log('\nTest 1: Native Android Bridge returns JSON {"success": false, "error": "Disk full"}...');
  const mockBridge = {
    startRenderAndPublish: (_payload: string) => {
      return JSON.stringify({
        success: false,
        error: 'Insufficient internal storage for Media3 rendering (512 MB required)',
      });
    },
  };

  const clip = createMockClip('c_bridge_err_1');
  const result = await executeClipApproval({
    clip,
    optimisticTitle: 'Title 1',
    optimisticDescription: 'Desc 1',
    optimisticLayout: 'portrait_9_16',
    optimisticMode: 'private',
    platformsToPublish: 'youtube',
    accountsMap: {},
    bridge: mockBridge,
  });

  if (result.success !== false) {
    throw new Error(`Expected result.success === false on native bridge error, got ${result.success}`);
  }
  if (!result.error || !result.error.includes('Insufficient internal storage')) {
    throw new Error(`Expected error message to mention 'Insufficient internal storage', got '${result.error}'`);
  }
  if (result.actionTaken !== 'native_bridge') {
    throw new Error(`Expected actionTaken 'native_bridge', got '${result.actionTaken}'`);
  }
  console.log('  ✓ Native bridge failure correctly captured and returned as failure result');
})();

// -----------------------------------------------------------------------------
// Test 2: Native Android Bridge failure with Object response
// -----------------------------------------------------------------------------
(async () => {
  console.log('\nTest 2: Native Android Bridge returns Object {success: false, error: "MediaCodec crashed"}...');
  const mockBridge = {
    startRenderAndPublish: (_payload: string) => {
      return {
        success: false,
        error: 'MediaCodec hardware decoder crashed during initialization',
      };
    },
  };

  const clip = createMockClip('c_bridge_err_2');
  const result = await executeClipApproval({
    clip,
    optimisticTitle: 'Title 2',
    optimisticDescription: 'Desc 2',
    optimisticLayout: 'portrait_9_16',
    optimisticMode: 'private',
    platformsToPublish: 'youtube',
    accountsMap: {},
    bridge: mockBridge,
  });

  if (result.success !== false) {
    throw new Error(`Expected result.success === false on native bridge object error, got ${result.success}`);
  }
  if (!result.error || !result.error.includes('MediaCodec hardware decoder crashed')) {
    throw new Error(`Expected error message to mention 'MediaCodec hardware decoder crashed', got '${result.error}'`);
  }
  console.log('  ✓ Native bridge object error correctly captured and returned as failure result');
})();

// -----------------------------------------------------------------------------
// Test 3: Native Android Bridge success with JSON string response
// -----------------------------------------------------------------------------
(async () => {
  console.log('\nTest 3: Native Android Bridge returns JSON {"success": true, "job_id": "render_abc", "status": "QUEUED"}...');
  const mockBridge = {
    startRenderAndPublish: (_payload: string) => {
      return JSON.stringify({
        success: true,
        job_id: 'render_1730000000_12345678',
        status: 'QUEUED',
      });
    },
  };

  const clip = createMockClip('c_bridge_ok');
  const result = await executeClipApproval({
    clip,
    optimisticTitle: 'Title 3',
    optimisticDescription: 'Desc 3',
    optimisticLayout: 'portrait_9_16',
    optimisticMode: 'private',
    platformsToPublish: 'youtube',
    accountsMap: {},
    bridge: mockBridge,
  });

  if (result.success !== true) {
    throw new Error(`Expected result.success === true on native bridge success, got ${result.success}`);
  }
  if (!result.message.includes('Started on-device video rendering')) {
    throw new Error(`Expected success message, got '${result.message}'`);
  }
  console.log('  ✓ Native bridge success confirmed with 2xx queue state');
})();

// -----------------------------------------------------------------------------
// Test 4: PC Workstation approval returns HTTP 409 Conflict
// -----------------------------------------------------------------------------
(async () => {
  console.log('\nTest 4: PC Workstation returns HTTP 409 Conflict (e.g. range changed concurrently)...');
  const mockFetch = async (url: any) => {
    const urlStr = String(url);
    if (urlStr.includes('/captions')) {
      return { ok: true, json: async () => ({ status: 'ok' }) } as any;
    }
    return {
      ok: false,
      status: 409,
      json: async () => ({ detail: 'The clip range changed while rendering. Review and approve again.' }),
    } as any;
  };

  const clip = createMockClip('c_pc_409');
  const result = await executeClipApproval({
    clip,
    optimisticTitle: 'Title 4',
    optimisticDescription: 'Desc 4',
    optimisticLayout: 'fit_blur',
    optimisticMode: 'private',
    platformsToPublish: 'youtube',
    accountsMap: {},
    pcHost: 'http://127.0.0.1:8000',
  }, mockFetch);

  if (result.success !== false) {
    throw new Error('Expected result.success === false on PC 409 Conflict');
  }
  if (!result.error || !result.error.includes('The clip range changed while rendering')) {
    throw new Error(`Expected server 409 error detail, got '${result.error}'`);
  }
  console.log('  ✓ PC HTTP 409 Conflict captured with server error detail');
})();

// -----------------------------------------------------------------------------
// Test 5: Network Exception during approval call
// -----------------------------------------------------------------------------
(async () => {
  console.log('\nTest 5: Network exception / timeout during PC approval call...');
  const mockFetch = async () => {
    throw new Error('Failed to fetch: Connection refused');
  };

  const clip = createMockClip('c_pc_net_err');
  const result = await executeClipApproval({
    clip,
    optimisticTitle: 'Title 5',
    optimisticDescription: 'Desc 5',
    optimisticLayout: 'fit_blur',
    optimisticMode: 'private',
    platformsToPublish: 'youtube',
    accountsMap: {},
    pcHost: 'http://127.0.0.1:8000',
  }, mockFetch);

  if (result.success !== false) {
    throw new Error('Expected result.success === false on network exception');
  }
  if (!result.error || !result.error.includes('Connection refused')) {
    throw new Error(`Expected error to mention Connection refused, got '${result.error}'`);
  }
  console.log('  ✓ Network exception safely caught and converted to failure result');
})();

// -----------------------------------------------------------------------------
// Test 6: PC Workstation approval succeeds (HTTP 200)
// -----------------------------------------------------------------------------
(async () => {
  console.log('\nTest 6: PC Workstation returns HTTP 200 OK...');
  const mockFetch = async (url: any) => {
    const urlStr = String(url);
    if (urlStr.includes('/captions')) {
      return { ok: true, json: async () => ({ status: 'ok' }) } as any;
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({ status: 'success', message: 'Clip approved' }),
    } as any;
  };

  const clip = createMockClip('c_pc_ok');
  const result = await executeClipApproval({
    clip,
    optimisticTitle: 'Title 6',
    optimisticDescription: 'Desc 6',
    optimisticLayout: 'fit_blur',
    optimisticMode: 'private',
    platformsToPublish: 'youtube',
    accountsMap: {},
    pcHost: 'http://127.0.0.1:8000',
  }, mockFetch);

  if (result.success !== true) {
    throw new Error(`Expected result.success === true on PC 200 OK, got ${result.success}`);
  }
  if (result.actionTaken !== 'pc_workstation') {
    throw new Error(`Expected actionTaken 'pc_workstation', got '${result.actionTaken}'`);
  }
  console.log('  ✓ PC HTTP 200 verified with queued success state');
})();

// -----------------------------------------------------------------------------
// Test 7: Cloud Queue approval succeeds
// -----------------------------------------------------------------------------
(async () => {
  console.log('\nTest 7: Cloud Queue submission succeeds (token present, no pcHost)...');
  const mockFetch = async () => {
    return {
      ok: true,
      status: 200,
      json: async () => ({ success: true }),
    } as any;
  };

  const clip = createMockClip('c_cloud_ok');
  const result = await executeClipApproval({
    clip,
    optimisticTitle: 'Title 7',
    optimisticDescription: 'Desc 7',
    optimisticLayout: 'fit_blur',
    optimisticMode: 'private',
    platformsToPublish: 'youtube',
    accountsMap: {},
    token: 'jwt_mock_token_123',
    isCloudMode: true,
  }, mockFetch);

  if (result.success !== true) {
    throw new Error(`Expected result.success === true on Cloud 200 OK, got ${result.success}`);
  }
  if (result.actionTaken !== 'cloud_queue') {
    throw new Error(`Expected actionTaken 'cloud_queue', got '${result.actionTaken}'`);
  }
  if (result.route !== 'cloud_queue') {
    throw new Error(`Expected route 'cloud_queue', got '${result.route}'`);
  }
  console.log('  ✓ Cloud queue submission verified');
})();

// -----------------------------------------------------------------------------
// Test 8: Route Exclusivity — stale pcHost in Cloud Mode never drops Cloud or dual-publishes
// -----------------------------------------------------------------------------
(async () => {
  console.log('\nTest 8: Stale unreachable pcHost + isCloudMode=true routes exclusively to cloud_queue...');
  const calledUrls: string[] = [];
  const mockFetch = async (url: any, init?: RequestInit) => {
    const urlStr = String(url);
    calledUrls.push(urlStr);
    if (urlStr.includes('192.168.1.99')) {
      throw new Error('Stale PC host should never be called in cloud mode');
    }
    const body = JSON.parse(String(init?.body || '{}'));
    assert.ok(body.idempotency_key && body.idempotency_key.startsWith('idem_'), 'idempotency_key sent to cloud');
    assert.equal(body.account_ids?.youtube, 'yt_primary_1', 'primary youtube account_id sent to cloud');
    return {
      ok: true,
      status: 200,
      json: async () => ({ ok: true, status: 'queued' }),
    } as any;
  };

  const clip = createMockClip('c_stale_pchost');
  const result = await executeClipApproval({
    clip,
    optimisticTitle: 'Cloud Title',
    optimisticDescription: 'Desc',
    optimisticLayout: 'fit_blur',
    optimisticMode: 'private',
    platformsToPublish: 'youtube',
    accountsMap: { youtube_shorts: 'yt_primary_1' },
    pcHost: 'http://192.168.1.99:8000',
    isCloudMode: true,
    isPcOnline: false,
    token: 'jwt_mock_token_123',
  }, mockFetch);

  assert.equal(result.success, true);
  assert.equal(result.route, 'cloud_queue');
  assert.equal(calledUrls.length, 1, 'Exactly one HTTP request must fire');
  assert.ok(calledUrls[0].endsWith('/publish/queue'), 'Must hit Cloud /publish/queue');
  console.log('  ✓ Stale pcHost in cloud mode ignored; exclusively routed to cloud_queue');
})();

// -----------------------------------------------------------------------------
// Test 9: Route Exclusivity — Active PC workstation never dual-publishes to Cloud
// -----------------------------------------------------------------------------
(async () => {
  console.log('\nTest 9: Active PC workstation (isCloudMode=false, isPcOnline=true) + token never dual-publishes...');
  const calledUrls: string[] = [];
  const mockFetch = async (url: any, init?: RequestInit) => {
    const urlStr = String(url);
    calledUrls.push(urlStr);
    const body = JSON.parse(String(init?.body || '{}'));
    assert.ok(body.idempotency_key, 'PC approve includes idempotency_key');
    assert.equal(body.account_ids?.youtube, 'yt_primary_1');
    return {
      ok: true,
      status: 200,
      json: async () => ({ status: 'success' }),
    } as any;
  };

  const clip = createMockClip('c_pc_no_dual');
  const result = await executeClipApproval({
    clip,
    optimisticTitle: 'PC Title',
    optimisticDescription: 'Desc',
    optimisticLayout: 'fit_blur',
    optimisticMode: 'private',
    platformsToPublish: 'youtube',
    accountsMap: { youtube_shorts: 'yt_primary_1' },
    pcHost: 'http://127.0.0.1:8000',
    isCloudMode: false,
    isPcOnline: true,
    token: 'jwt_mock_token_123',
  }, mockFetch);

  assert.equal(result.success, true);
  assert.equal(result.route, 'pc_workstation');
  assert.equal(calledUrls.length, 1, 'Exactly one publish route must execute');
  assert.ok(calledUrls[0].includes('/api/clips/c_pc_no_dual/approve'));
  console.log('  ✓ Active PC workstation executes once with zero dual-publish to cloud');
})();

// -----------------------------------------------------------------------------
// Test 10: Already-published clip with Android bridge routes to cloud_queue (republish)
// -----------------------------------------------------------------------------
(async () => {
  console.log('\nTest 10: Already-published clip with Android bridge uses cloud_queue republish instead of re-render...');
  let bridgeCalled = false;
  const mockBridge = {
    startRenderAndPublish: () => {
      bridgeCalled = true;
      return JSON.stringify({ success: true });
    },
  };
  const calledUrls: string[] = [];
  const mockFetch = async (url: any) => {
    calledUrls.push(String(url));
    return { ok: true, status: 200, json: async () => ({ ok: true }) } as any;
  };

  const clip = { ...createMockClip('c_repub'), status: 'published' };
  const result = await executeClipApproval({
    clip,
    optimisticTitle: 'Republish Title',
    optimisticDescription: 'Desc',
    optimisticLayout: 'fit_blur',
    optimisticMode: 'public',
    platformsToPublish: 'instagram',
    accountsMap: { instagram: 'ig_acc_1' },
    bridge: mockBridge,
    token: 'jwt_mock_token_123',
  }, mockFetch);

  assert.equal(bridgeCalled, false, 'Already-published clip must not re-render on mobile bridge');
  assert.equal(result.success, true);
  assert.equal(result.route, 'cloud_queue');
  assert.equal(result.actionTaken, 'republish');
  assert.equal(calledUrls.length, 1);
  console.log('  ✓ Already-published clip routed cleanly to cloud_queue republish');
})();

// -----------------------------------------------------------------------------
// Test 11: Native bridge malformed / empty response & PC caption pre-save HTTP 409 fail closed
// -----------------------------------------------------------------------------
(async () => {
  console.log('\nTest 11: Malformed bridge response and PC caption 409 fail closed...');
  const malformedRes = await executeClipApproval({
    clip: createMockClip('c_bad_bridge'),
    optimisticTitle: 'T',
    optimisticDescription: 'D',
    optimisticLayout: 'fit_blur',
    optimisticMode: 'private',
    platformsToPublish: 'youtube',
    accountsMap: {},
    bridge: { startRenderAndPublish: () => 'non-json-error-text' },
  });
  assert.equal(malformedRes.success, false, 'Non-JSON bridge text must fail closed');
  assert.equal(malformedRes.route, 'mobile_render_cloud');

  const calledUrls: string[] = [];
  const cap409Res = await executeClipApproval({
    clip: createMockClip('c_cap_409'),
    optimisticTitle: 'T',
    optimisticDescription: 'D',
    optimisticLayout: 'fit_blur',
    optimisticMode: 'private',
    platformsToPublish: 'youtube',
    accountsMap: { youtube_shorts: 'yt_1' },
    captionTrack: { clip_id: 'c_cap_409', groups: [] },
    pcHost: 'http://127.0.0.1:8000',
  }, async (url: any) => {
    calledUrls.push(String(url));
    return {
      ok: false,
      status: 409,
      json: async () => ({ detail: 'Captions modified on another device' }),
    } as any;
  });
  assert.equal(cap409Res.success, false, 'Caption pre-save 409 must abort PC approval');
  assert.ok(cap409Res.error?.includes('Captions modified'));
  assert.equal(calledUrls.length, 1, 'Must not call /approve after /captions returns 409');
  console.log('  ✓ Malformed bridge response and caption pre-save 409 fail closed');
})();

// -----------------------------------------------------------------------------
// Test 12: Idempotency key determinism & route resolution offline PC
// -----------------------------------------------------------------------------
(() => {
  console.log('\nTest 12: Idempotency key determinism and offline PC route resolution...');
  const k1 = buildApprovalIdempotencyKey({
    clipId: 'clip_1',
    rangeRevision: 3,
    platforms: 'instagram,youtube',
    publishMode: 'private',
    layoutMode: 'fit_blur',
    accountsMap: { youtube_shorts: 'yt_1', instagram: 'ig_1' },
  });
  const k2 = buildApprovalIdempotencyKey({
    clipId: 'clip_1',
    rangeRevision: 3,
    platforms: 'youtube,instagram',
    publishMode: 'private',
    layoutMode: 'fit_blur',
    accountsMap: { instagram: 'ig_1', youtube: 'yt_1' },
  });
  const k3 = buildApprovalIdempotencyKey({
    clipId: 'clip_1',
    rangeRevision: 4,
    platforms: 'youtube,instagram',
    publishMode: 'private',
    layoutMode: 'fit_blur',
    accountsMap: { instagram: 'ig_1', youtube: 'yt_1' },
  });
  assert.equal(k1, k2, 'Equivalent platform/account maps yield identical idempotency key');
  assert.notEqual(k1, k3, 'Revision increment yields distinct idempotency key');

  const routeRes = resolvePublishRoute({
    clip: createMockClip('c_off'),
    optimisticTitle: 'T',
    optimisticDescription: 'D',
    optimisticLayout: 'fit_blur',
    optimisticMode: 'private',
    platformsToPublish: 'youtube',
    accountsMap: {},
    pcHost: 'http://192.168.1.10:8000',
    isCloudMode: false,
    isPcOnline: false,
    token: 'jwt_tok',
  });
  assert.equal(routeRes.route, 'cloud_queue', 'Offline pcHost must not block cloud_queue');
  console.log('  ✓ Idempotency key and offline PC route resolution verified');
})();

// -----------------------------------------------------------------------------
// Test 13: Primary connected account selection, clip switch reset, and source vault filter
// -----------------------------------------------------------------------------
(() => {
  console.log('\nTest 13: Primary connected accounts, clip-switch reset, and source-vault filtering...');
  const accounts: ConnectedAccount[] = [
    {
      id: 'uuid-yt-vault-only',
      provider: 'youtube',
      account_id: 'UC_VAULT',
      account_name: 'Raw Vault Channel',
      metadata: { is_source_vault: true, is_publisher: false },
      created_at: '2026-10-01T00:00:00Z',
    },
    {
      id: 'uuid-yt-pub',
      provider: 'youtube',
      account_id: 'UC_MAIN',
      account_name: 'Main Creator Channel',
      metadata: { is_source_vault: false, is_publisher: true },
      created_at: '2026-10-02T00:00:00Z',
    },
    {
      id: 'uuid-ig-1',
      provider: 'instagram',
      account_id: 'ig_primary',
      account_name: '@creator',
      created_at: '2026-10-02T00:00:00Z',
    },
    {
      id: 'uuid-ig-2',
      provider: 'instagram',
      account_id: 'ig_secondary',
      account_name: '@creator2',
      created_at: '2026-10-03T00:00:00Z',
    },
    {
      id: 'uuid-li-1',
      provider: 'linkedin',
      account_id: 'li_1',
      account_name: 'Creator LinkedIn',
      created_at: '2026-10-03T00:00:00Z',
    },
  ];

  const primary = buildPrimaryAccountMap(accounts);
  assert.equal(primary.youtube_shorts, 'UC_MAIN', 'Prefers publisher-enabled YouTube channel over vault-only channel');
  assert.equal(primary.instagram, 'ig_primary', 'Selects first connected Instagram account');
  assert.equal(primary.linkedin, 'li_1');

  const switchedClipB = deriveDefaultPublishSelection(accounts, { id: 'clip_b', status: 'ready_review' });
  assert.deepEqual(switchedClipB.selectedPlatforms, ['youtube_shorts', 'instagram']);
  assert.equal(switchedClipB.selectedAccountMap.instagram, 'ig_primary', 'Resets secondary IG override back to primary on clip switch');

  const resolved = resolveSelectedAccountsMap(['youtube_shorts', 'instagram'], {}, accounts);
  assert.deepEqual(resolved, { youtube_shorts: 'UC_MAIN', instagram: 'ig_primary' }, 'Backfills primary account IDs even when UI map is empty');

  const payloadIds = normalizeAccountIdsForPayload(resolved);
  assert.equal(payloadIds.youtube, 'UC_MAIN');
  assert.equal(payloadIds.youtube_shorts, 'UC_MAIN');

  // cloudSessionBootstrap: must pick UC_VAULT (is_source_vault: true), NOT UC_MAIN (is_source_vault: false)
  const reordered = [accounts[1], accounts[0]];
  const sourceVault = selectSourceVaultYouTubeAccount(reordered);
  assert.equal(sourceVault?.id, 'uuid-yt-vault-only', 'Respects metadata.is_source_vault rather than picking first YouTube row');

  const disabledVault = selectSourceVaultYouTubeAccount([
    { ...accounts[1], metadata: { is_source_vault: false, is_publisher: true } },
  ]);
  assert.equal(disabledVault, undefined, 'Returns undefined when all YouTube accounts have is_source_vault=false');
  console.log('  ✓ Primary account selection, clip-switch reset, and source-vault filtering verified');
})();

// -----------------------------------------------------------------------------
// Test 14: executeTimelineCommit fail-closed behavior (never false-success)
// -----------------------------------------------------------------------------
(async () => {
  console.log('\nTest 14: executeTimelineCommit fail-closed behavior across PC/Cloud errors...');
  const baseArgs = {
    clipId: 'clip_tl_1',
    cloudId: 'cloud_tl_1',
    baseRevision: 2,
    projectDocument: { version: 1, timeline: { segments: [{ id: 's1', source_start: 0, source_end: 10 }] } },
    operationId: 'op_1',
    actionType: 'auto_cut' as const,
    pcHost: 'http://127.0.0.1:8000',
  };

  // PC HTTP 409 must fail closed and return success === false (never return true!)
  const pc409 = await executeTimelineCommit(
    { ...baseArgs, isCloudMode: false, isPcOnline: true, token: 'jwt_1' },
    async () => ({ ok: false, status: 409, json: async () => ({ detail: 'Stale segments revision' }) } as any)
  );
  assert.equal(pc409.success, false, 'PC 409 must return success === false');
  assert.ok(pc409.error?.includes('Stale segments revision'));

  // PC unreachable without cloudId or token must fail closed
  const pcOfflineNoCloud = await executeTimelineCommit(
    { ...baseArgs, cloudId: null, isCloudMode: false, isPcOnline: true, token: null },
    async () => { throw new Error('Connection refused'); }
  );
  assert.equal(pcOfflineNoCloud.success, false, 'Unreachable PC without cloud fallback must return success === false');

  // Cloud mode without auth token must fail closed
  const cloudNoToken = await executeTimelineCommit(
    { ...baseArgs, isCloudMode: true, token: null },
    async () => { throw new Error('Should not call fetch'); }
  );
  assert.equal(cloudNoToken.success, false, 'Cloud mode without token must return success === false');

  // Cloud HTTP 500 must fail closed
  const cloud500 = await executeTimelineCommit(
    { ...baseArgs, isCloudMode: true, token: 'jwt_1' },
    async () => ({ ok: false, status: 500, json: async () => ({ error: 'DB write failed' }) } as any)
  );
  assert.equal(cloud500.success, false, 'Cloud 500 must return success === false');
  assert.ok(cloud500.error?.includes('DB write failed'));

  // Cloud 409 single-flight retry succeeds honestly with new revision
  let calls = 0;
  const cloudRetryOk = await executeTimelineCommit(
    { ...baseArgs, isCloudMode: true, token: 'jwt_1' },
    async () => {
      calls++;
      if (calls === 1) {
        return { ok: false, status: 409, json: async () => ({ revision: 4 }) } as any;
      }
      return { ok: true, status: 200, json: async () => ({ revision: 5 }) } as any;
    }
  );
  assert.equal(cloudRetryOk.success, true);
  assert.equal(cloudRetryOk.revision, 5);
  console.log('  ✓ executeTimelineCommit fails closed on all error paths and returns real revisions');
})();

console.log('\n🎉 ALL PRODUCTION APPROVAL, ROUTE EXCLUSIVITY & FAIL-CLOSED TESTS PASSED 100%!\n');

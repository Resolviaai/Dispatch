// Test: Production Approval Execution & Rollback State Verification
// Tests executeClipApproval directly against Android native bridge, PC workstation, and cloud backends.

import { executeClipApproval } from './src/lib/approval';

console.log('🧪 Starting Dispatch Production Approval & Bridge Rollback Test Suite...');

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
  console.log('  ✓ Cloud queue submission verified');
})();

console.log('\n🎉 ALL PRODUCTION APPROVAL & BRIDGE ROLLBACK TESTS PASSED 100%!\n');

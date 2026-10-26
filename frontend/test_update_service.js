import assert from 'assert';
import { checkAppUpdate } from './src/update/updateService';
import { Bridge } from './src/bridge';

async function runUpdateTests() {
  console.log('🧪 Starting Dispatch In-App Update Service Test Suite...\n');

  // Test 1: Fallback app version info when bridge not present
  console.log('Test 1: App version info resolution...');
  const vInfo = Bridge.getAppVersionInfo();
  assert(typeof vInfo.versionCode === 'number' && vInfo.versionCode > 0, 'versionCode must be a positive integer');
  assert(typeof vInfo.versionName === 'string' && vInfo.versionName.length > 0, 'versionName must be non-empty');
  assert.strictEqual(vInfo.packageName, 'com.resolvia.dispatch', 'packageName must match invariant');
  console.log(`  ✓ Resolved version: ${vInfo.versionName} (code ${vInfo.versionCode})`);

  // Test 2: Update detection with mocked fetch
  console.log('Test 2: Update comparison logic (newer version available)...');
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({
        schemaVersion: 1,
        packageName: 'com.resolvia.dispatch',
        versionCode: vInfo.versionCode + 1,
        versionName: '9.9.9',
        releaseTag: 'v9.9.9-alpha',
        generatedAt: '2026-10-27T00:00:00Z',
        releaseNotes: 'Test release notes for next update',
        apkAsset: 'Dispatch.apk',
        apkSizeBytes: 22000000,
        apkSha256: 'abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890',
      }),
    });

    const result = await checkAppUpdate();
    assert.strictEqual(result.hasUpdate, true, 'Should detect update available when remote code > local code');
    assert.strictEqual(result.latestManifest?.versionCode, vInfo.versionCode + 1);
    assert.strictEqual(result.downloadUrl, 'https://github.com/Resolviaai/Dispatch/releases/download/v9.9.9-alpha/Dispatch.apk');
    assert.strictEqual(result.directMirrorUrl, 'https://resolviaai.github.io/Dispatch/Dispatch.apk');
    console.log('  ✓ Detected available update accurately with correct release download URLs');

    // Test 3: Up-to-date when remote code == local code
    console.log('Test 3: Up-to-date detection (same version)...');
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({
        schemaVersion: 1,
        packageName: 'com.resolvia.dispatch',
        versionCode: vInfo.versionCode,
        versionName: vInfo.versionName,
        releaseTag: `v${vInfo.versionName}-alpha`,
        generatedAt: '2026-10-27T00:00:00Z',
        releaseNotes: 'Current version',
        apkAsset: 'Dispatch.apk',
        apkSizeBytes: 21000000,
        apkSha256: 'abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890',
      }),
    });

    const upToDateResult = await checkAppUpdate();
    assert.strictEqual(upToDateResult.hasUpdate, false, 'Should be up to date when codes match');
    console.log('  ✓ Correctly confirmed up-to-date state');

    // Test 4: Offline / network error resilience
    console.log('Test 4: Network error resilience (fail-soft)...');
    globalThis.fetch = async () => {
      throw new Error('Connection refused');
    };

    const errorResult = await checkAppUpdate();
    assert.strictEqual(errorResult.hasUpdate, false);
    assert(errorResult.error && errorResult.error.includes('Connection refused'), 'Must capture error message without crashing');
    console.log('  ✓ Captured offline/network failure safely without throwing uncaught exceptions');
  } finally {
    globalThis.fetch = originalFetch;
  }

  console.log('\n🎉 ALL 4 IN-APP UPDATE TESTS PASSED 100%!');
}

runUpdateTests().catch((err) => {
  console.error('❌ Update test failed:', err);
  process.exit(1);
});

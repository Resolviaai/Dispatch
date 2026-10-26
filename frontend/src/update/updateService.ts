import { Bridge, AppVersionInfo } from '../bridge';

export interface ReleaseManifest {
  schemaVersion: number;
  packageName: string;
  versionCode: number;
  versionName: string;
  releaseTag: string;
  generatedAt: string;
  releaseNotes: string;
  apkAsset: string;
  apkSizeBytes: number;
  apkSha256: string;
}

export interface UpdateCheckResult {
  hasUpdate: boolean;
  currentVersion: AppVersionInfo;
  latestManifest?: ReleaseManifest;
  downloadUrl?: string;
  directMirrorUrl?: string;
  error?: string;
}

export const UPDATE_MANIFEST_URL = 'https://resolviaai.github.io/Dispatch/update.json';
export const DIRECT_APK_MIRROR_URL = 'https://resolviaai.github.io/Dispatch/Dispatch.apk';

/**
 * Checks for updates against the authoritative update.json hosted on GitHub Pages.
 * Enforces strict Android integer versionCode monotonicity.
 */
export async function checkAppUpdate(): Promise<UpdateCheckResult> {
  const current = Bridge.getAppVersionInfo();
  try {
    const res = await fetch(`${UPDATE_MANIFEST_URL}?t=${Date.now()}`, {
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
      },
    });

    if (!res.ok) {
      throw new Error(`Server returned HTTP ${res.status}`);
    }

    const manifest: ReleaseManifest = await res.json();

    if (!manifest || typeof manifest.versionCode !== 'number') {
      throw new Error('Invalid update manifest format');
    }

    // Android versionCode authority: an update is only offered if candidate code > installed code
    const hasUpdate = manifest.versionCode > current.versionCode;
    const downloadUrl = `https://github.com/Resolviaai/Dispatch/releases/download/${manifest.releaseTag}/Dispatch.apk`;

    return {
      hasUpdate,
      currentVersion: current,
      latestManifest: manifest,
      downloadUrl,
      directMirrorUrl: DIRECT_APK_MIRROR_URL,
    };
  } catch (err: any) {
    return {
      hasUpdate: false,
      currentVersion: current,
      error: err?.message || 'Network error checking for updates',
    };
  }
}

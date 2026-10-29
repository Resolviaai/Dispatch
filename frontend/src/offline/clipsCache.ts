import type { ClipItem } from '../screens/ClipsScreen';

const DB_NAME = 'dispatch-offline-v1';
const DB_VERSION = 1;
const SNAPSHOT_STORE = 'clipSnapshots';
const THUMBNAIL_STORE = 'clipThumbnails';
const THUMBNAIL_INDEX = 'userId';
const MAX_THUMBNAIL_BYTES = 1024 * 1024;

export interface ClipsCachePayload {
  ready: ClipItem[];
  recent_approved: ClipItem[];
  rejected: unknown[];
  queue_stats: unknown;
}

export interface ClipsCacheSnapshot {
  userId: string;
  savedAt: number;
  source: 'pc' | 'cloud';
  payload: ClipsCachePayload;
}

interface ThumbnailRecord {
  key: string;
  userId: string;
  clipId: string;
  thumbnailVersion: string;
  blob: Blob;
  savedAt: number;
}

let databasePromise: Promise<IDBDatabase> | null = null;

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('IndexedDB request failed'));
  });
}

function transactionDone(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error('IndexedDB transaction failed'));
    tx.onabort = () => reject(tx.error || new Error('IndexedDB transaction aborted'));
  });
}

function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === 'undefined') {
    return Promise.reject(new Error('IndexedDB is unavailable'));
  }
  if (databasePromise) return databasePromise;

  const opening = new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(SNAPSHOT_STORE)) {
        db.createObjectStore(SNAPSHOT_STORE, { keyPath: 'userId' });
      }
      if (!db.objectStoreNames.contains(THUMBNAIL_STORE)) {
        const store = db.createObjectStore(THUMBNAIL_STORE, { keyPath: 'key' });
        store.createIndex(THUMBNAIL_INDEX, 'userId', { unique: false });
      }
    };
    request.onsuccess = () => {
      const db = request.result;
      db.onversionchange = () => {
        db.close();
        if (databasePromise === opening) databasePromise = null;
      };
      resolve(db);
    };
    request.onerror = () => reject(request.error || new Error('IndexedDB open failed'));
    request.onblocked = () => reject(new Error('IndexedDB upgrade blocked'));
  });

  databasePromise = opening;
  opening.catch(() => {
    if (databasePromise === opening) databasePromise = null;
  });
  return opening;
}

function thumbnailVersionOf(clip: ClipItem): string {
  return String(clip.thumbnail_version ?? clip.range_revision ?? 0);
}

function thumbnailKey(userId: string, clip: ClipItem): string {
  return `${encodeURIComponent(userId)}:${encodeURIComponent(clip.id)}:${encodeURIComponent(thumbnailVersionOf(clip))}`;
}

export async function readClipsCache(userId: string): Promise<ClipsCacheSnapshot | null> {
  if (!userId) return null;
  try {
    const db = await openDatabase();
    const tx = db.transaction(SNAPSHOT_STORE, 'readonly');
    const snapshot = await requestResult<ClipsCacheSnapshot | undefined>(
      tx.objectStore(SNAPSHOT_STORE).get(userId)
    );
    await transactionDone(tx);
    if (!snapshot || snapshot.userId !== userId || !snapshot.payload) return null;
    if (!Array.isArray(snapshot.payload.ready) || !Array.isArray(snapshot.payload.recent_approved)) {
      return null;
    }
    return snapshot;
  } catch {
    return null;
  }
}

export async function writeClipsCache(
  userId: string,
  source: 'pc' | 'cloud',
  payload: ClipsCachePayload
): Promise<void> {
  if (!userId) return;
  const db = await openDatabase();
  const snapshot: ClipsCacheSnapshot = {
    userId,
    savedAt: Date.now(),
    source,
    payload,
  };
  const tx = db.transaction(SNAPSHOT_STORE, 'readwrite');
  tx.objectStore(SNAPSHOT_STORE).put(snapshot);
  await transactionDone(tx);
}

async function listThumbnailKeys(userId: string): Promise<Set<string>> {
  const db = await openDatabase();
  const tx = db.transaction(THUMBNAIL_STORE, 'readonly');
  const index = tx.objectStore(THUMBNAIL_STORE).index(THUMBNAIL_INDEX);
  const records = await requestResult<ThumbnailRecord[]>(
    index.getAll(IDBKeyRange.only(userId))
  );
  await transactionDone(tx);
  return new Set(records.map((record) => record.key));
}

async function listThumbnailRecords(userId: string): Promise<ThumbnailRecord[]> {
  const db = await openDatabase();
  const tx = db.transaction(THUMBNAIL_STORE, 'readonly');
  const index = tx.objectStore(THUMBNAIL_STORE).index(THUMBNAIL_INDEX);
  const records = await requestResult<ThumbnailRecord[]>(
    index.getAll(IDBKeyRange.only(userId))
  );
  await transactionDone(tx);
  return records;
}

export async function cacheClipThumbnails(
  userId: string,
  clips: ClipItem[],
  shouldAttempt?: (clip: ClipItem) => boolean
): Promise<void> {
  if (!userId || clips.length === 0) return;
  const existingKeys = await listThumbnailKeys(userId);
  const currentKeys = new Set(clips.map((clip) => thumbnailKey(userId, clip)));

  const db = await openDatabase();
  for (const clip of clips) {
    if (!clip.thumbnail_url) continue;
    const key = thumbnailKey(userId, clip);
    if (existingKeys.has(key)) continue;
    if (shouldAttempt && !shouldAttempt(clip)) continue;
    try {
      const response = await fetch(clip.thumbnail_url, {
        cache: 'no-store',
        credentials: 'omit',
      });
      if (!response.ok) continue;
      const blob = await response.blob();
      if (!blob.type.startsWith('image/') || blob.size <= 0 || blob.size > MAX_THUMBNAIL_BYTES) {
        continue;
      }
      const tx = db.transaction(THUMBNAIL_STORE, 'readwrite');
      tx.objectStore(THUMBNAIL_STORE).put({
        key,
        userId,
        clipId: clip.id,
        thumbnailVersion: thumbnailVersionOf(clip),
        blob,
        savedAt: Date.now(),
      } satisfies ThumbnailRecord);
      await transactionDone(tx);
      existingKeys.add(key);
    } catch {
      // A thumbnail is optional; a failed cache fetch must not affect the list.
    }
  }

  const staleKeys: string[] = [];
  for (const key of existingKeys) {
    if (!currentKeys.has(key)) staleKeys.push(key);
  }
  if (staleKeys.length > 0) {
    const tx = db.transaction(THUMBNAIL_STORE, 'readwrite');
    const store = tx.objectStore(THUMBNAIL_STORE);
    staleKeys.forEach((key) => store.delete(key));
    await transactionDone(tx);
  }
}

export async function cachedThumbnailUrls(userId: string, clips: ClipItem[]): Promise<Map<string, string>> {
  const urls = new Map<string, string>();
  if (!userId || clips.length === 0 || typeof URL.createObjectURL === 'undefined') {
    return urls;
  }
  try {
    const keys = new Set(clips.map((clip) => thumbnailKey(userId, clip)));
    const records = await listThumbnailRecords(userId);
    for (const record of records) {
      if (!keys.has(record.key) || !(record.blob instanceof Blob)) continue;
      urls.set(record.clipId, URL.createObjectURL(record.blob));
    }
  } catch {
    // The cached list remains usable without thumbnails.
  }
  return urls;
}

export async function clearClipsCache(userId: string): Promise<void> {
  if (!userId) return;
  try {
    const db = await openDatabase();
    const readTx = db.transaction(THUMBNAIL_STORE, 'readonly');
    const index = readTx.objectStore(THUMBNAIL_STORE).index(THUMBNAIL_INDEX);
    const keys = await requestResult<IDBValidKey[]>(index.getAllKeys(IDBKeyRange.only(userId)));
    await transactionDone(readTx);

    const writeTx = db.transaction([SNAPSHOT_STORE, THUMBNAIL_STORE], 'readwrite');
    writeTx.objectStore(SNAPSHOT_STORE).delete(userId);
    const store = writeTx.objectStore(THUMBNAIL_STORE);
    keys.forEach((key) => store.delete(key));
    await transactionDone(writeTx);
  } catch {
    // Account-scoped reads still prevent another user from seeing this cache.
  }
}

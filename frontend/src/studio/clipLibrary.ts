import type { ClipItem } from './types';

/** Parses only the response shapes supported by the clip-list API. */
export function parseClipListResponse(data: unknown): ClipItem[] {
  let clips: unknown[];
  if (Array.isArray(data)) {
    clips = data;
  } else if (data && typeof data === 'object') {
    const lists = data as Record<string, unknown>;
    const supportedKeys = ['ready', 'recent_approved', 'ready_for_review'];
    if (!supportedKeys.some((key) => Array.isArray(lists[key]))) {
      throw new Error('The clip library returned an invalid response');
    }
    clips = supportedKeys.flatMap((key) => Array.isArray(lists[key]) ? lists[key] as unknown[] : []);
  } else {
    throw new Error('The clip library returned an invalid response');
  }

  if (!clips.every((clip) => Boolean(
    clip && typeof clip === 'object' && typeof (clip as { id?: unknown }).id === 'string' && (clip as { id: string }).id.trim()
  ))) {
    throw new Error('The clip library returned an invalid clip entry');
  }
  return clips as ClipItem[];
}

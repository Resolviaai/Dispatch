import { AutoCutProposal } from './SpeechCleanupWorkspace';
import { Word } from './types';
import { roundMs } from './timelineEngine';

const GEMINI_FLASH_LITE_MODELS = [
  'gemini-3.5-flash-lite',
  'gemini-3.1-flash-lite',
  'gemini-2.5-flash-lite',
  'gemini-2.0-flash-lite',
];

interface RawGeminiCutProposal {
  word?: string;
  start?: number;
  end?: number;
  type?: 'filler' | 'false_start' | 'dead_air' | string;
  decision?: 'AUTO-REMOVE' | 'REVIEW' | 'PRESERVE';
  reason?: string;
  confidence?: number;
}

/**
 * Direct Client-Side Gemini Flash-Lite Speech Cleanup Engine.
 *
 * Runs 100% directly from the phone to Google AI Studio with zero PC server dependency.
 * Analyzes word-level transcripts for Hindi, Hinglish, and English creators.
 */
export async function analyzeSpeechWithGeminiFlashLite(
  words: Word[],
  apiKey: string,
  clipStart: number = 0,
  clipEnd: number = 60
): Promise<AutoCutProposal[]> {
  if (!words || words.length === 0 || !apiKey) {
    return [];
  }

  // Filter words strictly within current clip range
  const inRangeWords = words
    .filter((w) => w && Number.isFinite(w.start) && Number.isFinite(w.end) && w.end > w.start)
    .filter((w) => w.end > clipStart && w.start < clipEnd)
    .sort((a, b) => a.start - b.start);

  if (inRangeWords.length === 0) {
    return [];
  }

  // Compact transcript payload for minimal token usage
  const compactTranscript = inRangeWords.map((w, idx) => ({
    i: idx,
    w: w.text,
    s: roundMs(w.start),
    e: roundMs(w.end),
  }));

  const systemInstruction = `You are an expert speech cleanup editor specializing in videos by Indian creators speaking Hindi, Hinglish, and English.
You are given a list of chronological transcript words with start and end timestamps in seconds.
Identify:
1. Unintended conversational fillers & stalling sounds:
   - Stalling 'matlab', 'haan toh', 'samjho', 'arre', 'yaar' when used solely as an empty hesitation stall before a phrase.
   - Conversational fillers in Hinglish: 'like', 'um', 'uh', 'you know', 'basically', 'actually', 'sort of'.
2. Mechanical stutters and repeated false starts:
   - When the speaker repeats 1-3 words (e.g. "phir tut... phir tut jaati"), propose cutting the redundant initial attempt.
3. Abandoned sentence attempts.

CRITICAL INVARIANTS:
- Strictly PRESERVE legitimate vocabulary:
  * 'matlab' when asking or giving meaning/definition (e.g., 'iska matlab kya hai')
  * 'achha' when used as an adjective or compliment ('achha kaam', 'bohot achha')
  * 'like' when used as a verb ('I like this') or comparison ('looks like')
  * 'toh' when used as a grammatical connector ('agar ... toh')
- Strictly PRESERVE rhetorical pauses, storytelling cadence, and deliberate emphasis.
- Unambiguous hesitation sounds ('um', 'uh', pure grunts) should be AUTO-REMOVE.
- Ambiguous discourse markers ('like', 'matlab', 'so') should be REVIEW so the creator can decide.

Return ONLY a valid JSON array of objects with the exact schema:
[
  {
    "word": "string",
    "start": number,
    "end": number,
    "type": "filler" | "false_start" | "dead_air",
    "decision": "AUTO-REMOVE" | "REVIEW" | "PRESERVE",
    "reason": "Brief explanation",
    "confidence": number
  }
]`;

  const promptText = `Analyze this word-level transcript from a Hindi/Hinglish creator and return speech cleanup cut proposals:\n${JSON.stringify(compactTranscript)}`;

  let lastError: Error | null = null;

  for (const model of GEMINI_FLASH_LITE_MODELS) {
    try {
      const endpoint = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(apiKey)}`;

      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          contents: [
            {
              role: 'user',
              parts: [{ text: `${systemInstruction}\n\n${promptText}` }],
            },
          ],
          generationConfig: {
            responseMimeType: 'application/json',
            temperature: 0.1,
          },
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.warn(`Gemini model ${model} returned ${response.status}:`, errorText);
        // If model not found (404) or bad request (400), try fallback model
        if (response.status === 404 || response.status === 400) {
          continue;
        }
        throw new Error(`Gemini API error (${response.status}): ${errorText}`);
      }

      const result = await response.json();
      const rawText = result.candidates?.[0]?.content?.parts?.[0]?.text;
      if (!rawText) {
        return [];
      }

      const parsed: RawGeminiCutProposal[] = JSON.parse(rawText);
      if (!Array.isArray(parsed)) {
        return [];
      }

      const proposals: AutoCutProposal[] = parsed
        .filter((p) => Number.isFinite(p.start) && Number.isFinite(p.end) && (p.end ?? 0) > (p.start ?? 0))
        .map((p, idx) => {
          const s = Math.max(clipStart, Number(p.start));
          const e = Math.min(clipEnd, Number(p.end));
          const duration = roundMs(e - s);
          return {
            id: `gemini_cut_${idx + 1}`,
            type: p.type || 'filler',
            source_start: roundMs(s),
            source_end: roundMs(e),
            duration,
            reason: p.reason || 'Gemini Flash-Lite speech cleanup',
            confidence: Number(p.confidence ?? 0.9),
            decision: ((p.decision === 'AUTO-REMOVE' || p.decision === 'PRESERVE') ? p.decision : 'REVIEW') as 'AUTO-REMOVE' | 'REVIEW' | 'PRESERVE',
            word: p.word,
          };
        })
        .filter((p) => p.duration > 0.05);

      return proposals;
    } catch (err: any) {
      lastError = err;
      console.warn(`Attempt with ${model} failed:`, err);
    }
  }

  if (lastError) {
    throw lastError;
  }

  return [];
}

import { frameAge, type FrameTiming } from "./vision-frame";

export const PREVIEW_INTERVAL_MS = 500;
// Matches the existing LoopDecision display limit, not the Pi motion deadline.
export const PREVIEW_MAX_AGE_MS = 1000;

export function nextPreviewDelay(started: number, finished: number, interval = PREVIEW_INTERVAL_MS): number {
  return Math.max(0, interval - Math.max(0, finished - started));
}

export function previewLifetime(frame: FrameTiming | null, now = performance.now()): number | null {
  const age = frameAge(frame, now);
  return age === null ? null : Math.max(0, PREVIEW_MAX_AGE_MS - age);
}

/** Keep the visible JPEG and its analysis paired while the next image loads.
 * A new candidate never renews the visible frame's freshness deadline.
 */
export function displayedPreview<T extends FrameTiming & { url: string; stale: boolean; knownFreshness: boolean }>(
  candidate: T | null, displayed: T | null, now = performance.now(),
): T | null {
  if (!candidate || !displayed) return null;
  const visible = candidate.url === displayed.url ? candidate : displayed;
  if (!candidate.stale && previewLifetime(visible, now) !== 0) return visible;
  return { ...visible, stale: true, knownFreshness: false };
}

type Decoder = { src: string; decode: () => Promise<void> };

/** Decode a candidate before replacing the currently visible image and analysis. */
export async function decodePreview(
  url: string,
  signal: AbortSignal,
  createImage: () => Decoder = () => new Image(),
): Promise<void> {
  if (signal.aborted) throw new DOMException("Preview cancelled", "AbortError");
  const image = createImage();
  let abort: (() => void) | undefined;
  try {
    await new Promise<void>((resolve, reject) => {
      abort = () => {
        image.src = "";
        reject(new DOMException("Preview cancelled", "AbortError"));
      };
      signal.addEventListener("abort", abort, { once: true });
      image.src = url;
      image.decode().then(resolve, reject);
    });
  } finally {
    if (abort) signal.removeEventListener("abort", abort);
  }
}


/** A handoff invalidates in-flight processed frames, even after polling resumes. */
export function previewRequestCurrent(
  kind: "camera" | "face", path: string, transitioning: boolean, requestEpoch: number, currentEpoch: number,
): boolean {
  return kind !== "camera" || path !== "/camera/venom" || !transitioning && requestEpoch === currentEpoch;
}


/** Busy retries may keep the original frame, never renew its timestamp or TTL. */
export function retainedPreview<T extends FrameTiming & { stale: boolean; knownFreshness: boolean }>(
  previous: T, busyRetry = false, now = performance.now(),
): T {
  if (previous.stale || busyRetry && previous.knownFreshness && (previewLifetime(previous, now) ?? 0) > 0) return previous;
  return { ...previous, stale: true, knownFreshness: false };
}

/** A late candidate cannot expire a different, still-valid displayed source. */
export function expiredNativeCandidate(error: unknown): boolean {
  return error instanceof Error && error.message === "stream_source_expired";
}

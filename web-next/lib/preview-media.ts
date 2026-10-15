import { frameAge, type FrameTiming } from "./vision-frame";

export const PREVIEW_INTERVAL_MS = 500;
// Matches the existing LoopDecision display limit, not the Pi motion deadline.
export const PREVIEW_MAX_AGE_MS = 1000;

export function nextPreviewDelay(started: number, finished: number): number {
  return Math.max(0, PREVIEW_INTERVAL_MS - Math.max(0, finished - started));
}

export function previewLifetime(frame: FrameTiming | null, now = performance.now()): number | null {
  const age = frameAge(frame, now);
  return age === null ? null : Math.max(0, PREVIEW_MAX_AGE_MS - age);
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

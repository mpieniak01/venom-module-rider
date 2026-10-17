"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-client";
import { data, errorCode, record, type JsonRecord, type Section } from "./model";
import { readCameraPipeline, type CameraPipeline, type CameraProfile } from "./camera-pipeline";

type Mutate = (path: string, body?: JsonRecord, method?: string, quiet?: boolean) => Promise<JsonRecord>;
export function useCameraPipeline(section: Section, session: string | undefined, mutate: Mutate, invalidate: () => void) {
  const [status, setStatus] = useState<CameraPipeline | null>(null);
  const [supported, setSupported] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sessionRef = useRef(session); sessionRef.current = session;
  const statusRef = useRef(status); statusRef.current = status;
  const locked = useRef(false);
  const readEpoch = useRef(0);
  const refresh = useCallback(async (signal?: AbortSignal, recovering = false) => {
    if (locked.current && !recovering) return;
    const origin = sessionRef.current, epoch = readEpoch.current;
    const started = performance.now();
    const response = await apiFetch<unknown>("/api/v1/rider/camera/pipeline", { signal, cache: "no-store" });
    const receivedAt = performance.now();
    if (signal?.aborted || origin !== sessionRef.current || epoch !== readEpoch.current) return;
    const row = data(response), next = readCameraPipeline(row);
    if (!next && record(row).supported !== false) throw new Error("camera_pipeline_invalid_status");
    if (next) next.previewAnchor = {latest: record(record(row).latest_frame), receivedAt, roundTripMs: receivedAt - started};
    statusRef.current = next; setStatus(next); setSupported(!!next);
    setError(next?.error ?? null);
  }, []);
  useEffect(() => {
    readEpoch.current++; statusRef.current = null; setStatus(null); setSupported(null); setError(null); setBusy(false);
    if (!session || !["control", "vision"].includes(section)) return;
    let active = true, timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const tick = async () => {
      try { await refresh(controller.signal); }
      catch (failure) { if (active && !controller.signal.aborted) setError(errorCode(failure)); }
      if (active) timer = setTimeout(() => void tick(), 1000);
    };
    void tick();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [section, session, refresh]);
  const change = useCallback(async (action: "configure" | "start" | "stop", profile?: CameraProfile) => {
    if (locked.current) return;
    locked.current = true; readEpoch.current++; setBusy(true); setError(null); invalidate();
    const origin = sessionRef.current;
    try {
      const current = statusRef.current;
      if (!current || !origin) throw new Error("camera_pipeline_unavailable");
      const response = await mutate("/camera/pipeline", {
        contract_version: 1, request_id: (globalThis.crypto?.randomUUID?.() || "camera-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2)), expected_revision: current.revision,
        action, ...(action === "configure" ? { profile } : {}),
      }, "POST", true);
      if (origin !== sessionRef.current) return;
      const next = readCameraPipeline(response);
      if (!next) throw new Error("camera_pipeline_invalid_status");
      statusRef.current = next; setStatus(next); setSupported(true); setError(next.error);
      return next;
    } catch (failure) {
      if (origin === sessionRef.current) {
        setError(errorCode(failure));
        readEpoch.current++;
        try { await refresh(undefined, true); } catch { /* Preserve the operation error on failed refresh. */ }
        setError(errorCode(failure));
      }
      throw failure;
    } finally { readEpoch.current++; locked.current = false; if (origin === sessionRef.current) setBusy(false); }
  }, [mutate, refresh, invalidate]);
  return { status, supported, busy, error, refresh, change };
}

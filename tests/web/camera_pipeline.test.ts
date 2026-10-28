import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { anchoredPreviewAge, preferredPreviewAnchor, cameraPreviewStopped, nativePreview, nativeAnalysisMatches, readCameraPipeline } from "../../web-next/lib/camera-pipeline";
import { MjpegParser, readMjpeg } from "../../web-next/lib/mjpeg";
import { previewLifetime, retainedPreview } from "../../web-next/lib/preview-media";
const fixture = JSON.parse(fs.readFileSync(new URL("../../config/contracts/camera_pipeline_v1.examples.json", import.meta.url), "utf8"));
const encoder = new TextEncoder();
const jpeg = new Uint8Array([255, 216, 1, 2, 255, 217]);
function wire(headers = "", bytes = jpeg) {
  const before = encoder.encode("--test\r\nContent-Type: image/jpeg\r\nContent-Length: " + bytes.length + "\r\n" + headers + "\r\n");
  const after = encoder.encode("\r\n--test--\r\n");
  return new Uint8Array([...before, ...bytes, ...after]);
}
test("shared status is producer-owned; inactive transport does not start native readers", () => {
  const s = readCameraPipeline(fixture.stopped_status);
  assert.ok(s);
  assert.equal(nativePreview(s, "raw"), false);
  const native = {...s, profile: {...s.profile, remote_transport: "mjpeg" as const}};
  assert.equal(nativePreview(native, "raw"), true);
  assert.equal(nativePreview(native, "venom"), true);
  assert.equal(nativePreview(native, "proc"), false);
  assert.equal(nativePreview({...native, profile: {...native.profile, analysis_location:"local"}}, "raw"), false);
  assert.equal(readCameraPipeline({supported: false}), null);
  assert.equal(readCameraPipeline({...fixture.stopped_status, revision: 0}), null);
});
test("multipart survives every byte boundary and multiple coalesced parts", () => {
  const full = wire("X-Rider-Frame-Id: same-source-8\r\n");
  for (let cut = 1; cut < full.length; cut++) {
    const parser = new MjpegParser('multipart/x-mixed-replace; boundary="test"');
    const parts = [...parser.feed(full.slice(0, cut)), ...parser.feed(full.slice(cut))];
    parser.finish(); assert.equal(parts.length, 1); assert.deepEqual(parts[0].bytes, jpeg);
    assert.equal(parts[0].headers.get("X-Rider-Frame-Id"), "same-source-8");
  }
  const each = wire().slice(0, -10), parser = new MjpegParser("multipart/x-mixed-replace; boundary=test");
  assert.equal(parser.feed(new Uint8Array([...each, ...wire()])).length, 2);
});
test("wire parser rejects truncation, oversized image, duplicate and broken identity headers", () => {
  const p = new MjpegParser("multipart/x-mixed-replace; boundary=test"); p.feed(wire().slice(0, 65));
  assert.throws(() => p.finish(), /truncated/);
  for (const header of ["Content-Length: 6\r\n", "X-Foo: a\r\nX-Foo: b\r\n"])
    assert.throws(() => new MjpegParser("multipart/x-mixed-replace; boundary=test").feed(wire(header)), /duplicate/);
  assert.throws(() => new MjpegParser("image/jpeg"), /content_type/);
  assert.throws(() => new MjpegParser("multipart/x-mixed-replace; boundary=test").feed(encoder.encode("--test\r\nContent-Type: image/jpeg\r\nContent-Length: 9000000\r\n\r\n")), /image_limit/);
  assert.throws(() => new MjpegParser("multipart/x-mixed-replace; boundary=test").feed(wire("", new Uint8Array([1, 2, 3, 4]))), /jpeg/);
});
test("abort cancels a pending stream reader and never presents a partial JPEG", async () => {
  let cancelled = false, offered = 0;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(wire().slice(0, 20)); },
    cancel() { cancelled = true; },
  });
  const abort = new AbortController();
  const done = readMjpeg(new Response(stream, {headers: {"Content-Type":"multipart/x-mixed-replace; boundary=test"}}), abort.signal, () => {offered++;});
  await new Promise(resolve => setImmediate(resolve)); abort.abort(); await done;
  assert.equal(cancelled, true); assert.equal(offered, 0);
});
test("freeze or reconnect retains capture age rather than renewing a green overlay", () => {
  const frame = {ageMs: 80, receivedAt: 10, stale: false, knownFreshness: true};
  assert.equal(previewLifetime(frame, 400), 530);
  const old = retainedPreview(frame, false, 400);
  assert.equal(old.receivedAt, 10); assert.equal(old.ageMs, 80); assert.equal(old.stale, true);
  assert.equal(previewLifetime(old, 1100), 0);
});

test("browser backlog age uses Pi capture differences, never a renewed header timestamp", () => {
  const source = fixture.source;
  const anchor = {latest: {...source, frame_age_ms: 1500}, receivedAt: 100, roundTripMs: 40};
  assert.equal(anchoredPreviewAge(source, 10, 160, anchor), 1600);
  assert.equal(anchoredPreviewAge({...source,captured_monotonic_ns:source.captured_monotonic_ns+500e6},10,160,anchor),1100);
  assert.equal(anchoredPreviewAge({...source,camera_session:"new"},10,160,anchor),10);
});

test("active Pi run preview binds camera source even when request sequence and SHA identifier differ", () => {
  const source = fixture.source;
  const reference = Object.fromEntries(["camera_session", "sequence", "transport_epoch", "pipeline_revision", "frame_id", "sha256"].map(key => [key, source[key]]));
  const analysis = {run_id: "physical-observe", frame_id: "a".repeat(64), sequence: 1, config_revision: 3, state: "obstacle", source: reference};
  assert.equal(nativeAnalysisMatches(analysis, source), true);
  for (const key of Object.keys(reference)) {
    assert.equal(nativeAnalysisMatches({...analysis, source: {...reference, [key]: typeof reference[key] === "number" ? Number(reference[key]) + 1 : "foreign"}}, source), false, key);
    const missing = {...reference}; delete missing[key];
    assert.equal(nativeAnalysisMatches({...analysis, source: missing}, source), false, key + " missing");
  }
  assert.equal(nativeAnalysisMatches(undefined, source), false);
  assert.equal(nativeAnalysisMatches({...analysis, source: undefined, frame_id: source.frame_id, sequence: source.sequence}, source), false);
});

test("confirmed camera OFF suspends both media paths; pending and legacy status do not", () => {
  const base=readCameraPipeline(fixture.stopped_status)!;
  assert.equal(cameraPreviewStopped(null),false);
  for(const state of ["stopped","failed"])assert.equal(cameraPreviewStopped({...base,state}),true);
  for(const state of ["starting","waiting_frame","active","stopping"])assert.equal(cameraPreviewStopped({...base,state}),false);
});

test("slow status refresh cannot replace a tighter recent camera age bound or renew old video",()=>{
  const common={camera_session:"camera",transport_epoch:2,pipeline_revision:3};
  const fast={latest:{...common,captured_monotonic_ns:5e9,frame_age_ms:5},receivedAt:10010,roundTripMs:10};
  const slow={latest:{...common,captured_monotonic_ns:6.2e9,frame_age_ms:5},receivedAt:11400,roundTripMs:600};
  assert.equal(preferredPreviewAnchor(fast,slow),fast);
  assert.equal(anchoredPreviewAge(slow.latest,0,11400,fast),205);
  assert.equal(anchoredPreviewAge(slow.latest,700,11400,fast),700);
  assert.ok(anchoredPreviewAge(fast.latest,0,11400,fast)>1000);
  const later={...slow,receivedAt:13000};
  assert.equal(preferredPreviewAnchor(fast,later),later);
  const replaced={...slow,latest:{...slow.latest,camera_session:"new"}};
  assert.equal(preferredPreviewAnchor(fast,replaced),replaced);
  const missing={...slow,latest:{}};
  assert.equal(preferredPreviewAnchor(fast,missing),missing);
});

test("a local or host preview cannot survive a changed pipeline revision or transport generation", async () => {
  const { cameraPipelineIdentity, framePipelineCurrent } = await import("../../web-next/lib/camera-pipeline");
  const pipeline = readCameraPipeline(fixture.stopped_status)!;
  const frame = { pipelineIdentity: cameraPipelineIdentity(pipeline) };
  assert.equal(framePipelineCurrent(frame, pipeline), true);
  assert.equal(framePipelineCurrent(frame, { ...pipeline, revision: pipeline.revision + 1 }), false);
  assert.equal(framePipelineCurrent(frame, { ...pipeline, transport_epoch: pipeline.transport_epoch + 1 }), false);
  assert.equal(framePipelineCurrent(frame, null), false);
  assert.equal(framePipelineCurrent({}, pipeline), true);
});


test("PROC metadata identifies the actual local engine, not the newly requested selection", async () => {
  const { cameraSourceMatches, readCameraSource, framePipelineCurrent } = await import("../../web-next/lib/camera-pipeline");
  const parsed = readCameraPipeline(fixture.stopped_status)!;
  const profile = { ...parsed.profile, analysis_location: "local" as const, local_engine: "ssd" as const };
  const pipeline = { ...parsed, profile, active_profile: profile, camera_session: "proc-session" };
  const source = { pipeline_revision: pipeline.revision, transport_epoch: pipeline.transport_epoch, camera_session: "proc-session", local_engine: "ssd", model_id: "local-ssd", analysis_location: "local" };
  assert.deepEqual(readCameraSource(JSON.stringify(source)), source);
  assert.equal(cameraSourceMatches(source, pipeline), true);
  assert.equal(framePipelineCurrent({ cameraSource: source }, pipeline), true);
  for (const patch of [{ local_engine: "canny" }, { model_id: "local-canny" }, { analysis_location: "venom" }, { camera_session: "old" }, { pipeline_revision: pipeline.revision + 1 }])
    assert.equal(cameraSourceMatches({ ...source, ...patch }, pipeline), false);
  assert.equal(cameraSourceMatches(source, { ...pipeline, active_profile: null }), false);
  assert.equal(framePipelineCurrent({ cameraSource: { ...source, model_id: "local-canny" } }, pipeline), false);
  assert.equal(readCameraSource("invalid"), undefined);
  assert.equal(readCameraSource("{}"), undefined);
  assert.equal(readCameraSource("a".repeat(4097)), undefined);
});


test("pipeline enum fields reject malformed JSON types without coercion", () => {
  const valid = fixture.stopped_status;
  for (const container of ["profile", "active_profile"]) {
    for (const field of ["analysis_location", "local_engine", "remote_transport"]) {
      const profile = { ...valid.profile };
      for (const invalid of [[profile[field]], [], {}, { toString: "bad" }, null, 1, true]) {
        const value = JSON.parse(JSON.stringify({
          ...valid, [container]: { ...profile, [field]: invalid },
        }));
        assert.equal(readCameraPipeline(value), null, container + "." + field);
      }
    }
  }
});

test("pipeline state requires a literal string and preserves valid transport choices", () => {
  const valid = fixture.stopped_status;
  for (const state of [["stopped"], ["active"], {}, { toString: "bad" }, null, 0, true]) {
    assert.equal(readCameraPipeline({ ...valid, state }), null);
  }
  for (const remote_transport of ["snapshots", "mjpeg"]) {
    const profile = { ...valid.profile, analysis_location: "venom", remote_transport };
    const parsed = readCameraPipeline({ ...valid, profile, active_profile: profile });
    assert.ok(parsed);
    assert.equal(cameraPreviewStopped(parsed), true);
    assert.equal(nativePreview(parsed, "raw"), remote_transport === "mjpeg");
  }
});

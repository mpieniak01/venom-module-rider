import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import type { CameraProfile } from "../lib/camera-pipeline";
import { pipelinePending } from "../lib/camera-pipeline";
import { Button, Field, Pill } from "./primitives";
import styles from "../rider.module.css";

export function CameraPipelineControls({ client, t }: { client: RiderClient; t: Translator }) {
  const pipeline = client.pipeline, status = pipeline.status;
  if (pipeline.supported === false) return <p className={styles.hint}>{t("pipeline.legacy")}</p>;
  if (!status) return <p className={styles.hint} role="status">{t(pipeline.error ? "pipeline.readFailed" : "pipeline.loading")}{pipeline.error && <code> · {pipeline.error}</code>}</p>;
  const disabled = !client.canMutate || pipeline.busy || pipelinePending(status) || !!client.busy || client.cameraTransition;
  const configure = (patch: Partial<CameraProfile>) => {
    void pipeline.change("configure", { ...status.profile, ...patch }).catch(() => undefined);
  };
  const stages = Object.entries(status.effective_stages).filter(([, enabled]) => enabled).map(([name]) => name);
  return <div data-testid="camera-pipeline">
    <div className={styles.flex}>
      <Field label={t("pipeline.analysis")}><select className={styles.input} value={status.profile.analysis_location} disabled={disabled}
        onChange={event => configure({ analysis_location: event.target.value as CameraProfile["analysis_location"] })}>
        {["local", "venom"].map(value => <option key={value} value={value} disabled={!status.capabilities.analysis_locations.includes(value)}>{t("pipeline." + value)}</option>)}
      </select></Field>
      {status.profile.analysis_location === "local" ? <Field label={t("pipeline.localEngine")}><select className={styles.input} disabled={disabled} value={status.profile.local_engine}
        onChange={event => configure({ local_engine: event.target.value as CameraProfile["local_engine"] })}>
        {["canny", "ssd", "tflite"].map(value => <option key={value} value={value} disabled={!status.capabilities.local_engines.includes(value)}>{value.toUpperCase()}</option>)}
      </select></Field> : <Field label={t("pipeline.transport")}><select className={styles.input} disabled={disabled} value={status.profile.remote_transport}
        onChange={event => configure({ remote_transport: event.target.value as CameraProfile["remote_transport"] })}>
        {["snapshots", "mjpeg"].map(value => <option key={value} value={value} disabled={!status.capabilities.remote_transports.includes(value)}>{t("pipeline." + value)}</option>)}
      </select></Field>}
      <Button disabled={!client.canMutate || pipeline.busy || status.state === "stopping"} onClick={() => {
        const stopping = ["active", "starting", "waiting_frame"].includes(status.state);
        void pipeline.change(stopping ? "stop" : "start").then(() =>
          client.setCameraStream(stopping ? null : status.profile.analysis_location === "local" ? "proc" : "raw")).catch(() => undefined);
      }}>
        {t(["active", "starting", "waiting_frame"].includes(status.state) ? "pipeline.stop" : "pipeline.start")}
      </Button>
      <Pill tone={status.state === "active" ? "good" : status.state === "failed" ? "danger" : "warning"}>{t("pipeline.state." + status.state)}</Pill>
    </div>
    <p className={styles.small}>{t("pipeline.piAuthority")} · r{status.revision} · {t("pipeline.stages")}: {stages.join(", ") || "—"}</p>
    {status.profile.analysis_location === "local" && <p className={styles.hint}>{t("pipeline.localHint")}</p>}
    {pipeline.error && <p className={styles.hint} role="alert">{t("pipeline.failure")} · <code>{pipeline.error}</code></p>}
  </div>;
}

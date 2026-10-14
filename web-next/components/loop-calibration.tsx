import { useState } from "react";
import { calibrationMatches, calibrationScope, freshAnalysisFrame } from "../lib/loop-controls";
import type { JsonRecord } from "../lib/model";
import type { RiderClient } from "../lib/use-rider";
import type { Translator } from "../lib/i18n";
import { Button, Pill } from "./primitives";
import styles from "../rider.module.css";

export function LoopCalibration({client,t,policy,detector,dirty,busy,driving,onAccept,onSettings,onManualSettings}:{
  client:RiderClient;t:Translator;policy:JsonRecord|null;detector:JsonRecord;dirty:boolean;busy:boolean;driving:boolean;
  onAccept:()=>Promise<void>;onSettings:()=>void;onManualSettings:()=>void;
}) {
  const scope=calibrationScope(policy,detector);
  const [proof,setProof]=useState({scope:"",zone:false,braking:false,frame:""});
  const zone=proof.scope===scope&&proof.zone;
  const braking=proof.scope===scope&&proof.braking;
  const fresh=freshAnalysisFrame(client.camera,client.cameraStream);
  const accepted=calibrationMatches(policy,detector);
  const unavailable=!client.canMutate||!policy||dirty||busy||driving||detector.loaded!==true;
  const setZone=(checked:boolean)=>setProof({scope,zone:checked,braking:false,frame:checked?client.camera?.id??"":""});
  return <section className={styles.loopCalibration} aria-label={t("loopCalibrationTitle")} data-testid="loop-calibration">
    <div className={styles.flex}><h3>{t("loopCalibrationTitle")}</h3><Pill tone={accepted?"good":"warning"}>{t(accepted?"loopCalibrationAccepted":"loopCalibrationMissing")}</Pill></div>
    <p className={styles.hint}>{t("loopCalibrationMeaning")}</p>
    <ol className={styles.calibrationSteps}>
      <li><strong>{t("loopCalibrationStepPreview")}</strong><p>{t("loopCalibrationPreviewHelp")}</p></li>
      <li><strong>{t("loopCalibrationStepZone")}</strong><p>{t("loopCalibrationZoneHelp")}</p><Button onClick={onSettings}>{t("loopCalibrationEditZone")}</Button></li>
      <li><strong>{t("loopCalibrationStepBrake")}</strong><p>{t("loopCalibrationBrakeHelp")}</p><Button onClick={onManualSettings}>{t("loopCalibrationManualSettings")}</Button></li>
    </ol>
    <label className={styles.check}><input type="checkbox" data-testid="calibration-zone-check" checked={zone} disabled={unavailable||!fresh} onChange={e=>setZone(e.target.checked)}/>{t("loopCalibrationZoneCheck")}</label>
    {!fresh&&!zone&&<p className={styles.hint} data-testid="calibration-preview-required">{t("loopCalibrationNeedPreview")}</p>}
    <label className={styles.check}><input type="checkbox" data-testid="calibration-brake-check" checked={braking} disabled={unavailable||!zone} onChange={e=>setProof({...proof,braking:e.target.checked})}/>{t("loopCalibrationBrakeCheck")}</label>
    <Button tone="primary" data-testid="calibration-accept" disabled={unavailable||!zone||!braking||!proof.frame} onClick={()=>void onAccept()}>{t("loopCalibrationAccept")}</Button>
    <p className={styles.hint}>{t("loopCalibrationAcceptHelp")}</p>
    {accepted&&<div className={styles.meta}>{t("loopCalibration")}: {String(policy?.calibration_id)}</div>}
  </section>;
}

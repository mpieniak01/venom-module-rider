import { useOperatorTuning } from "../lib/operator-tuning";
import { useOperatorApply } from "../lib/use-operator-apply";
import type {StudyRecording} from "../lib/use-study-recording";
import { MotionStudy } from "./motion-study";
import { LoopTimingPanel } from "./loop-timing";
import { useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { calibrationDraft, calibrationValues, directionDescriptor, validCalibration, type CalibrationDraft } from "../lib/calibration";
import { descriptorRange } from "../lib/field-range";
import { DIRECTIONS, finite, record, rows, settingsOf, str, type JsonRecord, type NumericDescriptor } from "../lib/model";
import type { Translator } from "../lib/i18n";
import type { RiderClient } from "../lib/use-rider";
import { ManualPad, Posture } from "./control";
import { Button, Empty, Field, Input, NumberField, Panel, Pill, Select } from "./primitives";
import styles from "../rider.module.css";

export function Calibration({ client, t, language, recording }: { client: RiderClient; t: Translator; language: string; recording:StudyRecording }) {
  const settings = settingsOf(client.status);
  const workspace=useOperatorTuning();
  const draft=workspace.state.manual;
  const setDraft=(manual:CalibrationDraft|null)=>workspace.setState(previous=>({...previous,manual}));
  const operatorApply=useOperatorApply(workspace,client);
  const [selectedProfile, setSelectedProfile] = useState<string | null>(null);
  const [profileName, setProfileName] = useState<string | null>(null);
  const [deletePending, setDeletePending] = useState(false);
  const effective = draft ?? calibrationDraft(settings);
  const conflict = draft !== null && draft.revision !== settings.revision;
  const valid = validCalibration(effective, settings);
  const canEdit = client.canMutate && !operatorApply.busy;
  const schema = record(settings.parameter_schema);
  const profiles = record(settings.profiles);
  const selected = selectedProfile ?? str(settings.active_profile);
  const name = profileName ?? str(record(profiles[selected]).name);
  const setDirection = (direction: string, field: string, value: number) => setDraft({ ...effective, directions: { ...effective.directions, [direction]: { ...effective.directions[direction], [field]: value } } });
  const setValue = (field: string, value: number) => setDraft({ ...effective, values: { ...effective.values, [field]: value } });
  const save = async (profile?: JsonRecord) => {
    if (!valid || conflict) return;
    if(!profile){try{await operatorApply.apply();}catch(error){client.setNotice({key:"requestFailed",code:String(error),tone:"error"});}return;}
    const result = await client.mutate("/settings", { revision: effective.revision, values: calibrationValues(effective, settings), directions: effective.directions, ...(profile ? { profile } : {}) });
    if (result) { setDraft(null); setSelectedProfile(null); setProfileName(null); }
  };
  const profileAction = async (action: string) => {
    const result = await client.mutate("/settings", { revision: settings.revision, values: {}, profile: { action, id: selected, ...(action === "rename" ? { name } : {}) } });
    if (result) { setDraft(null); setSelectedProfile(null); setProfileName(null); setDeletePending(false); }
  };
  if (!settings.parameter_schema || !settings.directions) return <div className={styles.stack}><Panel title={t("calibration")}><Empty><SlidersHorizontal size={28} />{t("unavailable")}</Empty></Panel><MotionStudy key={client.status?.connection.session} recording={recording} t={t} language={language}/></div>;
  return <div className={styles.stack}>
    <div className={styles.calibrationTop}>
      <div className={styles.calibrationOperator} data-testid="calibration-operator"><Panel title={t("testDirection")} hint={t("testHint")}>{draft&&<p className={styles.hint}>{t("study.savedSettings")}</p>}<ManualPad client={client} t={t} language={language} compact /></Panel><Posture client={client} t={t} language={language}/></div>
      <div className={styles.calibrationForms} data-testid="calibration-forms">    <Panel title={t("directions")} hint={t("calibrationHint")} extra={<Pill>{t("revision")} {String(settings.revision)}</Pill>}>
      <p className={styles.hint}>{t("commandUnitsHint")}</p><p className={styles.hint}>{t("manualRangeHelp")}</p>
      {conflict && <div className={`${styles.banner} ${styles.demo}`}>{t("conflict")}</div>}
      <div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>{t("directions")}</th>{["speed", "duration", "minimum", "maximum", "step"].map(field => <th key={field}>{t(field)}<div className={styles.small}>{descriptorRange(directionDescriptor(settings, field))}</div></th>)}</tr></thead><tbody>{DIRECTIONS.map(direction => <tr key={direction}><td><strong>{t(direction)}</strong></td>{["speed", "duration", "minimum", "maximum", "step"].map(field => { const descriptor = directionDescriptor(settings, field, effective.directions[direction]); const value = Number(effective.directions[direction]?.[field]); const factor = descriptor.display_factor ?? 1; return <td key={field}><span className={styles.small}>{descriptorRange(descriptor)}</span><Input className={styles.numericInput} aria-label={`${t(direction)} · ${t(field)}`} type="number" value={finite(value) ? Number((value * factor).toFixed(6)) : ""} min={(descriptor.minimum ?? 0) * factor} max={descriptor.maximum === undefined ? undefined : descriptor.maximum * factor} step={field === "step" ? 10 ** -(descriptor.display_precision ?? 3) : 10 ** -(descriptor.display_precision ?? 1)} disabled={!canEdit} onChange={event => setDirection(direction, field, event.target.value === "" ? Number.NaN : Number(event.target.value) / factor)} /></td>; })}</tr>)}</tbody></table></div>
    </Panel><Panel title={t("profiles")} extra={settings.profile_modified === true ? <Pill tone="warning">{t("modified")}</Pill> : undefined}>
      <div className={styles.fieldRow}><Field label={t("profiles")}><Select value={selected} disabled={!canEdit} onChange={event => { setSelectedProfile(event.target.value); setProfileName(null); setDeletePending(false); }}><option value="">{t("custom")}</option>{Object.entries(profiles).map(([id, row]) => <option key={id} value={id}>{str(record(row).name)}</option>)}</Select></Field><Button disabled={!canEdit || !selected || !!draft} onClick={() => void profileAction("apply")}>{t("apply")}</Button></div>
      <div className={styles.rule} />
      <Field label={t("profileName")}><Input maxLength={60} value={name} onChange={event => setProfileName(event.target.value)} disabled={!canEdit} /></Field>
      <div className={styles.flex} style={{ marginTop: 14 }}><Button disabled={!canEdit || !name.trim() || !valid || conflict} onClick={() => void save({ action: "create", name })}>{t("createProfile")}</Button><Button disabled={!canEdit || !selected || !valid || conflict} onClick={() => void save({ action: "save", id: selected, name })}>{t("saveProfile")}</Button><Button disabled={!canEdit || !selected || !name.trim() || !!draft} onClick={() => void profileAction("rename")}>{t("rename")}</Button><Button tone="danger" disabled={!canEdit || !selected || !!draft} onClick={() => setDeletePending(true)}>{t("delete")}</Button></div>
      {deletePending && <div className={`${styles.banner} ${styles.demo}`} style={{ marginTop: 14 }}><span className={styles.noticeText}>{t("deleteConfirm")}: {str(record(profiles[selected]).name)}</span><Button tone="danger" onClick={() => void profileAction("delete")}>{t("delete")}</Button><Button onClick={() => setDeletePending(false)}>{t("cancel")}</Button></div>}
    </Panel></div>
    </div>
    <div className={styles.calibrationBottom}>
      <Panel title={t("ramp")}>      <div className={styles.grid3} style={{ marginTop: 16 }}>{Object.entries(record(schema.value_fields)).map(([field, descriptor]) => <NumberField compact key={field} label={t(field)} value={Number(effective.values[field])} descriptor={record(descriptor) as NumericDescriptor} disabled={!canEdit} onChange={value => setValue(field, value)} />)}<Field label={t("ramp_mode")}><Select value={String(effective.values.ramp_mode)} disabled={!canEdit} onChange={event => setValue("ramp_mode", Number(event.target.value))}>{rows(schema.ramp_modes).map(mode => <option key={String(mode.value)} value={String(mode.value)}>{t(str(mode.id))}</option>)}</Select></Field></div>
      <div className={`${styles.flex} ${styles.space}`} style={{ marginTop: 20 }}><div>{draft && <Pill tone={valid && !conflict ? "warning" : "danger"}>{t(valid && !conflict ? "unsaved" : "invalidDraft")}</Pill>}</div><div className={styles.flex}><Button disabled={!draft} onClick={() => setDraft(null)}>{t("resetDraft")}</Button><Button tone="primary" data-testid="calibration-save" disabled={!canEdit || !draft || !valid || conflict} onClick={() => void save()}>{t("save")}</Button></div></div>
</Panel>
      <MotionStudy key={client.status?.connection.session} recording={recording} t={t} language={language}/>
    </div>
    <LoopTimingPanel key={client.status?.connection.session} client={client} t={t} language={language}/>
  </div>;
}
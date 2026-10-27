import {record,safetyOf,type RiderStatus} from "../lib/model";
import type {Translator} from "../lib/i18n";
import styles from "../rider.module.css";
export function SimulationPreview({status,t}:{status:RiderStatus|null;t:Translator}){
 const safety=safetyOf(status),pose=record(safety.simulation),output=record(safety.output);
 const x=Number(pose.x??0),y=Number(pose.y??0),heading=Number(pose.heading??0);
 const moving=Number(output.lx)!==0||Number(output.az)!==0;
 const px=Math.max(30,Math.min(450,240+x*100)),py=Math.max(30,Math.min(290,160-y*100));
 return <section className={styles.panel} data-testid="simulation-preview">
 <h2>{t("simulation.title")}</h2>
 <svg viewBox="0 0 480 320" role="img" aria-label={t("simulation.title")} style={{width:"100%",minHeight:220,maxHeight:420,background:"var(--r-panel)",borderRadius:12}}>
 <defs><pattern id="demo-grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M 40 0 L 0 0 0 40" fill="none" stroke="currentColor" opacity=".15"/></pattern></defs>
 <rect width="480" height="320" fill="url(#demo-grid)"/>
 <path d={"M 240 160 L "+px+" "+py} stroke="#05dfaa" strokeDasharray="4 4"/>
 <g data-testid="simulation-robot" data-x={x} data-y={y} transform={"translate("+px+" "+py+") rotate("+(90-heading*180/Math.PI)+")"}>
 <rect x="-17" y="-22" width="34" height="44" rx="10" fill="#092a36" stroke="#05dfaa" strokeWidth="3"/>
 <rect x="-25" y="-15" width="8" height="30" rx="3" fill="#788fa7"/><rect x="17" y="-15" width="8" height="30" rx="3" fill="#788fa7"/>
 <path d="M 0 -30 L -7 -18 L 7 -18 Z" fill="#05dfaa"/>
 <circle cx="-6" cy="-6" r="3" fill="#05dfaa"/><circle cx="6" cy="-6" r="3" fill="#05dfaa"/>
 </g></svg>
 <p role="status" data-testid="simulation-status">{t(moving?"simulation.moving":"simulation.idle")} · {t("simulation.relative")}: {x.toFixed(2)}, {y.toFixed(2)}</p>
 </section>;
}

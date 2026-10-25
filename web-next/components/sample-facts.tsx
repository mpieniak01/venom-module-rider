import type {ReactNode} from "react";
import styles from "../rider.module.css";
export function SampleFacts({items}:{items:{label:string;value:ReactNode}[]}){
  return <dl className={styles.sampleFacts}>{items.map(item=><div key={item.label} className={styles.metric}><dt className={styles.metricLabel}>{item.label}</dt><dd className={styles.sampleFactValue}>{item.value}</dd></div>)}</dl>;
}

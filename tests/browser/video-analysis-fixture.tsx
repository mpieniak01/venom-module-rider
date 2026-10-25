import React from "react";
import {createRoot} from "react-dom/client";
import {SamplesBrowser} from "../../web-next/components/samples-browser";
import {translate} from "../../web-next/lib/i18n";
import styles from "../../web-next/rider.module.css";
const localFetch=window.fetch.bind(window);
window.fetch=(input,options)=>{
 const value=typeof input==="string"?input:input instanceof URL?input.href:input.url;
 const url=new URL(value,location.href);
 if(!url.pathname.startsWith("/api/v1/rider/samples/")&&!url.pathname.startsWith("/api/v1/rider/analysis/")&&url.pathname!=="/api/v1/rider/navigation/config")throw new Error("outside_video_analysis_fixture");
 return localFetch(url.pathname+url.search,options);
};
createRoot(document.getElementById("root")!).render(<main className={styles.page} style={{padding:16,maxWidth:1100,margin:"auto"}}>
 <h1>Video analysis fixture — no model, robot or hardware</h1>
 <SamplesBrowser standalone t={(key,values)=>translate("pl",key,values)} language="pl"/>
</main>);

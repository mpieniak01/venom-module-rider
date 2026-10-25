import React,{useState} from "react";
import {createRoot} from "react-dom/client";
import {InputRegionControls} from "../../web-next/components/input-region";
import {GeometryOverlay} from "../../web-next/components/geometry-overlay";
import {inputBounds,inputCovers,region,RECOMMENDED_INPUT} from "../../web-next/lib/input-region";
import {translate} from "../../web-next/lib/i18n";
function Fixture(){const [value,setValue]=useState(region(RECOMMENDED_INPUT));const bounds=inputBounds(value);const t=(key:string)=>translate("pl",key);
return <main><InputRegionControls value={value} onChange={setValue} t={t}/><button data-testid="apply-crop" disabled={!inputCovers(value,[.35,.8,.65,.95])}>Zastosuj</button><output data-testid="crop-value">{JSON.stringify(value)}</output><div style={{position:"relative",width:640,height:480,background:"#142333"}}><GeometryOverlay value={{version:1,width:320,height:240,input_bounds:bounds.map((v,i)=>Math.round(v*(i%2?240:320))),score_bounds:[112,192,208,228]}} size={[320,240]}/></div></main>;}
createRoot(document.getElementById("root")!).render(<Fixture/>);

import React,{useState} from "react";
import {createRoot} from "react-dom/client";
import {CollisionSettings} from "../../web-next/components/collision-settings";
import {collisionDraft,collisionNavigationValid,guidanceReady} from "../../web-next/lib/collision-geometry";
import {GeometryOverlay} from "../../web-next/components/geometry-overlay";
import {translate} from "../../web-next/lib/i18n";
import type {JsonRecord} from "../../web-next/lib/model";
function Fixture(){
 const [geometry,setGeometry]=useState<JsonRecord>({mode:"image"}),[supported,setSupported]=useState(true);
 const corridor=[.35,.8,.65,.95],t=(key:string)=>translate("pl",key);
 const policy={corridor,avoidance:{enabled:true,protocol_version:3,geometry}};
 const valid=collisionNavigationValid({collision_geometry_supported:supported,axis_guidance_supported:supported},policy);
 return <main><CollisionSettings geometry={geometry} corridor={corridor} supported={supported} guidanceSupported={supported} disabled={false} t={t} onChange={setGeometry}/>
 <button data-testid="capability" onClick={()=>setSupported(false)}>Starszy Pi</button>
 <button data-testid="load-legacy" onClick={()=>setGeometry({mode:"image",collision:collisionDraft(corridor)})}>Wczytaj starszą konfigurację</button>
 <button data-testid="save-collision" disabled={!valid}>Zapisz</button>
 <button data-testid="start-guidance" disabled={!valid||!guidanceReady(policy)}>Sprawdź gotowość</button>
 <output data-testid="geometry-draft">{JSON.stringify(geometry)}</output>
 <div style={{position:"relative",width:640,height:480}}><GeometryOverlay value={{version:1,width:320,height:240,input_bounds:[0,0,320,240],reference_bounds:[112,156,208,228],paths:{left:[[16,192],[16,199],[112,227],[207,227],[111,199],[111,192],[16,192]],center:[[112,192],[207,192],[207,227],[112,227],[112,192]]},near:[[112,215],[207,215],[207,227],[112,227],[112,215]],guard_clear:true}} size={[320,240]}/></div></main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);

import React from "react";
import {createRoot} from "react-dom/client";
import RiderPage from "../../web-next/page";
import {Host,useLanguage} from "./workspace-host";
function App(){
 const {setLanguage}=useLanguage();
 return <><main><RiderPage/></main><select aria-label="Fixture language" style={{position:"fixed",right:0,bottom:0,zIndex:2200}} onChange={e=>setLanguage(e.target.value as "pl"|"en"|"de")}><option value="pl">PL</option><option value="en">EN</option><option value="de">DE</option></select></>;
}
createRoot(document.getElementById("root")!).render(<Host><App/></Host>);

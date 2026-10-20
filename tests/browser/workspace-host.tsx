import {createContext,useContext,useState,type ReactNode,type ComponentProps} from "react";
import type {RiderLanguage} from "../../web-next/lib/i18n";
const Context=createContext<{language:RiderLanguage;setLanguage:(value:RiderLanguage)=>void}>({language:"pl",setLanguage:()=>{}});
export function Host({children}:{children:ReactNode}){
 const [language,setLanguage]=useState<RiderLanguage>("pl");
 return <Context.Provider value={{language,setLanguage}}>{children}</Context.Provider>;
}
export const useLanguage=()=>useContext(Context);
export const useTheme=()=>({theme:"dark"});

export default function FixtureLink(props:ComponentProps<"a">){return <a {...props}/>;}

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import type { CalibrationDraft } from "./calibration";
import type { JsonRecord } from "./model";

export type TuningState = { base: JsonRecord | null; draft: JsonRecord | null; manual: CalibrationDraft | null; view: "manual" | "automatic" | "journey"; pane: "motion" | "model"; direction:"forward"|"backward"|"left"|"right"; trialProfile:"A"|"B"|"C"|null; trialRunId:string; busy: boolean; modelPending: boolean; modelReady:boolean; modelChoice:{id:string;provider:string;session:string}|null };
const initial = (): TuningState => ({base:null,draft:null,manual:null,view:"manual",pane:"motion",direction:"forward",trialProfile:null,trialRunId:"",busy:false,modelPending:false,modelReady:true,modelChoice:null});
type Workspace = { state:TuningState; setState:Dispatch<SetStateAction<TuningState>>; claimed:{current:boolean}; alive:{current:boolean}; modelApply:{current:((resume?:boolean)=>Promise<{policy:JsonRecord;token:{session:string;epoch:number;generation:string}}|null>)|null} };
const Context = createContext<Workspace|null>(null);
function useWorkspace(): Workspace {
 const [state,setState]=useState(initial),claimed=useRef(false),alive=useRef(true),modelApply=useRef<((resume?:boolean)=>Promise<{policy:JsonRecord;token:{session:string;epoch:number;generation:string}}|null>)|null>(null);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 return useMemo(()=>({state,setState,claimed,alive,modelApply}),[state]);
}
export function OperatorTuningProvider({children}:{children:ReactNode}) {
 const value=useWorkspace();return <Context.Provider value={value}>{children}</Context.Provider>;
}
/** Scope lives above page tabs; no browser storage, commands or automatic replay. */
export function useOperatorTuning() {
 const shared=useContext(Context),fallback=useWorkspace();return shared??fallback;
}
export function useTuningField<K extends keyof TuningState>(key:K):[TuningState[K],Dispatch<SetStateAction<TuningState[K]>>] {
 const {state,setState}=useOperatorTuning();
 const set=useCallback((value:SetStateAction<TuningState[K]>)=>setState(previous=>({...previous,[key]:typeof value==="function"?(value as (old:TuningState[K])=>TuningState[K])(previous[key]):value})),[key,setState]);
 return [state[key],set];
}

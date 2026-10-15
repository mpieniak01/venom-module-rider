import type { JsonRecord } from "./model";

type Mutate = (path: string, body: JsonRecord, method: string, throwOnError: boolean) => Promise<unknown>;

/** Retire producers before releasing their camera; Pi preserves other owners. */
export async function stopLoopResources(goals: JsonRecord, mutate: Mutate): Promise<void> {
  for (const [key, name] of [["drive", "s14_venom_navigation"], ["observe", "s15_venom_perception"]] as const) {
    if (goals[key] === true) await mutate("/goal/" + name, { enabled: false }, "POST", true);
  }
  // Pi confirms camera release and stops the unused frame distributor.
  await mutate("/resource/camera", { action: "stop" }, "POST", true);
}

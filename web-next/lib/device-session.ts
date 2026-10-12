import type { DeviceConfig, RiderStatus } from "./model";

// A restarted host may have a different allowlist as well as a new session.
export async function configForConnection(
  current: DeviceConfig,
  connection: RiderStatus["connection"],
  readConfig: () => Promise<DeviceConfig>,
): Promise<DeviceConfig> {
  if (current.session === connection.session && current.device_id === connection.device_id) return current;
  const next = await readConfig();
  if (next.session !== connection.session || next.device_id !== connection.device_id) {
    throw Object.assign(new Error("stale_device_session"), { data: { error: "stale_device_session" } });
  }
  return next;
}

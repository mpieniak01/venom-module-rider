import type { NumericDescriptor } from "./model";
/** Display the same device bounds and units used by the numeric input. */
export function descriptorRange(descriptor: NumericDescriptor): string {
  const factor = descriptor.display_factor ?? 1;
  if (descriptor.minimum === undefined || descriptor.maximum === undefined) return descriptor.display_unit ?? "";
  const number = (value: number) => String(Number((value * factor).toFixed(6)));
  return `${descriptor.exclusive_minimum ? ">" : ""}${number(descriptor.minimum)}–${number(descriptor.maximum)}${descriptor.display_unit ? ` ${descriptor.display_unit}` : ""}`;
}

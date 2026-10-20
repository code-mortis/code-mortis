export const findingOrderings = { created_at: "Created", updated_at: "Updated", severity: "Severity", path: "File" };
export const snapshotOrderings = { position: "Report order", severity: "Severity", path: "File" };
export const assessmentOrderings = { position: "Assessment order", created_at: "Created", finished_at: "Finished", severity: "Severity", path: "File" };
export const runFindingOrderings = { severity: "Severity", path: "File" };

export function normalizeOrdering(value: string | null | undefined, options: Record<string, string>, fallback: string) {
  return value && Object.hasOwn(options, value.replace(/^-/, "")) ? value : fallback;
}

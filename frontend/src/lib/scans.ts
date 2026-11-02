/** A skipped scan, as the `scan.skipped` event reports it. */
export interface SkippedScan {
  creator_name: string;
  message: string;
}

/** One toast for a burst of skipped scans: who, if it's one, and each distinct reason. */
export function skippedScansMessage(skipped: SkippedScan[]): string {
  const reasons = [...new Set(skipped.map((s) => s.message))].join("; ");
  if (skipped.length === 1) return `Scan of ${skipped[0].creator_name} skipped: ${reasons}`;
  return `${skipped.length} scans skipped: ${reasons}`;
}

import { cx } from "../../lib/format";

export function ProgressBar({ value, tone = "accent", className, indeterminate }: { value: number; tone?: "accent" | "ok" | "info" | "warn"; className?: string; indeterminate?: boolean }) {
  const color = { accent: "bg-accent", ok: "bg-ok", info: "bg-info", warn: "bg-warn" }[tone];
  return (
    <div className={cx("h-1.5 w-full overflow-hidden rounded-full bg-bg-3", className)}>
      <div
        className={cx("h-full rounded-full transition-[width] duration-300", color, indeterminate && "animate-pulse")}
        style={{ width: `${indeterminate ? 100 : Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  );
}

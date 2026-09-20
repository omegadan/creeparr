import type { ReactNode } from "react";
import { TONE_CLASS, type StatusMeta } from "../../lib/status";
import { cx } from "../../lib/format";

export function Badge({ tone = "muted", children, className, title }: { tone?: StatusMeta["tone"]; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium", TONE_CLASS[tone], className)}>
      {children}
    </span>
  );
}

export function StatusBadge({ meta, title }: { meta: StatusMeta; title?: string }) {
  return <Badge tone={meta.tone} title={title}>{meta.label}</Badge>;
}

import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cx } from "../../lib/format";

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cx("h-5 w-5 animate-spin text-fg-muted", className)} />;
}

export function EmptyState({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="card flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      {icon && <div className="text-fg-dim">{icon}</div>}
      <div className="text-base font-semibold">{title}</div>
      {hint && <div className="max-w-md text-sm text-fg-muted">{hint}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function Field({ label, hint, children, className }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <div className={className}>
      <label className="label">{label}</label>
      {children}
      {hint && <p className="mt-1 text-xs text-fg-dim">{hint}</p>}
    </div>
  );
}

export function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="card">
      <div className="border-b border-line px-5 py-3">
        <h3 className="text-sm font-semibold">{title}</h3>
        {description && <p className="text-xs text-fg-muted">{description}</p>}
      </div>
      <div className="grid gap-4 px-5 py-4">{children}</div>
    </section>
  );
}

export function Pager({ page, pageSize, total, onChange }: { page: number; pageSize: number; total: number; onChange: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <div className="flex items-center justify-between px-1 py-3 text-xs text-fg-muted">
      <span>
        Page {page} of {pages} · {total} items
      </span>
      <div className="flex gap-1">
        <button className="rounded border border-line px-2 py-1 hover:bg-bg-3 disabled:opacity-40" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          Prev
        </button>
        <button className="rounded border border-line px-2 py-1 hover:bg-bg-3 disabled:opacity-40" disabled={page >= pages} onClick={() => onChange(page + 1)}>
          Next
        </button>
      </div>
    </div>
  );
}

export function Avatar({ src, name, size = 40 }: { src: string | null; name: string; size?: number }) {
  const initials = name
    .split(/\s+/)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() ?? "")
    .join("");
  return src ? (
    <img src={src} alt="" width={size} height={size} className="shrink-0 rounded-full object-cover" style={{ width: size, height: size }} referrerPolicy="no-referrer" />
  ) : (
    <div className="flex shrink-0 items-center justify-center rounded-full bg-bg-3 text-xs font-semibold text-fg-muted" style={{ width: size, height: size }}>
      {initials || "?"}
    </div>
  );
}

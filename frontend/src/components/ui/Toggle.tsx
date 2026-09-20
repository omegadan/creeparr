import { cx } from "../../lib/format";

interface Props {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
  hint?: string;
  disabled?: boolean;
  size?: "sm" | "md";
}

export function Toggle({ checked, onChange, label, hint, disabled, size = "md" }: Props) {
  const w = size === "sm" ? "h-4 w-7" : "h-5 w-9";
  const knob = size === "sm" ? "h-3 w-3" : "h-4 w-4";
  const shift = size === "sm" ? "translate-x-3" : "translate-x-4";
  return (
    <label className={cx("flex items-start gap-3", disabled ? "opacity-50" : "cursor-pointer")}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cx("relative mt-0.5 shrink-0 rounded-full border transition-colors", w, checked ? "border-accent bg-accent" : "border-line bg-bg-3")}
      >
        <span className={cx("absolute left-0.5 top-0.5 rounded-full bg-white transition-transform", knob, checked && shift)} />
      </button>
      {(label || hint) && (
        <span className="flex flex-col">
          {label && <span className="text-sm text-fg">{label}</span>}
          {hint && <span className="text-xs text-fg-dim">{hint}</span>}
        </span>
      )}
    </label>
  );
}

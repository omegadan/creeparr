import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cx } from "../../lib/format";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
}

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent-hover border-transparent font-semibold shadow-sm",
  secondary: "bg-bg-2 text-fg hover:bg-bg-3 border-line",
  ghost: "bg-transparent text-fg-muted hover:text-fg hover:bg-bg-2 border-transparent",
  danger: "bg-danger/15 text-danger hover:bg-danger/25 border-danger/30",
};
const SIZES: Record<Size, string> = { sm: "h-7 px-2.5 text-xs gap-1.5", md: "h-9 px-3.5 text-sm gap-2" };

export function Button({ variant = "secondary", size = "md", loading, icon, className, children, disabled, ...rest }: Props) {
  return (
    <button
      className={cx(
        "inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-md border transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      {children}
    </button>
  );
}

export function IconButton({ title, className, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      title={title}
      aria-label={title}
      className={cx("inline-flex h-7 w-7 items-center justify-center rounded-md text-fg-muted hover:bg-bg-3 hover:text-fg disabled:opacity-40", className)}
      {...rest}
    >
      {children}
    </button>
  );
}

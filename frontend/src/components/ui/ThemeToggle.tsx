import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { getTheme, setTheme, type Theme } from "../../lib/theme";
import { cx } from "../../lib/format";

const OPTIONS: { value: Theme; icon: typeof Sun; label: string }[] = [
  { value: "light", icon: Sun, label: "Light" },
  { value: "dark", icon: Moon, label: "Dark" },
  { value: "auto", icon: Monitor, label: "Auto (follow system)" },
];

export function ThemeToggle() {
  const [theme, setLocal] = useState<Theme>(getTheme());
  useEffect(() => {
    const onChange = () => setLocal(getTheme());
    window.addEventListener("creeparr-themechange", onChange);
    return () => window.removeEventListener("creeparr-themechange", onChange);
  }, []);
  return (
    <div className="inline-flex rounded-lg border border-white/15 bg-white/5 p-0.5" role="group" aria-label="Theme">
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          title={o.label}
          aria-label={o.label}
          aria-pressed={theme === o.value}
          onClick={() => setTheme(o.value)}
          className={cx(
            "flex h-6 w-7 items-center justify-center rounded-md transition-colors",
            theme === o.value
              ? "bg-[var(--color-sidebar-bg2)] text-white"
              : "text-[var(--color-sidebar-muted)] hover:text-white",
          )}
        >
          <o.icon className="h-3.5 w-3.5" />
        </button>
      ))}
    </div>
  );
}

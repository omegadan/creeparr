export type Theme = "light" | "dark" | "auto";

const KEY = "creeparr-theme";
// The app used to store this under its old name; carry the preference over once.
const LEGACY_KEY = "patrearr-theme";
try {
  if (localStorage.getItem(KEY) === null) {
    const old = localStorage.getItem(LEGACY_KEY);
    if (old !== null) localStorage.setItem(KEY, old);
  }
} catch {
  /* storage unavailable */
}

export function getTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark" || v === "auto") return v;
  } catch {
    /* ignore */
  }
  return "auto";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
}

export function setTheme(theme: Theme): void {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* ignore */
  }
  applyTheme(theme);
  window.dispatchEvent(new CustomEvent("creeparr-themechange"));
}

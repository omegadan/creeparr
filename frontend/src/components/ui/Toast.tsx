import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { cx } from "../../lib/format";

type Kind = "info" | "success" | "error";
interface Toast { id: number; kind: Kind; message: string }
interface Ctx { toast: (message: string, kind?: Kind) => void; error: (e: unknown, fallback?: string) => void }

const ToastCtx = createContext<Ctx>({ toast: () => {}, error: () => {} });
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const toast = useCallback((message: string, kind: Kind = "info") => {
    const id = Date.now() + Math.random();
    setItems((s) => [...s, { id, kind, message }]);
    window.setTimeout(() => setItems((s) => s.filter((t) => t.id !== id)), kind === "error" ? 8000 : 4000);
  }, []);
  const error = useCallback(
    (e: unknown, fallback = "Something went wrong") => {
      const msg = e instanceof Error ? e.message : fallback;
      const detail = (e as { detail?: string | null })?.detail;
      toast(detail ? `${msg}: ${detail}` : msg, "error");
    },
    [toast],
  );
  const value = useMemo(() => ({ toast, error }), [toast, error]);
  const icons = { info: <Info className="h-4 w-4 text-info" />, success: <CheckCircle2 className="h-4 w-4 text-ok" />, error: <AlertTriangle className="h-4 w-4 text-danger" /> };
  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div role="status" aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} className={cx("pointer-events-auto flex items-start gap-2 rounded-md border border-line bg-bg-2 px-3 py-2 text-sm shadow-lg")}>
            <span className="mt-0.5">{icons[t.kind]}</span>
            <span className="flex-1 break-words">{t.message}</span>
            <button aria-label="Dismiss" onClick={() => setItems((s) => s.filter((x) => x.id !== t.id))} className="text-fg-dim hover:text-fg"><X className="h-3.5 w-3.5" /></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

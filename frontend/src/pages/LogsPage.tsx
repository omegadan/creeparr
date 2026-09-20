import { useEffect, useRef, useState } from "react";
import { useLogs } from "../api/hooks/useSystem";
import { PageHeader } from "../components/layout/AppShell";
import { Toggle } from "../components/ui/Toggle";
import { cx } from "../lib/format";

const LEVEL_CLASS: Record<string, string> = { DEBUG: "text-fg-dim", INFO: "text-fg", WARNING: "text-warn", ERROR: "text-danger", CRITICAL: "text-danger" };

export function LogsPage() {
  const [level, setLevel] = useState("INFO");
  const [search, setSearch] = useState("");
  const [auto, setAuto] = useState(true);
  const logs = useLogs(level, search, auto);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (auto) end.current?.scrollIntoView({ block: "end" });
  }, [logs.data, auto]);
  return (
    <>
      <PageHeader
        title="Logs"
        actions={
          <>
            <input className="input w-56" placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} />
            <select className="input w-32" value={level} onChange={(e) => setLevel(e.target.value)}>
              {["DEBUG", "INFO", "WARNING", "ERROR"].map((l) => <option key={l}>{l}</option>)}
            </select>
            <Toggle checked={auto} onChange={setAuto} label="Auto-refresh" size="sm" />
          </>
        }
      />
      <div className="card h-[calc(100vh-10rem)] overflow-auto p-3 font-mono text-[12px] leading-5">
        {(logs.data?.lines ?? []).map((l, i) => (
          <div key={i} className="flex gap-3 whitespace-pre-wrap">
            <span className="shrink-0 text-fg-dim">{l.time.slice(11, 19)}</span>
            <span className={cx("w-16 shrink-0", LEVEL_CLASS[l.level] ?? "")}>{l.level}</span>
            <span className="shrink-0 text-fg-dim">{l.logger.replace("patrearr.", "")}</span>
            <span className={LEVEL_CLASS[l.level]}>{l.message}</span>
          </div>
        ))}
        {logs.data && logs.data.lines.length === 0 && <div className="text-fg-muted">No log lines match.</div>}
        <div ref={end} />
      </div>
    </>
  );
}

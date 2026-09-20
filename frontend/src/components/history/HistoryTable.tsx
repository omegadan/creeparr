import { Link } from "react-router";
import type { HistoryEvent } from "../../api/types";
import { formatDate } from "../../lib/format";
import { Badge } from "../ui/Badge";

const TONE: Record<HistoryEvent["level"], "info" | "warn" | "danger"> = { info: "info", warning: "warn", error: "danger" };

export function HistoryTable({ events }: { events: HistoryEvent[] }) {
  return (
    <div className="card overflow-x-auto">
      <table className="table">
        <thead>
          <tr>
            <th className="w-40">When</th>
            <th className="w-40">Event</th>
            <th className="w-40">Creator</th>
            <th>Message</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td className="whitespace-nowrap text-fg-muted">{formatDate(e.occurred_at, true)}</td>
              <td><Badge tone={TONE[e.level]}>{e.event_type.replace(/_/g, " ")}</Badge></td>
              <td>{e.creator_id ? <Link to={`/creators/${e.creator_id}`} className="text-fg-muted hover:text-fg">{e.creator_name}</Link> : <span className="text-fg-dim">–</span>}</td>
              <td className="max-w-2xl truncate" title={e.message}>{e.message}</td>
            </tr>
          ))}
          {events.length === 0 && <tr><td colSpan={4} className="py-8 text-center text-fg-muted">No history yet.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Job, Queue } from "./types";

type Handler = (data: Record<string, unknown>) => void;

/** Subscribe to the server-sent event stream and keep TanStack Query caches fresh. */
export function useServerEvents(): void {
  const qc = useQueryClient();
  const timers = useRef<Record<string, number>>({});

  useEffect(() => {
    const pending = timers.current; // one object for the effect's lifetime
    const debounced = (key: string, fn: () => void, ms = 400) => {
      window.clearTimeout(pending[key]);
      pending[key] = window.setTimeout(fn, ms);
    };
    const invalidate = (keys: unknown[][]) => () => keys.forEach((k) => qc.invalidateQueries({ queryKey: k }));

    const handlers: Record<string, Handler> = {
      "auth.status": () => debounced("status", invalidate([["system", "status"], ["providers"]])),
      "scan.started": () => debounced("creators", invalidate([["creators"], ["system", "status"]])),
      "scan.queued": () => debounced("creators", invalidate([["creators"], ["system", "status"]])),
      "scan.progress": () => debounced("posts", invalidate([["posts"]]), 800),
      "scan.finished": (d) => {
        debounced("creators", invalidate([["creators"], ["creator", d.creator_id], ["scans", d.creator_id], ["posts"], ["system", "status"]]));
      },
      "job.progress": (d) => {
        // Every cached queue page (keys are ["queue", page, failedPage]).
        qc.setQueriesData<Queue>({ queryKey: ["queue"] }, (old) => {
          if (!old) return old;
          const jobs = old.jobs.map((j) => (j.id === d.id ? { ...j, ...(d as Partial<Job>), status: "running" as const } : j));
          return { ...old, jobs };
        });
      },
      "job.started": () => debounced("queue", invalidate([["queue"]]), 200),
      "job.finished": (d) => {
        debounced("queue", invalidate([["queue"], ["system", "status"]]), 200);
        debounced("posts", invalidate([["posts"], ["post", d.post_id], ["creators"]]), 600);
      },
      "queue.changed": () => debounced("queue", invalidate([["queue"]]), 200),
      "queue.paused": () => debounced("queue", invalidate([["queue"], ["system", "status"]]), 100),
      "queue.resumed": () => debounced("queue", invalidate([["queue"], ["system", "status"]]), 100),
      "history.added": () => debounced("history", invalidate([["history"]]), 500),
      "creator.changed": (d) => debounced("creators", invalidate([["creators"], ["creator", d.id]]), 300),
      "post.changed": (d) => {
        debounced("posts", invalidate([["posts"]]), 600);
        qc.invalidateQueries({ queryKey: ["post", d.id] });
      },
      "settings.changed": () => debounced("settings", invalidate([["settings"], ["system", "status"], ["system", "tasks"]])),
    };

    // EventSource retries a dropped connection by itself, but gives up for good on a
    // non-200 answer (a reverse proxy's 502 while the container restarts). Then we
    // open a new one ourselves, backing off from 1 s to 30 s.
    let es: EventSource | null = null;
    let retryTimer = 0;
    let delay = 1000;
    let stopped = false;
    const connect = () => {
      es = new EventSource("/api/v1/events");
      for (const [name, fn] of Object.entries(handlers)) {
        es.addEventListener(name, (e: MessageEvent) => {
          try {
            fn(JSON.parse(e.data));
          } catch {
            /* ignore malformed */
          }
        });
      }
      es.addEventListener("hello", () => {
        delay = 1000;
        // (Re)connected: refresh everything that may have changed while we were away.
        qc.invalidateQueries();
      });
      es.onerror = () => {
        if (stopped || es?.readyState !== EventSource.CLOSED) return; // browser is retrying
        es.close();
        retryTimer = window.setTimeout(connect, delay);
        delay = Math.min(delay * 2, 30_000);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(retryTimer);
      es?.close();
      Object.values(pending).forEach((t) => window.clearTimeout(t));
    };
  }, [qc]);
}

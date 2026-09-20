import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { AuthStatus, Job, Queue } from "./types";

type Handler = (data: Record<string, unknown>) => void;

/** Subscribe to the server-sent event stream and keep TanStack Query caches fresh. */
export function useServerEvents(): void {
  const qc = useQueryClient();
  const timers = useRef<Record<string, number>>({});

  useEffect(() => {
    const debounced = (key: string, fn: () => void, ms = 400) => {
      window.clearTimeout(timers.current[key]);
      timers.current[key] = window.setTimeout(fn, ms);
    };
    const invalidate = (keys: unknown[][]) => () => keys.forEach((k) => qc.invalidateQueries({ queryKey: k }));

    const handlers: Record<string, Handler> = {
      "auth.status": (d) => {
        qc.setQueryData<AuthStatus>(["auth"], d as unknown as AuthStatus);
        debounced("status", invalidate([["system", "status"]]));
      },
      "scan.started": () => debounced("creators", invalidate([["creators"], ["system", "status"]])),
      "scan.queued": () => debounced("creators", invalidate([["creators"], ["system", "status"]])),
      "scan.progress": () => debounced("posts", invalidate([["posts"]]), 800),
      "scan.finished": (d) => {
        debounced("creators", invalidate([["creators"], ["creator", d.creator_id], ["scans", d.creator_id], ["posts"], ["system", "status"]]));
      },
      "job.progress": (d) => {
        qc.setQueryData<Queue>(["queue"], (old) => {
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

    const es = new EventSource("/api/v1/events");
    const listeners: Array<[string, (e: MessageEvent) => void]> = [];
    for (const [name, fn] of Object.entries(handlers)) {
      const l = (e: MessageEvent) => {
        try {
          fn(JSON.parse(e.data));
        } catch {
          /* ignore malformed */
        }
      };
      es.addEventListener(name, l);
      listeners.push([name, l]);
    }
    es.addEventListener("hello", (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data);
        if (d.auth) qc.setQueryData(["auth"], d.auth);
      } catch {
        /* ignore */
      }
      // Reconnected: refresh everything that may have changed while we were away.
      qc.invalidateQueries();
    });
    return () => {
      listeners.forEach(([n, l]) => es.removeEventListener(n, l));
      es.close();
      Object.values(timers.current).forEach((t) => window.clearTimeout(t));
    };
  }, [qc]);
}

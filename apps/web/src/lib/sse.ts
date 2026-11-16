import { useEffect, useRef } from "react";
import { API_BASE } from "./api";

export interface StreamEvent {
  id: number;
  event: string;
  data: Record<string, unknown>;
}

/**
 * Project event stream over fetch (EventSource cannot send the X-Project-Token header, and
 * the token must not go into URLs or logs). Reconnects with Last-Event-ID after a drop.
 */
export function useProjectEvents(
  token: string | null,
  onEvent: (e: StreamEvent) => void,
  enabled = true,
): void {
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!token || !enabled) return;
    const ctrl = new AbortController();
    let last = 0;
    let stopped = false;

    async function run(): Promise<void> {
      let delay = 1000;
      while (!stopped) {
        try {
          const res = await fetch(`${API_BASE}/projects/current/events`, {
            headers: {
              "X-Project-Token": token ?? "",
              Accept: "text/event-stream",
              ...(last ? { "Last-Event-ID": String(last) } : {}),
            },
            signal: ctrl.signal,
          });
          if (!res.ok || !res.body) throw new Error(`events: HTTP ${res.status}`);
          delay = 1000;
          const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
          let buf = "";
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += value;
            let idx: number;
            while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
              const block = buf.slice(0, idx);
              buf = buf.slice(idx).replace(/^\r?\n\r?\n/, "");
              let id = 0;
              let event = "message";
              const data: string[] = [];
              for (const line of block.split(/\r?\n/)) {
                if (line.startsWith("id:")) id = Number(line.slice(3).trim());
                else if (line.startsWith("event:")) event = line.slice(6).trim();
                else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
              }
              if (!data.length) continue; // comment / ping
              if (id) last = id;
              try {
                handler.current({ id, event, data: JSON.parse(data.join("\n")) as Record<string, unknown> });
              } catch {
                /* ignore a malformed event */
              }
            }
          }
        } catch (err) {
          if (ctrl.signal.aborted) return;
          void err;
        }
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(delay * 2, 15000);
      }
    }
    void run();
    return () => {
      stopped = true;
      ctrl.abort();
    };
  }, [token, enabled]);
}

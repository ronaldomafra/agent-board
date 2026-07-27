import { useCallback, useEffect, useRef, useState } from "react";
import {
  createBoardEventSource,
  getBoard,
  type BoardSnapshot,
} from "./api";

export type ConnectionState = "connecting" | "live" | "reconnecting" | "offline";

export function useBoard() {
  const [snapshot, setSnapshot] = useState<BoardSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const eventIdRef = useRef<string | number | null>(null);
  const refreshTimerRef = useRef<number | null>(null);
  const hasSnapshot = snapshot !== null;

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await getBoard(signal);
      setSnapshot(next);
      eventIdRef.current = next.last_event_id ?? eventIdRef.current;
      setLastUpdatedAt(new Date());
      setError(null);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : "Não foi possível carregar o quadro.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => {
      if (!controller.signal.aborted) void refresh(controller.signal);
    });
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    if (!hasSnapshot) return;
    let closed = false;
    let source: EventSource | null = null;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (closed) return;
      setConnection((current) => (current === "connecting" ? "connecting" : "reconnecting"));
      source = createBoardEventSource(eventIdRef.current);

      source.onopen = () => setConnection("live");
      source.onmessage = (event) => {
        eventIdRef.current = event.lastEventId || eventIdRef.current;
        if (refreshTimerRef.current !== null) window.clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = window.setTimeout(() => void refresh(), 120);
      };
      source.onerror = () => {
        source?.close();
        setConnection("offline");
        if (!closed) reconnectTimer = window.setTimeout(connect, 2500);
      };
    };

    connect();
    return () => {
      closed = true;
      source?.close();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (refreshTimerRef.current !== null) window.clearTimeout(refreshTimerRef.current);
    };
  }, [hasSnapshot, refresh]);

  return { snapshot, error, loading, connection, lastUpdatedAt, refresh };
}

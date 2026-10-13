/** One shared WebSocket to /ws with auto-reconnect; components subscribe to topics via useLive(). */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { getToken, wsUrl } from './api';

export interface LiveMessage<T = Record<string, unknown>> {
  topic: string;
  data: T;
  ts: string;
}

type Handler = (msg: LiveMessage) => void;

interface LiveCtx {
  connected: boolean;
  lastMessageAt: number | null;
  subscribe: (topics: string[] | '*', fn: Handler) => () => void;
}

const Ctx = createContext<LiveCtx>({ connected: false, lastMessageAt: null, subscribe: () => () => undefined });

/** Exponential backoff with jitter, capped: 1 s, 2 s, 4 s … 30 s. */
export function backoffMs(attempt: number): number {
  const base = Math.min(30000, 1000 * 2 ** Math.max(0, attempt));
  return Math.round(base * (0.8 + Math.random() * 0.4));
}

export function LiveProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [lastMessageAt, setLast] = useState<number | null>(null);
  const subs = useRef(new Map<number, { topics: Set<string> | '*'; fn: Handler }>());
  const nextId = useRef(1);

  useEffect(() => {
    if (!enabled) return;
    let ws: WebSocket | null = null;
    let attempt = 0;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let ping: ReturnType<typeof setInterval> | undefined;

    const connect = () => {
      if (stopped || !getToken()) return;
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        schedule();
        return;
      }
      ws.onopen = () => {
        attempt = 0;
        setConnected(true);
        ping = setInterval(() => {
          try {
            ws?.send('ping');
          } catch {
            /* reconnect handles it */
          }
        }, 25000);
      };
      ws.onmessage = (e) => {
        let msg: LiveMessage;
        try {
          msg = JSON.parse(e.data as string) as LiveMessage;
        } catch {
          return;
        }
        if (!msg || !msg.topic || msg.topic === 'pong') return;
        setLast(Date.now());
        subs.current.forEach((s) => {
          if (s.topics === '*' || s.topics.has(msg.topic)) {
            try {
              s.fn(msg);
            } catch (err) {
              console.warn('live handler failed', err);
            }
          }
        });
      };
      ws.onclose = () => {
        setConnected(false);
        if (ping) clearInterval(ping);
        schedule();
      };
      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          /* ignore */
        }
      };
    };
    const schedule = () => {
      if (stopped) return;
      retry = setTimeout(connect, backoffMs(attempt++));
    };
    connect();
    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      if (ping) clearInterval(ping);
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
      setConnected(false);
    };
  }, [enabled]);

  const subscribe = useCallback((topics: string[] | '*', fn: Handler) => {
    const id = nextId.current++;
    subs.current.set(id, { topics: topics === '*' ? '*' : new Set(topics), fn });
    return () => {
      subs.current.delete(id);
    };
  }, []);
  const value = useMemo<LiveCtx>(() => ({ connected, lastMessageAt, subscribe }), [connected, lastMessageAt, subscribe]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Subscribe to topics; the latest handler is always used without resubscribing. */
export function useLive(topics: string[] | '*', handler?: Handler): { connected: boolean } {
  const ctx = useContext(Ctx);
  const ref = useRef(handler);
  ref.current = handler;
  const key = topics === '*' ? '*' : topics.join(',');
  const { subscribe } = ctx;
  useEffect(() => {
    if (!ref.current) return;
    return subscribe(key === '*' ? '*' : key.split(','), (m) => ref.current?.(m));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, subscribe]);
  return { connected: ctx.connected };
}

/** Run `fn` when any of the topics arrives, debounced (to coalesce bursts), e.g. to refetch a view. */
export function useLiveRefresh(topics: string[], fn: () => void, debounceMs = 1200): { connected: boolean } {
  const t = useRef<ReturnType<typeof setTimeout>>();
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => () => clearTimeout(t.current), []);
  return useLive(topics, () => {
    clearTimeout(t.current);
    t.current = setTimeout(() => fnRef.current(), debounceMs);
  });
}

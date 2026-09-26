/** Shared site status: /api/dashboard/live, refreshed periodically and on relevant WebSocket topics. */
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { api, type AnprEvent, type LiveData } from './api';
import { useLive } from './useLive';
import { errMsg, useInterval } from './ui';

interface SiteCtx {
  live: LiveData | null;
  error: string | null;
  refresh: () => void;
  updatedAt: number | null;
}

const Ctx = createContext<SiteCtx>({ live: null, error: null, refresh: () => undefined, updatedAt: null });

const REFRESH_TOPICS = ['anpr.event', 'review.new', 'payment.updated', 'device.health', 'cash.updated', 'handover.pending',
  'handover.confirmed', 'dispute.new', 'session.closed', 'alert'];

export function SiteProvider({ children }: { children: ReactNode }) {
  const [live, setLive] = useState<LiveData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const inflight = useRef(false);
  const pending = useRef<ReturnType<typeof setTimeout>>();

  const refresh = useCallback(() => {
    if (inflight.current) return;
    inflight.current = true;
    api
      .live()
      .then((d) => {
        setLive(d);
        setError(null);
        setUpdatedAt(Date.now());
      })
      .catch((e) => setError(errMsg(e)))
      .finally(() => {
        inflight.current = false;
      });
  }, []);

  useEffect(() => {
    refresh();
    return () => clearTimeout(pending.current);
  }, [refresh]);
  useInterval(refresh, 20000);

  useLive(REFRESH_TOPICS, (msg) => {
    // show new ANPR events immediately, then reconcile with a debounced full refresh
    if (msg.topic === 'anpr.event') {
      const e = msg.data as Partial<AnprEvent> & { plate_image?: string | null };
      setLive((cur) => {
        if (!cur || !e.id || cur.latest_events.some((x) => x.id === e.id)) return cur;
        const defaults: AnprEvent = {
          wrong_way: false, candidates: [], vehicle_id: null, match_distance: null, matched_plate: null, latency_ms: null,
          extra_images: [], camera_ids: [], raw_plate: null, plate: null, confidence: null, review_reason: null, session_id: null,
          match_type: null, vehicle_class: 'BIKE', status: 'MATCHED', gate_id: '', direction: 'IN', ts: new Date().toISOString(),
          images: {}, id: e.id,
        };
        const ev: AnprEvent = { ...defaults, ...(e as Partial<AnprEvent>), images: e.plate_image ? { plate_crop: e.plate_image } : {} };
        return { ...cur, latest_events: [ev, ...cur.latest_events].slice(0, 30) };
      });
    }
    if (msg.topic === 'device.health' && (msg.data as { internet?: LiveData['internet'] }).internet) {
      const net = (msg.data as { internet: LiveData['internet'] }).internet;
      setLive((cur) => (cur ? { ...cur, internet: net, gateway: { ...cur.gateway, online: net.gateway_online ?? cur.gateway.online } } : cur));
    }
    clearTimeout(pending.current);
    pending.current = setTimeout(refresh, 1500);
  });

  return <Ctx.Provider value={{ live, error, refresh, updatedAt }}>{children}</Ctx.Provider>;
}

export const useSite = () => useContext(Ctx);

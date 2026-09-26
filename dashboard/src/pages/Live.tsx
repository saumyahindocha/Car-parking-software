import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import type { Device, LiveData } from '../api';
import { Bars } from '../charts';
import { ago, pct, plate, rupees, time } from '../format';
import { useSite } from '../site';
import { Badge, Card, Dot, Empty, ErrorBox, Loading, Stat, StatusBadge, Thumb } from '../ui';

export default function LivePage() {
  const { live, error, refresh } = useSite();
  const nav = useNavigate();
  if (!live) return error ? <ErrorBox error={error} onRetry={refresh} /> : <Loading />;

  const rc = live.review_counts;
  const rev = live.revenue_today ?? {};
  const recon = live.cash.reconciliation;
  const classes = Object.entries(live.occupancy);
  const totalOcc = classes.reduce((a, [, v]) => a + v.total, 0);
  const upi = (rev.walkin_upi_paise ?? 0) + (rev.pass_upi_paise ?? 0);
  const cashAmt = (rev.walkin_cash_paise ?? 0) + (rev.pass_cash_paise ?? 0);
  const reviewTotal = rc.unread + rc.review + rc.orphans + rc.offline_claims + rc.disputes;

  return (
    <div className="page">
      <div className="page-h">
        <h1>Live</h1>
        <span className="muted">Business date {live.date} · server time {time(live.now)} IST</span>
      </div>
      <ErrorBox error={error} onRetry={refresh} />

      <div className="stats">
        <Stat
          label="Vehicles inside"
          value={totalOcc}
          sub={
            classes.length
              ? classes.map(([vc, v]) => (
                  <span key={vc} className="occ">
                    {vc}: {v.total} <span className="muted">(open {v.OPEN} · prepaid {v.PREPAID} · pass {v.PASS})</span>
                  </span>
                ))
              : 'empty lot'
          }
        />
        <Stat label="Revenue today" value={rupees(rev.total_paise ?? 0)} sub={`UPI ${rupees(upi)} · cash ${rupees(cashAmt)}${rev.claimed_offline_paise ? ` · ${rupees(rev.claimed_offline_paise)} offline claims` : ''}`} />
        <Stat
          label="Cash today"
          value={rupees(recon.collected_paise)}
          sub={`handed over ${rupees(recon.handed_over_paise)} · deposited ${rupees(recon.deposited_paise)}`}
          tone={recon.gap_collected_vs_handed_paise > 0 ? 'warn' : undefined}
          onClick={() => nav('/cash')}
        />
        <Stat
          label="Unpaid > 30 min"
          value={live.unpaid_flagged}
          sub="no payment 30 min after entry"
          tone={live.unpaid_flagged ? 'bad' : 'ok'}
          onClick={() => nav('/review?tab=unpaid')}
        />
        <Stat
          label="Review queue"
          value={reviewTotal}
          tone={reviewTotal ? 'warn' : 'ok'}
          onClick={() => nav('/review')}
          sub={`unread ${rc.unread} · review ${rc.review} · orphans ${rc.orphans} · UPI claims ${rc.offline_claims} · disputes ${rc.disputes}`}
        />
        <Stat
          label="Sessions today"
          value={rev.sessions ?? 0}
          sub={`${rev.pass_sessions ?? 0} on pass (${pct((rev.sessions ?? 0) ? (rev.pass_sessions ?? 0) / (rev.sessions ?? 1) : 0)})`}
        />
      </div>

      <div className="grid-2">
        <Card title="Movements per gate per hour (today, IST)">
          <MovementsChart live={live} />
        </Card>
        <Card title="Devices & connectivity">
          <Connectivity live={live} />
          <DeviceTable devices={live.devices} />
        </Card>
      </div>

      <div className="grid-2 grid-2-wide-left">
        <Card title="Latest events" pad={false}>
          {live.latest_events.length === 0 ? (
            <Empty>No events yet today</Empty>
          ) : (
            <div className="table-wrap" style={{ maxHeight: 560 }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Plate image</th>
                    <th>Time</th>
                    <th>Gate</th>
                    <th>Plate</th>
                    <th className="num">Conf.</th>
                    <th>Status</th>
                    <th>Match</th>
                  </tr>
                </thead>
                <tbody>
                  {live.latest_events.map((e) => (
                    <tr key={e.id} className={e.status === 'UNREAD' || e.wrong_way ? 'row-bad' : e.status === 'REVIEW' ? 'row-warn' : ''}>
                      <td>
                        <Thumb src={e.images.plate_crop} alt={e.plate ?? 'unread plate'} />
                      </td>
                      <td className="mono">{time(e.ts)}</td>
                      <td>
                        {e.gate_id} <Badge tone={e.direction === 'IN' ? 'info' : 'muted'}>{e.direction}</Badge>
                      </td>
                      <td className="plate">
                        {e.vehicle_id ? <a onClick={() => nav(`/vehicles/${e.vehicle_id}`)}>{plate(e.plate)}</a> : plate(e.plate)}
                        {e.raw_plate && e.plate && e.raw_plate !== e.plate && <div className="muted small">read {e.raw_plate}</div>}
                      </td>
                      <td className="num">{e.confidence ? pct(e.confidence) : '—'}</td>
                      <td>
                        <StatusBadge status={e.wrong_way ? 'WRONG_WAY' : e.status} />
                        {e.review_reason && <div className="muted small">{e.review_reason.replace(/_/g, ' ')}</div>}
                      </td>
                      <td className="small">{e.match_type ?? '—'}{e.match_distance ? ` (d=${e.match_distance})` : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card title="Cash in hand (open shifts)" pad={false}>
          {live.cash.holdings.length === 0 ? (
            <Empty>No open shifts</Empty>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Worker</th>
                  <th>Zone</th>
                  <th className="num">In hand</th>
                  <th>vs limit</th>
                </tr>
              </thead>
              <tbody>
                {live.cash.holdings.map((h) => (
                  <tr key={h.user_id} className={h.blocked ? 'row-bad' : h.warn ? 'row-warn' : ''}>
                    <td>{h.name}</td>
                    <td>{h.zone_id ?? '—'}</td>
                    <td className="num">{rupees(h.cash_in_hand_paise)}</td>
                    <td>
                      <Meter value={h.cash_in_hand_paise} max={h.limit_paise} tone={h.blocked ? 'bad' : h.warn ? 'warn' : 'ok'} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}

export function Meter({ value, max, tone }: { value: number; max: number; tone: 'ok' | 'warn' | 'bad' }) {
  const p = max > 0 ? Math.min(1, value / max) : 0;
  return (
    <div className="meter" title={`${rupees(value)} of ${rupees(max)}`}>
      <div className={`meter-fill tone-${tone}`} style={{ width: `${p * 100}%` }} />
      <span className="meter-label">{Math.round(p * 100)}%</span>
    </div>
  );
}

function MovementsChart({ live }: { live: LiveData }) {
  const { data, series } = useMemo(() => {
    const keys = Array.from(new Set(live.movements.map((m) => `${m.gate_id} ${m.direction}`))).sort();
    const rows: Record<string, number | string>[] = Array.from({ length: 24 }, (_, h) => ({ hour: h }));
    for (const m of live.movements) rows[m.hour][`${m.gate_id} ${m.direction}`] = m.count;
    for (const r of rows) for (const k of keys) r[k] = (r[k] as number) ?? 0;
    const lastHour = Math.max(...live.movements.map((m) => m.hour), 6);
    const firstHour = Math.min(...live.movements.map((m) => m.hour), 5);
    return { data: rows.slice(firstHour, lastHour + 1), series: keys.map((k) => ({ key: k, label: k })) };
  }, [live.movements]);
  if (!series.length) return <Empty>No movements yet today</Empty>;
  return <Bars data={data} x="hour" series={series} height={260} xFormat={(h) => `${String(h).padStart(2, '0')}:00`} />;
}

function Connectivity({ live }: { live: LiveData }) {
  const net = live.internet;
  return (
    <div className="conn-row">
      <span className="pill">
        <Dot ok={net.online} /> Internet {net.online === null || net.online === undefined ? 'unknown' : net.online ? 'up' : 'DOWN'}
        {net.since && <span className="muted"> since {time(net.since, false)}</span>}
      </span>
      <span className="pill">
        <Dot ok={live.gateway.online} /> UPI gateway ({live.gateway.name}) {live.gateway.online === null ? 'unknown' : live.gateway.online ? 'up' : 'DOWN'}
      </span>
      {net.checked_at && <span className="muted small">checked {ago(net.checked_at)}</span>}
    </div>
  );
}

function num(v: unknown): number | null {
  return typeof v === 'number' ? v : null;
}

function DeviceTable({ devices }: { devices: Device[] }) {
  if (!devices.length) return <Empty>No device has sent a heartbeat yet</Empty>;
  return (
    <div className="table-wrap" style={{ maxHeight: 300 }}>
      <table className="tbl">
        <thead>
          <tr>
            <th></th>
            <th>Device</th>
            <th>Gate</th>
            <th>Last seen</th>
            <th className="num">FPS</th>
            <th className="num">Read rate</th>
          </tr>
        </thead>
        <tbody>
          {devices.map((d) => {
            const m = d.metrics ?? {};
            const stall = m.stream_ok === false;
            return (
              <tr key={d.id} className={!d.online || stall ? 'row-bad' : ''}>
                <td>
                  <Dot ok={d.online && !stall} title={d.online ? (stall ? 'stream stalled' : 'online') : 'offline'} />
                </td>
                <td>
                  {d.name ?? d.id} <span className="muted small">{d.kind.replace(/_/g, ' ').toLowerCase()}</span>
                </td>
                <td>{d.gate_id ?? '—'}</td>
                <td>{ago(d.last_seen)}</td>
                <td className="num">{num(m.fps)?.toFixed(1) ?? '—'}</td>
                <td className="num">{num(m.read_rate) !== null ? pct(num(m.read_rate), 1) : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

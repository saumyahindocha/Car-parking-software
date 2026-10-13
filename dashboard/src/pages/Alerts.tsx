import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type Alert } from '../api';
import { ago, dateTime } from '../format';
import { Badge, Card, Empty, ErrorBox, Loading, ReasonDialog, StatusBadge, useAction, useAsync } from '../ui';
import { useLiveRefresh } from '../useLive';
import { userName, useUsers } from '../users';

const KINDS = ['', 'EXIT_UNPAID', 'WRONG_WAY', 'CAMERA_STALL', 'INTERNET_DOWN', 'OFFLINE_CLAIM_STALE'];

export default function AlertsPage({ onChange }: { onChange: () => void }) {
  const [openOnly, setOpenOnly] = useState(true);
  const [kind, setKind] = useState('');
  const [hours, setHours] = useState(24);
  const { data, error, loading, reload } = useAsync(() => api.alerts({ open_only: openOnly, kind: kind || undefined, hours }), [openOnly, kind, hours]);
  useLiveRefresh(['alert'], reload, 800);
  const [acking, setAcking] = useState<Alert | null>(null);
  const { run, busy } = useAction();
  const users = useUsers();
  const nav = useNavigate();
  const ackAll = () =>
    run(async () => {
      for (const a of (data ?? []).filter((x) => !x.acknowledged_at)) await api.ackAlert(a.id, 'bulk acknowledged');
      await reload();
      onChange();
    }, 'All shown alerts acknowledged');

  return (
    <div className="page">
      <div className="page-h">
        <h1>Alerts</h1>
        <label className="check">
          <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> Open only
        </label>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {k ? k.replace(/_/g, ' ') : 'All kinds'}
            </option>
          ))}
        </select>
        <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          <option value={6}>Last 6 h</option>
          <option value={24}>Last 24 h</option>
          <option value={72}>Last 3 days</option>
          <option value={168}>Last 7 days</option>
        </select>
        {openOnly && (data ?? []).length > 1 && (
          <button className="btn btn-sm" disabled={busy} onClick={ackAll}>
            Acknowledge all shown
          </button>
        )}
      </div>
      <ErrorBox error={error} onRetry={reload} />
      <Card pad={false}>
        {!data && loading ? (
          <Loading />
        ) : (data ?? []).length === 0 ? (
          <Empty>{openOnly ? 'No open alerts' : 'No alerts in this period'}</Empty>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>When</th>
                <th>Severity</th>
                <th>Kind</th>
                <th>Gate</th>
                <th>Message</th>
                <th>Acknowledged</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(data ?? []).map((a) => (
                <tr key={a.id} className={!a.acknowledged_at ? (a.severity === 'CRIT' ? 'row-bad' : 'row-warn') : 'row-muted'}>
                  <td>
                    {dateTime(a.created_at)} <div className="muted small">{ago(a.created_at)}</div>
                  </td>
                  <td>
                    <StatusBadge status={a.severity} />
                  </td>
                  <td>
                    <Badge>{a.kind.replace(/_/g, ' ')}</Badge>
                  </td>
                  <td>{a.gate_id ?? '—'}</td>
                  <td>{a.message}</td>
                  <td className="small">
                    {a.acknowledged_at ? (
                      <>
                        {userName(users, a.acknowledged_by)} · {dateTime(a.acknowledged_at)}
                        {a.note && <div className="muted">{a.note}</div>}
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="actions">
                    {a.vehicle_id && (
                      <button className="btn btn-sm" onClick={() => nav(`/vehicles/${a.vehicle_id}`)}>
                        Vehicle
                      </button>
                    )}
                    {!a.acknowledged_at && (
                      <button className="btn btn-sm btn-primary" onClick={() => setAcking(a)}>
                        Acknowledge
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {acking && (
        <ReasonDialog
          title={`Acknowledge ${acking.kind.replace(/_/g, ' ').toLowerCase()} alert`}
          label="Note (what was done)"
          confirmLabel="Acknowledge"
          onClose={() => setAcking(null)}
          onSubmit={(note) =>
            run(() => api.ackAlert(acking.id, note), 'Alert acknowledged').then((ok) => {
              if (ok) {
                void reload();
                onChange();
              }
              return ok;
            })
          }
        />
      )}
    </div>
  );
}

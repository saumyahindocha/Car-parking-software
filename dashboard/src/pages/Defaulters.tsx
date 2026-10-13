import { useNavigate } from 'react-router-dom';
import { api, download, saveBlob, type DefaulterRow, type Defaulters } from '../api';
import { dateTime, istDate, plate, rupees } from '../format';
import { Card, Empty, ErrorBox, Loading, Stat, useAction, useAsync } from '../ui';

export default function DefaultersPage() {
  const { data, error, loading, reload } = useAsync(() => api.report<Defaulters>('defaulters'), []);
  const settings = useAsync(() => api.settings(), []);
  const { run, busy } = useAction();
  const thr = settings.data?.alert_balance_threshold_paise as number | undefined;
  const exportXlsx = () => run(() => download('/api/reports/defaulters', { format: 'xlsx' }, `defaulters-${istDate()}.xlsx`));
  const exportCsv = (rows: DefaulterRow[], name: string) => {
    const head = 'plate,balance_rupees,last_seen,visits,phone';
    const lines = rows.map((r) => [r.plate, (r.balance_paise / 100).toFixed(2), r.last_seen, r.visits, r.phone ?? ''].join(','));
    saveBlob(new Blob([[head, ...lines].join('\n')], { type: 'text/csv' }), `${name}-${istDate()}.csv`);
  };
  return (
    <div className="page">
      <div className="page-h">
        <h1>Defaulters</h1>
        <span className="muted">Vehicles with a balance above {thr !== undefined ? rupees(thr) : 'the alert threshold'}</span>
        <button className="btn btn-primary" disabled={busy} onClick={exportXlsx}>
          Export Excel
        </button>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {!data ? (
        loading && <Loading />
      ) : (
        <>
          <div className="stats">
            <Stat label="Regular defaulters" value={data.defaulters.length} sub={`owe ${rupees(data.defaulters_total_paise)}`} tone={data.defaulters.length ? 'warn' : 'ok'} />
            <Stat label="Unrecovered one-time dues" value={data.unrecovered_one_time.length} sub={`${rupees(data.unrecovered_total_paise)} — single visit, not seen for 7+ days`} />
          </div>
          <DefTable title="Defaulters (recovered at next entry)" rows={data.defaulters} onCsv={() => exportCsv(data.defaulters, 'defaulters')} />
          <DefTable title="Unrecovered one-time dues" rows={data.unrecovered_one_time} onCsv={() => exportCsv(data.unrecovered_one_time, 'one-time-dues')} />
        </>
      )}
    </div>
  );
}

function DefTable({ title, rows, onCsv }: { title: string; rows: DefaulterRow[]; onCsv: () => void }) {
  const nav = useNavigate();
  return (
    <Card title={`${title} · ${rows.length}`} pad={false} actions={rows.length ? <button className="btn btn-sm" onClick={onCsv}>CSV</button> : undefined}>
      {rows.length === 0 ? (
        <Empty>None</Empty>
      ) : (
        <div className="table-wrap" style={{ maxHeight: 480 }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Plate</th>
                <th className="num">Balance</th>
                <th>Last seen</th>
                <th className="num">Visits</th>
                <th>Phone</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.vehicle_id} className="clickable" onClick={() => nav(`/vehicles/${r.vehicle_id}`)}>
                  <td className="plate">{plate(r.plate)}</td>
                  <td className="num tone-text-bad">{rupees(r.balance_paise)}</td>
                  <td>{dateTime(r.last_seen)}</td>
                  <td className="num">{r.visits}</td>
                  <td>{r.phone ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

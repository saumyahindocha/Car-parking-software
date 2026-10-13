import { useMemo, useState, type ChangeEvent } from 'react';
import { api, download, type ImportResult, type ImportRow } from '../api';
import { rupees } from '../format';
import { Badge, Card, Empty, Stat, useAction } from '../ui';

type Filter = 'all' | 'error' | 'warning' | 'ok';

/** Import existing customers (pass holders, regulars, old dues) from a CSV / Excel file. */
export default function ImportPage() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportResult | null>(null);
  const [done, setDone] = useState<ImportResult | null>(null);
  const [filter, setFilter] = useState<Filter>('all');
  const { run, busy } = useAction();

  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    setFile(f);
    setPreview(null);
    setDone(null);
    if (f) void run(async () => setPreview(await api.importCustomers(f, false)));
  };
  const commit = (skipErrors: boolean) =>
    file &&
    run(async () => {
      const r = await api.importCustomers(file, true, skipErrors);
      setDone(r);
      setPreview(null);
    }, 'Customers imported');

  const s = preview?.summary;
  const rows = useMemo(
    () => (preview?.rows ?? []).filter((r) => filter === 'all' || r.status === filter),
    [preview, filter],
  );

  return (
    <div className="page">
      <div className="page-h">
        <h1>Import customers</h1>
        <span className="muted">
          Bring in monthly-pass holders, regular customers and unpaid dues from your previous system. Nothing changes until you
          confirm, and importing the same file again does not create duplicates.
        </span>
      </div>

      <Card title="1 · Prepare the file">
        <p>
          A spreadsheet (<b>.xlsx</b> or <b>.csv</b>) with one row per vehicle. Only <b>plate</b> is required; other columns are
          optional:
        </p>
        <table className="tbl tbl-compact">
          <tbody>
            <tr><td><code>plate</code></td><td>Number plate, any spacing (MH 43 AB 1234)</td></tr>
            <tr><td><code>vehicle_class</code></td><td>BIKE (default) or CAR</td></tr>
            <tr><td><code>name</code>, <code>phone</code></td><td>Customer name and 10-digit mobile (for receipts and pass reminders)</td></tr>
            <tr><td><code>pass_type</code></td><td>Name of a pass type, e.g. Monthly. Leave empty for customers without a pass</td></tr>
            <tr><td><code>pass_start</code>, <code>pass_end</code></td><td>Dates (YYYY-MM-DD or DD-MM-YYYY). The end date is the last day the pass is valid; if empty it is worked out from the pass type</td></tr>
            <tr><td><code>pass_amount</code></td><td>What they paid in rupees (defaults to the current price). Not counted as today’s revenue</td></tr>
            <tr><td><code>opening_balance</code></td><td>Old dues in rupees (e.g. 40), or a negative number for credit (e.g. -20)</td></tr>
            <tr><td><code>notes</code></td><td>Anything else</td></tr>
          </tbody>
        </table>
        <button className="btn" onClick={() => run(() => download('/api/import/customers/template', undefined, 'customer-import-template.csv'))}>
          Download template
        </button>
      </Card>

      <Card title="2 · Upload and check">
        <input type="file" accept=".csv,.xlsx,.xlsm,text/csv" onChange={pick} disabled={busy} />
        {busy && !preview && <p className="muted">Checking…</p>}
        {s && (
          <>
            <div className="stats">
              <Stat label="Rows" value={s.rows} />
              <Stat label="New vehicles" value={s.new_vehicles} />
              <Stat label="Passes" value={s.passes} />
              <Stat label="Contacts updated" value={s.contacts} />
              <Stat label="Opening dues" value={rupees(s.opening_dues_paise)} sub={`${s.opening_balances} balances`} />
              <Stat label="Opening credit" value={rupees(s.opening_credit_paise)} />
              <Stat label="Warnings" value={s.warnings} tone={s.warnings ? 'warn' : 'ok'} onClick={() => setFilter('warning')} />
              <Stat label="Errors" value={s.errors} tone={s.errors ? 'bad' : 'ok'} onClick={() => setFilter('error')} />
            </div>
            <div className="chips">
              {(['all', 'error', 'warning', 'ok'] as Filter[]).map((f) => (
                <button key={f} className={`chip chip-btn ${filter === f ? 'active' : ''}`} onClick={() => setFilter(f)}>
                  {f === 'all' ? 'All rows' : f === 'ok' ? 'OK' : f === 'error' ? 'Errors' : 'Warnings'}
                </button>
              ))}
            </div>
            <RowsTable rows={rows} />
          </>
        )}
      </Card>

      {s && (
        <Card title="3 · Import">
          {s.errors > 0 ? (
            <p>
              <b>{s.errors}</b> row{s.errors === 1 ? ' has' : 's have'} errors. Fix the file and upload it again, or import only the{' '}
              {s.importable} valid rows.
            </p>
          ) : (
            <p>Everything checks out. {s.importable} rows will be imported.</p>
          )}
          <div className="btn-row">
            {s.errors === 0 ? (
              <button className="btn btn-primary" disabled={busy || s.importable === 0} onClick={() => commit(false)}>
                Import {s.importable} rows
              </button>
            ) : (
              <button className="btn btn-danger-outline" disabled={busy || s.importable === 0} onClick={() => commit(true)}>
                Import {s.importable} valid rows, skip {s.errors}
              </button>
            )}
          </div>
        </Card>
      )}

      {done && (
        <Card title="Imported">
          <p>
            <b>{done.imported_rows}</b> rows imported (batch <code>{done.batch}</code>). Pass holders are now recognised at the gates;
            opening dues are added to each vehicle’s next payment. Every change is in the audit log.
          </p>
        </Card>
      )}
    </div>
  );
}

function RowsTable({ rows }: { rows: ImportRow[] }) {
  if (!rows.length) return <Empty>No rows in this view</Empty>;
  return (
    <div className="table-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th>Row</th>
            <th>Plate</th>
            <th>Status</th>
            <th>Will do</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 1000).map((r) => (
            <tr key={r.row}>
              <td>{r.row}</td>
              <td className="plate">{r.display_plate || '—'}</td>
              <td>
                <Badge tone={r.status === 'error' ? 'bad' : r.status === 'warning' ? 'warn' : 'ok'}>{r.status}</Badge>
              </td>
              <td>{r.actions.join(' · ') || '—'}</td>
              <td className="muted">{r.messages.join(' · ')}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 1000 && <p className="muted">Showing the first 1,000 of {rows.length} rows.</p>}
    </div>
  );
}

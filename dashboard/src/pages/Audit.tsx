import { Fragment, useState } from 'react';
import { api } from '../api';
import { dateTime } from '../format';
import { Badge, Card, Empty, ErrorBox, Loading, useAsync } from '../ui';
import { userName, useUsers } from '../users';

export default function AuditPage() {
  const [table, setTable] = useState('');
  const [rowId, setRowId] = useState('');
  const [limit, setLimit] = useState(200);
  const [applied, setApplied] = useState({ table: '', rowId: '', limit: 200 });
  const { data, error, loading, reload } = useAsync(
    () => api.audit({ table: applied.table || undefined, row_id: applied.rowId || undefined, limit: applied.limit }),
    [applied],
  );
  const users = useUsers();
  const [open, setOpen] = useState<number | null>(null);
  const tables = Array.from(new Set((data ?? []).map((a) => a.table))).sort();

  return (
    <div className="page">
      <div className="page-h">
        <h1>Audit log</h1>
        <span className="muted">Every create / update / delete, with before and after values. Admin only.</span>
      </div>
      <form
        className="filters"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied({ table, rowId, limit });
        }}
      >
        <input list="audit-tables" placeholder="table (e.g. payments)" value={table} onChange={(e) => setTable(e.target.value.trim())} />
        <datalist id="audit-tables">
          {tables.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
        <input placeholder="row id" value={rowId} onChange={(e) => setRowId(e.target.value.trim())} />
        <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          {[100, 200, 500, 2000].map((n) => (
            <option key={n} value={n}>
              last {n}
            </option>
          ))}
        </select>
        <button className="btn btn-primary" type="submit">
          Filter
        </button>
      </form>
      <ErrorBox error={error} onRetry={reload} />
      <Card pad={false}>
        {!data && loading ? (
          <Loading />
        ) : (data ?? []).length === 0 ? (
          <Empty>No audit rows</Empty>
        ) : (
          <div className="table-wrap" style={{ maxHeight: 'calc(100vh - 230px)' }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>#</th>
                  <th>When</th>
                  <th>User</th>
                  <th>Action</th>
                  <th>Table</th>
                  <th>Row</th>
                  <th>Changed fields</th>
                </tr>
              </thead>
              <tbody>
                {(data ?? []).map((a) => {
                  const changed = diffKeys(a.before, a.after);
                  return (
                    <Fragment key={a.id}>
                      <tr className="clickable" onClick={() => setOpen(open === a.id ? null : a.id)}>
                        <td>{a.id}</td>
                        <td>{dateTime(a.created_at, true)}</td>
                        <td>{a.user_id ? userName(users, a.user_id) : <span className="muted">system</span>}</td>
                        <td>
                          <Badge tone={a.action === 'DELETE' ? 'bad' : a.action === 'INSERT' || a.action === 'CREATE' ? 'ok' : 'info'}>{a.action}</Badge>
                        </td>
                        <td className="mono">{a.table}</td>
                        <td className="mono">{a.row_id}</td>
                        <td className="small">{changed.slice(0, 8).join(', ')}{changed.length > 8 ? '…' : ''}</td>
                      </tr>
                      {open === a.id && (
                        <tr className="detail-row">
                          <td colSpan={7}>
                            <AuditDiff before={a.before} after={a.after} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function asObj(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function diffKeys(before: unknown, after: unknown): string[] {
  const b = asObj(before);
  const a = asObj(after);
  const keys = new Set([...Object.keys(b), ...Object.keys(a)]);
  return [...keys].filter((k) => JSON.stringify(b[k]) !== JSON.stringify(a[k]));
}

function AuditDiff({ before, after }: { before: unknown; after: unknown }) {
  const b = asObj(before);
  const a = asObj(after);
  const keys = [...new Set([...Object.keys(b), ...Object.keys(a)])];
  return (
    <table className="tbl tbl-compact diff">
      <thead>
        <tr>
          <th>Field</th>
          <th>Before</th>
          <th>After</th>
        </tr>
      </thead>
      <tbody>
        {keys.map((k) => {
          const changed = JSON.stringify(b[k]) !== JSON.stringify(a[k]);
          return (
            <tr key={k} className={changed ? 'row-warn' : ''}>
              <td className="mono">{k}</td>
              <td className="mono small">{fmt(b[k])}</td>
              <td className="mono small">{fmt(a[k])}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function fmt(v: unknown): string {
  if (v === undefined) return '';
  return typeof v === 'string' ? v : JSON.stringify(v);
}

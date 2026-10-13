import { useState, type ReactNode } from 'react';
import { api, download, type CashRecon, type Defaulters, type RevenueDay, type UpiRecon, type WorkerComparison } from '../api';
import { Bars } from '../charts';
import { addDays, istDate, pct, rupees } from '../format';
import { AutoTable, Badge, Card, Empty, ErrorBox, Field, Loading, Stat, useAction, useAsync } from '../ui';

type ReportId =
  | 'revenue' | 'cash-reconciliation' | 'upi-reconciliation' | 'worker-comparison' | 'collections' | 'disputes'
  | 'unpaid-sessions' | 'passes' | 'anpr-accuracy' | 'peak-hours' | 'overrides' | 'defaulters';

const REPORTS: { id: ReportId; label: string; hint: string; exportable: boolean }[] = [
  { id: 'revenue', label: 'Daily revenue', hint: 'walk-in vs pass, UPI vs cash', exportable: true },
  { id: 'cash-reconciliation', label: 'Cash reconciliation', hint: 'collected → handed over → deposited → credited (start date)', exportable: true },
  { id: 'upi-reconciliation', label: 'UPI reconciliation', hint: 'gateway settlement / bank statement vs system', exportable: false },
  { id: 'worker-comparison', label: 'Worker comparison', hint: 'per worker per shift, with automatic flags', exportable: true },
  { id: 'collections', label: 'Collections per shift', hint: 'per worker per shift', exportable: true },
  { id: 'disputes', label: 'Disputes by worker', hint: 'by worker and outcome', exportable: true },
  { id: 'unpaid-sessions', label: 'Sessions with no payment', hint: 'non-pass sessions with no confirmed payment', exportable: true },
  { id: 'passes', label: 'Passes', hint: 'active, expiring, sales, traffic share', exportable: true },
  { id: 'anpr-accuracy', label: 'ANPR accuracy', hint: 'read / approximate / manual rates per camera', exportable: true },
  { id: 'peak-hours', label: 'Peak hours', hint: 'IN / OUT by hour of day', exportable: true },
  { id: 'overrides', label: 'Overrides', hint: 'amount overrides, reversals, refunds, adjustments', exportable: true },
  { id: 'defaulters', label: 'Defaulters', hint: 'balances above threshold (current)', exportable: true },
];

export default function ReportsPage() {
  const today = istDate();
  const [start, setStart] = useState(addDays(today, -6));
  const [end, setEnd] = useState(today);
  const [rep, setRep] = useState<ReportId>('revenue');
  const { run, busy } = useAction();
  const meta = REPORTS.find((r) => r.id === rep)!;

  const presets: [string, string, string][] = [
    ['Today', today, today],
    ['Yesterday', addDays(today, -1), addDays(today, -1)],
    ['Last 7 days', addDays(today, -6), today],
    ['Last 30 days', addDays(today, -29), today],
    ['This month', today.slice(0, 8) + '01', today],
  ];
  const exp = (format: 'xlsx' | 'pdf') =>
    run(() => download(`/api/reports/${rep}`, { start, end, format }, `${rep}_${start}_${end}.${format}`));

  return (
    <div className="page">
      <div className="page-h">
        <h1>Reports</h1>
        <label className="inline-field">
          From <input type="date" value={start} max={end} onChange={(e) => setStart(e.target.value || today)} />
        </label>
        <label className="inline-field">
          To <input type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value || today)} />
        </label>
        <div className="btn-group">
          {presets.map(([l, s, e]) => (
            <button key={l} className={`btn btn-sm ${s === start && e === end ? 'active' : ''}`} onClick={() => (setStart(s), setEnd(e))}>
              {l}
            </button>
          ))}
        </div>
      </div>
      <div className="reports-layout">
        <nav className="report-list">
          {REPORTS.map((r) => (
            <button key={r.id} className={`report-item ${rep === r.id ? 'active' : ''}`} onClick={() => setRep(r.id)}>
              <span>{r.label}</span>
              <small>{r.hint}</small>
            </button>
          ))}
        </nav>
        <div className="report-body">
          <div className="section-h">
            <h2>{meta.label}</h2>
            <span className="muted small">{meta.hint}</span>
            {meta.exportable && (
              <div className="btn-group right">
                <button className="btn btn-sm" disabled={busy} onClick={() => exp('xlsx')}>
                  Export Excel
                </button>
                <button className="btn btn-sm" disabled={busy} onClick={() => exp('pdf')}>
                  Export PDF
                </button>
              </div>
            )}
          </div>
          {rep === 'upi-reconciliation' ? <UpiRecon date={end} /> : <ReportView key={`${rep}|${start}|${end}`} id={rep} start={start} end={end} />}
        </div>
      </div>
    </div>
  );
}

function ReportView({ id, start, end }: { id: ReportId; start: string; end: string }) {
  const { data, error, loading, reload } = useAsync(() => api.report<unknown>(id, start, end), [id, start, end]);
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (loading && data === undefined) return <Loading />;
  if (data === undefined) return null;
  switch (id) {
    case 'revenue':
      return <Revenue rows={data as RevenueDay[]} start={start} end={end} />;
    case 'cash-reconciliation':
      return <CashReconView r={data as CashRecon} />;
    case 'worker-comparison': {
      const wc = data as WorkerComparison;
      return (
        <Card pad={false} actions={<span className="muted small">averages: cash share {pct(wc.averages.cash_share)} · unpaid rate {pct(wc.averages.unpaid_rate, 1)}</span>} title={`${wc.rows.length} shifts`}>
          <AutoTable
            rows={wc.rows as unknown as Record<string, unknown>[]}
            columns={['name', 'zone_id', 'opened_at', 'closed_at', 'collections', 'upi_paise', 'cash_paise', 'cash_share', 'zone_sessions', 'unpaid_in_zone',
              'unpaid_rate', 'disputes_total', 'disputes_upheld', 'disputes_unresolved', 'handover_variance_paise', 'cash_receipt_phone_share', 'limit_breaches', 'flags']}
            highlight={(r) => ((r.flags as string[]).length ? 'bad' : undefined)}
            render={{ flags: (v) => (v as string[]).map((f) => <Badge key={f} tone="bad">{f.replace(/_/g, ' ')}</Badge>) }}
          />
        </Card>
      );
    }
    case 'disputes':
      return (
        <Card pad={false}>
          <AutoTable rows={data as Record<string, unknown>[]} highlight={(r) => (r.repeat_flag ? 'bad' : undefined)} />
        </Card>
      );
    case 'passes':
      return <Passes d={data as Record<string, unknown>} />;
    case 'anpr-accuracy':
      return (
        <Card pad={false}>
          <AutoTable rows={data as Record<string, unknown>[]} highlight={(r) => ((r.read_rate as number) < 0.9 ? 'warn' : undefined)} />
        </Card>
      );
    case 'peak-hours': {
      const rows = data as { hour: number; IN: number; OUT: number }[];
      return (
        <>
          <Card title="Movements by hour of day (IST)">
            <Bars data={rows} x="hour" series={[{ key: 'IN', label: 'In' }, { key: 'OUT', label: 'Out' }]} height={280} xFormat={(h) => `${String(h).padStart(2, '0')}:00`} />
          </Card>
          <Card pad={false}>
            <AutoTable rows={rows.filter((r) => r.IN || r.OUT)} empty="No movements in range" />
          </Card>
        </>
      );
    }
    case 'defaulters': {
      const d = data as Defaulters;
      return (
        <>
          <Card title={`Defaulters · ${rupees(d.defaulters_total_paise)}`} pad={false}>
            <AutoTable rows={d.defaulters as unknown as Record<string, unknown>[]} columns={['plate', 'balance_paise', 'last_seen', 'visits', 'phone']} />
          </Card>
          <Card title={`Unrecovered one-time dues · ${rupees(d.unrecovered_total_paise)}`} pad={false}>
            <AutoTable rows={d.unrecovered_one_time as unknown as Record<string, unknown>[]} columns={['plate', 'balance_paise', 'last_seen', 'visits', 'phone']} />
          </Card>
        </>
      );
    }
    default:
      return (
        <Card pad={false}>
          <AutoTable rows={(data as Record<string, unknown>[]) ?? []} maxHeight={640} />
        </Card>
      );
  }
}

function Revenue({ rows, start, end }: { rows: RevenueDay[]; start: string; end: string }) {
  if (!rows.length) return <Empty>No revenue in range</Empty>;
  const byDate = new Map(rows.map((r) => [r.date, r]));
  const days: RevenueDay[] = [];
  for (let d = start; d <= end && days.length < 400; d = addDays(d, 1)) days.push(byDate.get(d) ?? { date: d });
  const sum = (k: keyof RevenueDay) => rows.reduce((a, r) => a + ((r[k] as number) ?? 0), 0);
  const chart = days.map((r) => ({
    date: r.date?.slice(5),
    walkin_upi: (r.walkin_upi_paise ?? 0) / 100,
    walkin_cash: (r.walkin_cash_paise ?? 0) / 100,
    pass_upi: (r.pass_upi_paise ?? 0) / 100,
    pass_cash: (r.pass_cash_paise ?? 0) / 100,
  }));
  return (
    <>
      <div className="stats">
        <Stat label="Total collected" value={rupees(sum('total_paise'))} sub={`${sum('payments')} payments`} />
        <Stat label="Walk-in" value={rupees(sum('walkin_upi_paise') + sum('walkin_cash_paise'))} sub={`UPI ${rupees(sum('walkin_upi_paise'))} · cash ${rupees(sum('walkin_cash_paise'))}`} />
        <Stat label="Passes" value={rupees(sum('pass_upi_paise') + sum('pass_cash_paise'))} sub={`UPI ${rupees(sum('pass_upi_paise'))} · cash ${rupees(sum('pass_cash_paise'))}`} />
        <Stat label="Charges posted" value={rupees(sum('charges_paise'))} sub="actual-time charges at exit" />
        <Stat label="Offline claims (unconfirmed)" value={rupees(sum('claimed_offline_paise'))} tone={sum('claimed_offline_paise') ? 'warn' : undefined} />
      </div>
      <Card title="Revenue per day (₹)">
        <Bars data={chart} x="date" stacked height={280} yFormat={(v) => `₹${v.toLocaleString('en-IN')}`}
          series={[{ key: 'walkin_upi', label: 'Walk-in UPI' }, { key: 'walkin_cash', label: 'Walk-in cash' }, { key: 'pass_upi', label: 'Pass UPI' }, { key: 'pass_cash', label: 'Pass cash' }]} />
      </Card>
      <Card pad={false}>
        <AutoTable rows={rows as Record<string, unknown>[]} />
      </Card>
    </>
  );
}

export function CashReconView({ r }: { r: CashRecon }) {
  return (
    <>
      <div className="stats funnel">
        <Stat label="Collected" value={rupees(r.collected_paise)} sub={r.reversed_paise ? `${rupees(r.reversed_paise)} reversed` : undefined} />
        <Stat label="Handed over" value={rupees(r.handed_over_paise)} sub={`gap ${rupees(r.gap_collected_vs_handed_paise)}`} tone={r.gap_collected_vs_handed_paise ? 'warn' : 'ok'} />
        <Stat label="Deposited" value={rupees(r.deposited_paise)} sub={`gap ${rupees(r.gap_handed_vs_deposited_paise)}`} tone={r.gap_handed_vs_deposited_paise ? 'warn' : 'ok'} />
        <Stat label="Bank credited" value={rupees(r.bank_credited_paise)} sub={r.gap_deposited_vs_credited_paise === null ? 'pending' : `gap ${rupees(r.gap_deposited_vs_credited_paise)}`}
          tone={r.gap_deposited_vs_credited_paise ? 'bad' : undefined} />
        <Stat label="Handover variance" value={rupees(r.handover_variance_paise, { signed: true })} tone={r.handover_variance_paise < 0 ? 'bad' : undefined} />
      </div>
      <Card title={`Per worker · ${r.date}`} pad={false}>
        <AutoTable rows={r.per_worker as unknown as Record<string, unknown>[]} columns={['name', 'collected_paise', 'handed_over_paise', 'gap_paise']}
          highlight={(x) => ((x.gap_paise as number) > 0 ? 'warn' : undefined)} empty="No cash collected" />
      </Card>
      <Card title="Deposits" pad={false}>
        <AutoTable rows={r.deposits as unknown as Record<string, unknown>[]} empty="No deposit recorded for this date" />
      </Card>
    </>
  );
}

function Passes({ d }: { d: Record<string, unknown> }) {
  const expiring = (d.expiring_this_week as Record<string, unknown>[]) ?? [];
  return (
    <>
      <div className="stats">
        <Stat label="Active passes" value={String(d.active)} />
        <Stat label="Sold in range" value={String(d.sold_count)} sub={rupees(d.revenue_paise as number)} />
        <Stat label="Traffic on pass" value={pct(d.traffic_share_on_pass as number, 1)} sub={`${d.pass_sessions} of ${d.total_sessions} sessions`} />
        <Stat label="Expiring this week" value={expiring.length} tone={expiring.length ? 'warn' : undefined} />
      </div>
      <Card title="Expiring this week" pad={false}>
        <AutoTable rows={expiring} columns={['plate', 'pass_type', 'starts_on', 'ends_on', 'amount_paise', 'phone', 'status']} empty="No pass expires in the next 7 days" />
      </Card>
    </>
  );
}

function UpiRecon({ date: initial }: { date: string }) {
  const [date, setDate] = useState(initial);
  const [file, setFile] = useState<File | null>(null);
  const [res, setRes] = useState<UpiRecon | null>(null);
  const { run, busy } = useAction();
  return (
    <>
      <div className="grid-2">
        <Card title="Pull gateway settlement">
          <p className="muted small">Fetches the gateway's settlement report for the day, confirms matching offline claims and lists mismatches.</p>
          <div className="btn-row">
            <Field label="Date">
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <button className="btn btn-primary" disabled={busy} onClick={() => run(async () => setRes(await api.upiReconRun(date)), 'Reconciliation complete')}>
              Run reconciliation
            </button>
          </div>
        </Card>
        <Card title="Upload bank statement CSV">
          <p className="muted small">
            Columns: <code>txn_ref</code>, <code>amount</code> (rupees) or <code>amount_paise</code>, optional <code>utr</code>.
          </p>
          <div className="btn-row">
            <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <button className="btn btn-primary" disabled={busy || !file} onClick={() => run(async () => setRes(await api.upiReconUpload(file!)), 'Statement reconciled')}>
              Upload &amp; reconcile
            </button>
          </div>
        </Card>
      </div>
      {res && <UpiResult r={res} />}
    </>
  );
}

function UpiResult({ r }: { r: UpiRecon }): ReactNode {
  return (
    <>
      <div className="stats">
        <Stat label="Settlement lines" value={r.lines} sub={r.date ? `for ${r.date}` : 'uploaded file'} />
        <Stat label="Offline claims confirmed" value={r.confirmed_offline.length} tone="ok" />
        <Stat label="Amount mismatches" value={r.amount_mismatch.length} tone={r.amount_mismatch.length ? 'bad' : 'ok'} />
        <Stat label="Unknown credits" value={r.unknown_credits.length} tone={r.unknown_credits.length ? 'warn' : 'ok'} sub="money received, no payment in system" />
        <Stat label="Missing from settlement" value={r.missing_from_settlement.length} tone={r.missing_from_settlement.length ? 'bad' : 'ok'} sub="confirmed in system, not settled" />
      </div>
      <div className="grid-2">
        <Card title="Confirmed offline claims" pad={false}>
          <AutoTable rows={r.confirmed_offline.map((id) => ({ payment_id: id }))} empty="None" />
        </Card>
        <Card title="Amount mismatches" pad={false}>
          <AutoTable rows={r.amount_mismatch as unknown as Record<string, unknown>[]} empty="None" highlight={() => 'bad'} />
        </Card>
        <Card title="Unknown credits" pad={false}>
          <AutoTable rows={r.unknown_credits.map((u) => ({ txn_ref: u.txn_ref, amount_paise: u.amount_paise, parsed: u.parsed ? JSON.stringify(u.parsed) : '' }))} empty="None" />
        </Card>
        <Card title="Missing from settlement" pad={false}>
          <AutoTable rows={r.missing_from_settlement as unknown as Record<string, unknown>[]} empty="None" highlight={() => 'warn'} />
        </Card>
      </div>
    </>
  );
}

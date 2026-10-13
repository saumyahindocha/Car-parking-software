import { useState } from 'react';
import { api, type Deposit, type Handover, type WorkerComparison, type WorkerRow } from '../api';
import { addDays, dateTime, istDate, parseRupees, pct, rupees } from '../format';
import { Badge, Card, Empty, ErrorBox, Field, Modal, ReasonDialog, Stat, StatusBadge, Tabs, useAction, useAsync } from '../ui';
import { useLiveRefresh } from '../useLive';
import { userName, useUsers } from '../users';
import { Meter } from './Live';

const DENOMS = [500, 200, 100, 50, 20, 10, 5, 2, 1];

const FLAG_TEXT: Record<string, string> = {
  HIGH_UNPAID_IN_ZONE: 'Unpaid rate in zone well above peers',
  HIGH_UNPAID_LOW_CASH: 'High unpaid in zone + below-average cash share',
  UNPAID_OUTLIER: 'Unpaid rate > 1.5σ above site average',
  REPEATED_DISPUTES: '3+ upheld/unresolved disputes',
  CASH_SHORT: 'Cash short at handover or shift close',
  LIMIT_BREACH: 'Cash limit breached (offline sync)',
  LOW_RECEIPT_TO_PHONE: 'Few cash receipts delivered to a phone',
};

export default function CashPage() {
  const [date, setDate] = useState(istDate());
  const holdings = useAsync(() => api.holdings(), []);
  const recon = useAsync(() => api.cashRecon(date), [date]);
  const pending = useAsync(() => api.handovers('PENDING'), []);
  const [hoTab, setHoTab] = useState<'PENDING' | 'ALL'>('PENDING');
  const allHo = useAsync(() => (hoTab === 'ALL' ? api.handovers(null) : Promise.resolve([] as Handover[])), [hoTab]);
  const deposits = useAsync(() => api.deposits(), []);
  const users = useUsers();
  const [confirming, setConfirming] = useState<Handover | null>(null);
  const [rejecting, setRejecting] = useState<Handover | null>(null);
  const [newDeposit, setNewDeposit] = useState(false);
  const [crediting, setCrediting] = useState<Deposit | null>(null);
  const { run } = useAction();

  const reloadAll = () => {
    void holdings.reload();
    void recon.reload();
    void pending.reload();
    void deposits.reload();
    if (hoTab === 'ALL') void allHo.reload();
  };
  useLiveRefresh(['cash.updated', 'handover.pending', 'handover.confirmed', 'payment.updated'], reloadAll, 1500);

  const r = recon.data;
  const hoRows = hoTab === 'PENDING' ? pending.data ?? [] : allHo.data ?? [];
  return (
    <div className="page">
      <div className="page-h">
        <h1>Cash control</h1>
        <label className="inline-field">
          Business date <input type="date" value={date} max={istDate()} onChange={(e) => setDate(e.target.value || istDate())} />
        </label>
      </div>

      <ErrorBox error={recon.error} onRetry={recon.reload} />
      {r && (
        <div className="stats funnel">
          <Stat label="Cash collected (system)" value={rupees(r.collected_paise)} sub={r.reversed_paise ? `${rupees(r.reversed_paise)} reversed` : 'confirmed cash payments'} />
          <Stat label="Handed over to supervisor" value={rupees(r.handed_over_paise)} sub={<Gap v={r.gap_collected_vs_handed_paise} label="not yet handed over" />}
            tone={r.gap_collected_vs_handed_paise ? 'warn' : 'ok'} />
          <Stat label="Deposited in bank" value={rupees(r.deposited_paise)} sub={<Gap v={r.gap_handed_vs_deposited_paise} label="not deposited" />}
            tone={r.gap_handed_vs_deposited_paise ? 'warn' : 'ok'} />
          <Stat label="Credited by bank" value={rupees(r.bank_credited_paise)}
            sub={r.gap_deposited_vs_credited_paise === null ? 'awaiting bank credit' : <Gap v={r.gap_deposited_vs_credited_paise} label="deposit vs credit" />}
            tone={r.gap_deposited_vs_credited_paise ? 'bad' : r.gap_deposited_vs_credited_paise === 0 ? 'ok' : undefined} />
          <Stat label="Handover variance" value={rupees(r.handover_variance_paise, { signed: true })} sub="counted − declared"
            tone={r.handover_variance_paise < 0 ? 'bad' : r.handover_variance_paise > 0 ? 'warn' : 'ok'} />
        </div>
      )}

      <div className="grid-2">
        <Card title="Live cash in hand" pad={false} actions={<button className="btn btn-sm" onClick={() => holdings.reload()}>Refresh</button>}>
          <ErrorBox error={holdings.error} />
          {(holdings.data ?? []).length === 0 ? (
            <Empty>No open shifts</Empty>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Worker</th>
                  <th>Shift</th>
                  <th>Zone</th>
                  <th className="num">In hand</th>
                  <th className="num">Limit</th>
                  <th style={{ width: '30%' }}>Usage</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {(holdings.data ?? []).map((h) => (
                  <tr key={h.user_id} className={h.blocked ? 'row-bad' : h.warn ? 'row-warn' : ''}>
                    <td>{h.name}</td>
                    <td>#{h.shift_id}</td>
                    <td>{h.zone_id ?? '—'}</td>
                    <td className="num">{rupees(h.cash_in_hand_paise)}</td>
                    <td className="num">{rupees(h.limit_paise)}</td>
                    <td>
                      <Meter value={h.cash_in_hand_paise} max={h.limit_paise} tone={h.blocked ? 'bad' : h.warn ? 'warn' : 'ok'} />
                    </td>
                    <td>{h.blocked ? <Badge tone="bad">BLOCKED</Badge> : h.warn ? <Badge tone="warn">WARN</Badge> : <Badge tone="ok">OK</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title={`Per worker · ${date}`} pad={false}>
          {!r || r.per_worker.length === 0 ? (
            <Empty>No cash collected on {date}</Empty>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Worker</th>
                  <th className="num">Collected</th>
                  <th className="num">Handed over</th>
                  <th className="num">Gap</th>
                </tr>
              </thead>
              <tbody>
                {r.per_worker.map((w) => (
                  <tr key={w.user_id} className={w.gap_paise > 0 ? 'row-warn' : ''}>
                    <td>{w.name ?? userName(users, w.user_id)}</td>
                    <td className="num">{rupees(w.collected_paise)}</td>
                    <td className="num">{rupees(w.handed_over_paise)}</td>
                    <td className="num">{rupees(w.gap_paise)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Card
        title="Cash handovers"
        pad={false}
        actions={
          <Tabs value={hoTab} onChange={setHoTab} tabs={[{ id: 'PENDING', label: 'Pending', count: pending.data?.length ?? 0 }, { id: 'ALL', label: 'Recent (all)' }]} />
        }
      >
        <ErrorBox error={pending.error} />
        {hoRows.length === 0 ? (
          <Empty>{hoTab === 'PENDING' ? 'No handovers waiting for a count' : 'No handovers yet'}</Empty>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Declared</th>
                <th>Worker</th>
                <th className="num">System expects</th>
                <th className="num">Declared</th>
                <th>Denominations</th>
                <th className="num">Counted</th>
                <th className="num">Variance</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {hoRows.map((h) => (
                <tr key={h.id} className={h.variance_paise ? (h.variance_paise < 0 ? 'row-bad' : 'row-warn') : ''}>
                  <td>{dateTime(h.declared_at)}</td>
                  <td>{h.from_name}</td>
                  <td className="num">{rupees(h.expected_paise)}</td>
                  <td className="num">{rupees(h.declared_paise)}</td>
                  <td className="small mono">{denomText(h.declared_denoms)}</td>
                  <td className="num">{rupees(h.counted_paise)}</td>
                  <td className="num">{h.variance_paise === null ? '—' : rupees(h.variance_paise, { signed: true })}</td>
                  <td>
                    <StatusBadge status={h.status} />
                    {h.note && <div className="small muted">{h.note}</div>}
                  </td>
                  <td className="actions">
                    {h.status === 'PENDING' && (
                      <>
                        <button className="btn btn-sm btn-primary" onClick={() => setConfirming(h)}>
                          Count &amp; confirm
                        </button>
                        <button className="btn btn-sm btn-danger-outline" onClick={() => setRejecting(h)}>
                          Reject
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card
        title="Bank deposits"
        pad={false}
        actions={
          <button className="btn btn-sm btn-primary" onClick={() => setNewDeposit(true)}>
            Record deposit
          </button>
        }
      >
        <ErrorBox error={deposits.error} />
        {(deposits.data ?? []).length === 0 ? (
          <Empty>No deposits recorded</Empty>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Business date</th>
                <th className="num">Amount</th>
                <th>Slip ref</th>
                <th>Deposited</th>
                <th>By</th>
                <th>Bank status</th>
                <th className="num">Credited</th>
                <th>Note</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(deposits.data ?? []).map((d) => (
                <tr key={d.id} className={d.bank_status === 'MISMATCH' ? 'row-bad' : ''}>
                  <td>{d.business_date}</td>
                  <td className="num">{rupees(d.amount_paise)}</td>
                  <td className="mono">{d.slip_ref}</td>
                  <td>{dateTime(d.deposited_at)}</td>
                  <td>{userName(users, d.deposited_by)}</td>
                  <td>
                    <StatusBadge status={d.bank_status} />
                  </td>
                  <td className="num">{rupees(d.bank_credited_paise)}</td>
                  <td className="small">{d.note}</td>
                  <td className="actions">
                    <button className="btn btn-sm" onClick={() => setCrediting(d)}>
                      {d.bank_status === 'PENDING' ? 'Mark bank credit' : 'Update credit'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <WorkerSection users={users} />

      {confirming && <ConfirmHandover h={confirming} onClose={() => setConfirming(null)} onDone={reloadAll} />}
      {rejecting && (
        <ReasonDialog
          title={`Reject handover from ${rejecting.from_name} (${rupees(rejecting.declared_paise)})`}
          danger
          confirmLabel="Reject handover"
          onClose={() => setRejecting(null)}
          onSubmit={(note) => run(() => api.rejectHandover(rejecting.id, note), 'Handover rejected').then((ok) => (ok && reloadAll(), ok))}
        />
      )}
      {newDeposit && <DepositDialog defaultDate={date} suggested={r ? r.handed_over_paise - r.deposited_paise : 0} onClose={() => setNewDeposit(false)} onDone={reloadAll} />}
      {crediting && <CreditDialog d={crediting} onClose={() => setCrediting(null)} onDone={reloadAll} />}
    </div>
  );
}

function Gap({ v, label }: { v: number; label: string }) {
  if (!v) return <span className="tone-text-ok">no gap</span>;
  return (
    <span className={v > 0 ? 'tone-text-warn' : 'tone-text-bad'}>
      gap {rupees(v)} {label}
    </span>
  );
}

function denomText(d: Record<string, number> | null | undefined): string {
  if (!d) return '—';
  return Object.entries(d)
    .filter(([, n]) => n)
    .sort((a, b) => Number(b[0]) - Number(a[0]))
    .map(([k, n]) => `₹${k}×${n}`)
    .join(' ');
}

function denomTotal(d: Record<string, number>): number {
  return Object.entries(d).reduce((a, [k, n]) => a + Number(k) * 100 * (n || 0), 0);
}

function ConfirmHandover({ h, onClose, onDone }: { h: Handover; onClose: () => void; onDone: () => void }) {
  const [counts, setCounts] = useState<Record<string, number>>(() => Object.fromEntries(DENOMS.map((d) => [String(d), h.declared_denoms?.[String(d)] ?? 0])));
  const [photo, setPhoto] = useState<File | null>(null);
  const [note, setNote] = useState('');
  const { run, busy } = useAction();
  const counted = denomTotal(counts);
  const variance = counted - h.declared_paise;
  const valid = !!photo && counted > 0 && (variance === 0 || note.trim());
  const submit = async () => {
    const nonzero = Object.fromEntries(Object.entries(counts).filter(([, n]) => n > 0));
    const ok = await run(() => api.confirmHandover(h.id, nonzero, photo!, note.trim() || undefined), 'Handover confirmed');
    if (ok) {
      onDone();
      onClose();
    }
  };
  return (
    <Modal
      wide
      title={`Count cash from ${h.from_name}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy || !valid} onClick={submit}>
            Confirm {rupees(counted)}
          </button>
        </>
      }
    >
      <p className="muted">
        Declared {rupees(h.declared_paise)} ({denomText(h.declared_denoms)}); system expected {rupees(h.expected_paise)} in hand. Enter what you counted.
      </p>
      <table className="tbl denom-grid">
        <thead>
          <tr>
            <th>Note / coin</th>
            <th className="num">Declared</th>
            <th>Counted</th>
            <th className="num">Value</th>
          </tr>
        </thead>
        <tbody>
          {DENOMS.map((d) => {
            const k = String(d);
            const diff = (counts[k] ?? 0) !== (h.declared_denoms?.[k] ?? 0);
            return (
              <tr key={d} className={diff ? 'row-warn' : ''}>
                <td>₹{d}</td>
                <td className="num">{h.declared_denoms?.[k] ?? 0}</td>
                <td>
                  <input type="number" min={0} className="num-input" value={counts[k] ?? 0}
                    onChange={(e) => setCounts((c) => ({ ...c, [k]: Math.max(0, parseInt(e.target.value || '0', 10) || 0) }))} />
                </td>
                <td className="num">{rupees(d * 100 * (counts[k] ?? 0))}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <th>Total</th>
            <th className="num">{rupees(h.declared_paise)}</th>
            <th></th>
            <th className="num">{rupees(counted)}</th>
          </tr>
        </tfoot>
      </table>
      <div className={`variance ${variance ? (variance < 0 ? 'tone-text-bad' : 'tone-text-warn') : 'tone-text-ok'}`}>
        Variance {rupees(variance, { signed: true })} {variance ? '— a note is required' : '— matches declaration'}
      </div>
      <Field label="Photo of the counted cash (required)">
        <input type="file" accept="image/*" capture="environment" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
      </Field>
      <Field label={variance ? 'Note (required: explain the variance)' : 'Note (optional)'}>
        <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
    </Modal>
  );
}

function DepositDialog({ defaultDate, suggested, onClose, onDone }: { defaultDate: string; suggested: number; onClose: () => void; onDone: () => void }) {
  const [date, setDate] = useState(defaultDate);
  const [amount, setAmount] = useState(suggested > 0 ? String(suggested / 100) : '');
  const [slip, setSlip] = useState('');
  const [note, setNote] = useState('');
  const [photo, setPhoto] = useState<File | null>(null);
  const { run, busy } = useAction();
  const paise = parseRupees(amount);
  const valid = paise !== null && paise > 0 && slip.trim() && photo;
  const submit = async () => {
    const ok = await run(() => api.recordDeposit({ business_date: date, amount_paise: paise!, slip_ref: slip.trim(), note: note || undefined, photo: photo! }), 'Deposit recorded');
    if (ok) {
      onDone();
      onClose();
    }
  };
  return (
    <Modal
      title="Record bank deposit"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy || !valid} onClick={submit}>
            Record {paise ? rupees(paise) : ''}
          </button>
        </>
      }
    >
      <div className="form-grid">
        <Field label="Business date">
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
        <Field label="Amount (₹)" hint={suggested > 0 ? `handed over but not yet deposited: ${rupees(suggested)}` : undefined}>
          <input value={amount} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} />
        </Field>
        <Field label="Deposit slip reference">
          <input value={slip} onChange={(e) => setSlip(e.target.value)} />
        </Field>
        <Field label="Slip photo (required)">
          <input type="file" accept="image/*,application/pdf" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
        </Field>
      </div>
      <Field label="Note">
        <input value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
    </Modal>
  );
}

function CreditDialog({ d, onClose, onDone }: { d: Deposit; onClose: () => void; onDone: () => void }) {
  const [amount, setAmount] = useState(String((d.bank_credited_paise ?? d.amount_paise) / 100));
  const [note, setNote] = useState('');
  const { run, busy } = useAction();
  const paise = parseRupees(amount);
  const submit = async () => {
    const ok = await run(() => api.bankCredit(d.id, paise!, note || undefined), 'Bank credit recorded');
    if (ok) {
      onDone();
      onClose();
    }
  };
  return (
    <Modal
      title={`Bank credit for deposit ${d.slip_ref}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy || paise === null} onClick={submit}>
            Save
          </button>
        </>
      }
    >
      <p className="muted">Deposited {rupees(d.amount_paise)} for {d.business_date}. Enter the amount the bank statement shows as credited.</p>
      <Field label="Credited (₹)" hint={paise !== null && paise !== d.amount_paise ? `mismatch of ${rupees(paise - d.amount_paise, { signed: true })}` : undefined}>
        <input autoFocus value={amount} onChange={(e) => setAmount(e.target.value)} />
      </Field>
      <Field label="Note">
        <input value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
    </Modal>
  );
}

// ------------------------------------------------------------------ worker accountability
function WorkerSection({ users }: { users: ReturnType<typeof useUsers> }) {
  const [start, setStart] = useState(addDays(istDate(), -6));
  const [end, setEnd] = useState(istDate());
  const cmp = useAsync(() => api.report<WorkerComparison>('worker-comparison', start, end), [start, end]);
  const disp = useAsync(() => api.report<Record<string, unknown>[]>('disputes', start, end), [start, end]);
  const rows = cmp.data?.rows ?? [];
  const avg = cmp.data?.averages ?? {};
  const flagged = rows.filter((r) => r.flags.length);
  return (
    <>
      <div className="section-h">
        <h2>Worker accountability</h2>
        <label className="inline-field">
          From <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="inline-field">
          To <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      <Card title="Worker comparison (per shift)" pad={false}
        actions={<span className="muted small">site average: cash share {pct(avg.cash_share, 0)} · unpaid rate {pct(avg.unpaid_rate, 1)} · disputes {avg.disputes?.toFixed(1) ?? '—'}</span>}>
        <ErrorBox error={cmp.error} onRetry={cmp.reload} />
        {flagged.length > 0 && (
          <div className="flag-banner">
            <strong>{flagged.length} shift{flagged.length > 1 ? 's' : ''} flagged:</strong>{' '}
            {flagged.map((r) => `${r.name} (${r.flags.join(', ')})`).join(' · ')}
          </div>
        )}
        {rows.length === 0 ? (
          <Empty>No shifts in this range</Empty>
        ) : (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Worker</th>
                  <th>Shift</th>
                  <th>Zone</th>
                  <th className="num">Collections</th>
                  <th className="num">UPI</th>
                  <th className="num">Cash</th>
                  <th className="num">Cash share</th>
                  <th className="num">Zone sessions</th>
                  <th className="num">Unpaid in zone</th>
                  <th className="num">Unpaid rate</th>
                  <th className="num">Disputes (upheld/unres.)</th>
                  <th className="num">Handover var.</th>
                  <th className="num">Receipts phone/screen</th>
                  <th>Flags</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r: WorkerRow) => (
                  <tr key={r.shift_id} className={r.flags.length ? 'row-bad' : ''}>
                    <td>{r.name ?? userName(users, r.user_id)}</td>
                    <td className="small">
                      {dateTime(r.opened_at)} → {r.closed_at ? dateTime(r.closed_at) : 'open'}
                    </td>
                    <td>{r.zone_id ?? '—'}</td>
                    <td className="num">{r.collections}</td>
                    <td className="num">{rupees(r.upi_paise)}</td>
                    <td className="num">{rupees(r.cash_paise)}</td>
                    <td className={`num ${avg.cash_share !== undefined && r.cash_share < avg.cash_share * 0.6 ? 'tone-text-warn' : ''}`}>{pct(r.cash_share)}</td>
                    <td className="num">{r.zone_sessions}</td>
                    <td className="num">{r.unpaid_in_zone}</td>
                    <td className={`num ${avg.unpaid_rate !== undefined && r.unpaid_rate > avg.unpaid_rate * 1.25 && r.zone_sessions ? 'tone-text-bad' : ''}`}>{pct(r.unpaid_rate, 1)}</td>
                    <td className="num">
                      {r.disputes_total} ({r.disputes_upheld}/{r.disputes_unresolved})
                    </td>
                    <td className={`num ${r.handover_variance_paise < 0 ? 'tone-text-bad' : ''}`}>{rupees(r.handover_variance_paise, { signed: true })}</td>
                    <td className="num">
                      {r.cash_receipts_to_phone}/{r.cash_receipts_shown}
                    </td>
                    <td>
                      {r.flags.map((f) => (
                        <Badge key={f} tone="bad" title={FLAG_TEXT[f]}>
                          {f.replace(/_/g, ' ')}
                        </Badge>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title="Disputes by worker" pad={false}>
        <ErrorBox error={disp.error} />
        {(disp.data ?? []).length === 0 ? (
          <Empty>No disputes in this range</Empty>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Worker</th>
                <th className="num">Open</th>
                <th className="num">Upheld</th>
                <th className="num">Rejected</th>
                <th className="num">Unresolved</th>
                <th className="num">Total</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(disp.data ?? []).map((d, i) => (
                <tr key={i} className={d.repeat_flag ? 'row-bad' : ''}>
                  <td>{String(d.name)}</td>
                  <td className="num">{String(d.OPEN)}</td>
                  <td className="num">{String(d.UPHELD)}</td>
                  <td className="num">{String(d.REJECTED)}</td>
                  <td className="num">{String(d.UNRESOLVED)}</td>
                  <td className="num">{String(d.total)}</td>
                  <td>{d.repeat_flag ? <Badge tone="bad">REPEATED</Badge> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

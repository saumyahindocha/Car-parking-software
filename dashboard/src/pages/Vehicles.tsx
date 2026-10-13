import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, type Payment, type Vehicle, type VehicleDetail } from '../api';
import { useAuth } from '../auth';
import { dateTime, duration, minutesBetween, normPlate, parseRupees, plate, rupees } from '../format';
import { Badge, Card, Empty, ErrorBox, Field, Loading, Modal, ReasonDialog, Stat, StatusBadge, Tabs, Thumb, useAction, useAsync } from '../ui';
import { userName, useUsers } from '../users';

export default function VehiclesPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const [text, setText] = useState(q);
  const nav = useNavigate();
  const results = useAsync(() => (q ? api.searchVehicles(q) : Promise.resolve([] as Vehicle[])), [q]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const n = normPlate(text);
    if (!n) return;
    setParams({ q: n });
    if (id) nav(`/vehicles?q=${n}`);
  };
  useEffect(() => {
    if (!id && results.data && results.data.filter((v) => v.exact).length === 1) nav(`/vehicles/${results.data.find((v) => v.exact)!.id}?q=${q}`, { replace: true });
  }, [results.data, id, nav, q]);

  return (
    <div className="page">
      <div className="page-h">
        <h1>Vehicle ledger</h1>
        <form className="search" onSubmit={submit}>
          <input autoFocus={!id} className="plate-input" placeholder="Search plate (approximate) e.g. MH43AB1234" value={text}
            onChange={(e) => setText(e.target.value.toUpperCase())} />
          <button className="btn btn-primary" type="submit">
            Search
          </button>
        </form>
      </div>
      {q && !id && (
        <Card title={`Matches for ${q}`} pad={false}>
          <ErrorBox error={results.error} />
          {results.loading ? (
            <Loading />
          ) : (results.data ?? []).length === 0 ? (
            <Empty>No vehicle within the matching tolerance</Empty>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Plate</th>
                  <th>Match</th>
                  <th>Class</th>
                  <th className="num">Balance</th>
                  <th>Pass</th>
                  <th>Inside now</th>
                  <th>Last seen</th>
                  <th>Phone</th>
                </tr>
              </thead>
              <tbody>
                {(results.data ?? []).map((v) => (
                  <tr key={v.id} className="clickable" onClick={() => nav(`/vehicles/${v.id}?q=${q}`)}>
                    <td className="plate">{plate(v.plate)}</td>
                    <td>{v.exact ? <Badge tone="ok">exact</Badge> : <Badge tone="warn">distance {v.distance}</Badge>}</td>
                    <td>{v.vehicle_class}</td>
                    <td className={`num ${v.balance_paise > 0 ? 'tone-text-bad' : ''}`}>{rupees(v.balance_paise)}</td>
                    <td>{v.pass ? `till ${dateTime(v.pass.ends_at)}` : '—'}</td>
                    <td>{v.open_session ? <StatusBadge status={v.open_session.status} /> : '—'}</td>
                    <td>{dateTime(v.last_seen)}</td>
                    <td>{v.phone ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
      {id && q && (results.data ?? []).length > 1 && (
        <div className="chips">
          <span className="muted small">Similar plates for {q}:</span>
          {(results.data ?? []).map((v) => (
            <button key={v.id} className={`chip chip-btn ${String(v.id) === id ? 'active' : ''}`} onClick={() => nav(`/vehicles/${v.id}?q=${q}`)}>
              <span className="plate">{plate(v.plate)}</span> <span className="muted small">{v.exact ? 'exact' : `d=${v.distance}`}</span>
            </button>
          ))}
        </div>
      )}
      {id ? <VehicleView id={Number(id)} /> : !q && <Empty>Search a plate to see its sessions, payments, ledger and passes.</Empty>}
    </div>
  );
}

type DTab = 'sessions' | 'payments' | 'ledger' | 'passes';

function VehicleView({ id }: { id: number }) {
  const { data: v, error, loading, reload } = useAsync(() => api.vehicle(id), [id]);
  const [tab, setTab] = useState<DTab>('sessions');
  const [adjusting, setAdjusting] = useState(false);
  const [contact, setContact] = useState(false);
  const [moneyAction, setMoneyAction] = useState<{ p: Payment; kind: 'reverse' | 'refund' } | null>(null);
  const { isAdmin } = useAuth();
  const nav = useNavigate();
  const { run } = useAction();
  const users = useUsers();

  const ledger = useMemo(() => {
    if (!v) return [];
    // entries are newest first; walk back from the current total so the running balance is right even when truncated
    let bal = v.ledger_balance_paise;
    return v.ledger.map((e) => {
      const row = { ...e, balance_after: bal };
      bal -= e.amount_paise;
      return row;
    });
  }, [v]);

  if (!v) return error ? <ErrorBox error={error} onRetry={reload} /> : loading ? <Loading /> : null;
  const os = v.open_session;
  return (
    <>
      <div className="vehicle-head">
        <div>
          <div className="plate plate-xl">{plate(v.plate)}</div>
          <div className="muted">
            {v.vehicle_class} · first seen {dateTime(v.first_seen)} · last seen {dateTime(v.last_seen)}
          </div>
          <div className="small">
            {v.name && <strong>{v.name} · </strong>}
            {v.phone ?? <span className="muted">no phone</span>}{' '}
            <button className="link-btn" onClick={() => setContact(true)}>
              edit contact
            </button>
            {v.notes && <span className="muted"> · {v.notes}</span>}
          </div>
          <div className="badges">
            {v.pass && <Badge tone="info">Pass till {dateTime(v.pass.ends_at)}</Badge>}
            {v.pass_candidate && <Badge tone="warn">Pass candidate</Badge>}
            {os && <Badge tone="warn">Inside since {dateTime(os.entry_at)} ({duration(minutesBetween(os.entry_at))})</Badge>}
          </div>
        </div>
        <div className="stats stats-compact">
          <Stat label="Balance" value={rupees(v.balance_paise)} sub={v.balance_paise > 0 ? 'customer owes' : v.balance_paise < 0 ? 'credit' : 'settled'}
            tone={v.balance_paise > 0 ? 'bad' : 'ok'} />
          {v.pending_claims_paise > 0 && <Stat label="Pending UPI claims" value={rupees(v.pending_claims_paise)} tone="warn" />}
          {v.ledger_balance_paise !== v.balance_paise && <Stat label="Ledger sum" value={rupees(v.ledger_balance_paise)} tone="bad" sub="differs from balance" />}
        </div>
        <div className="btn-col">
          <button className="btn btn-primary" onClick={() => setAdjusting(true)}>
            Adjust balance
          </button>
          {isAdmin && (
            <button className="btn" onClick={() => nav(`/privacy?vehicle=${v.id}`)}>
              Privacy export / erase
            </button>
          )}
        </div>
      </div>

      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'sessions', label: 'Sessions', count: v.sessions.length },
          { id: 'payments', label: 'Payments', count: v.payments.length },
          { id: 'ledger', label: 'Ledger', count: v.ledger.length },
          { id: 'passes', label: 'Passes', count: v.passes.length },
        ]}
      />
      <Card pad={false}>
        {tab === 'sessions' &&
          (v.sessions.length === 0 ? (
            <Empty>No sessions</Empty>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Status</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>Duration</th>
                  <th className="num">Charge</th>
                  <th>Match (in/out)</th>
                  <th>Zone</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {v.sessions.map((s) => (
                  <tr key={s.id} className={s.unpaid_flagged && s.status === 'OPEN' ? 'row-bad' : ''}>
                    <td>{s.id}</td>
                    <td>
                      <StatusBadge status={s.status} />
                    </td>
                    <td>
                      {dateTime(s.entry_at)} <span className="muted small">{s.entry_gate}</span>
                    </td>
                    <td>
                      {dateTime(s.exit_at)} <span className="muted small">{s.exit_gate}</span>
                    </td>
                    <td>{s.entry_at ? duration(minutesBetween(s.entry_at, s.exit_at ?? new Date())) : '—'}</td>
                    <td className="num">{rupees(s.charge_paise)}</td>
                    <td className="small">
                      {s.entry_match ?? '—'} / {s.exit_match ?? '—'}
                    </td>
                    <td>{s.zone_id ?? '—'}</td>
                    <td className="small">{s.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
        {tab === 'payments' &&
          (v.payments.length === 0 ? (
            <Empty>No payments</Empty>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Created</th>
                  <th>Mode</th>
                  <th>Purpose</th>
                  <th className="num">Amount</th>
                  <th className="num">Dues incl.</th>
                  <th>Status</th>
                  <th>Ref / UTR</th>
                  <th>Collected by</th>
                  <th>Receipt</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {v.payments.map((p) => (
                  <tr key={p.id} className={p.status === 'REVERSED' || p.status === 'FAILED' ? 'row-muted' : ''}>
                    <td>{p.id}</td>
                    <td>{dateTime(p.created_at)}</td>
                    <td>
                      <Badge tone={p.mode === 'CASH' ? 'warn' : 'info'}>{p.mode}</Badge>
                      {p.offline && <span className="muted small"> offline</span>}
                    </td>
                    <td>
                      {p.purpose}
                      {p.duration_minutes ? <span className="muted small"> {duration(p.duration_minutes)}</span> : null}
                    </td>
                    <td className="num">{rupees(p.amount_paise)}</td>
                    <td className="num">{p.dues_paise ? rupees(p.dues_paise) : '—'}</td>
                    <td>
                      <StatusBadge status={p.status} />
                      {p.status_note && <div className="muted small">{p.status_note}</div>}
                    </td>
                    <td className="mono small">
                      {p.txn_ref}
                      {p.utr && <div>UTR {p.utr}</div>}
                    </td>
                    <td>{p.channel === 'WORKER' || p.collected_by ? userName(users, p.collected_by) : p.channel}</td>
                    <td className="small">
                      {p.receipt ? (
                        <a href={`/r/${p.receipt.code}`} target="_blank" rel="noreferrer">
                          {p.receipt.number}
                        </a>
                      ) : (
                        '—'
                      )}
                      {p.receipt && <div className="muted">{p.receipt.channel ?? ''} {p.receipt.delivery_status}</div>}
                    </td>
                    <td className="actions">
                      {p.status === 'CONFIRMED' && p.mode === 'CASH' && (
                        <button className="btn btn-sm btn-danger-outline" onClick={() => setMoneyAction({ p, kind: 'reverse' })}>
                          Reverse cash
                        </button>
                      )}
                      {p.status === 'CONFIRMED' && p.mode === 'UPI' && (
                        <button className="btn btn-sm btn-danger-outline" onClick={() => setMoneyAction({ p, kind: 'refund' })}>
                          Refund UPI
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
        {tab === 'ledger' &&
          (ledger.length === 0 ? (
            <Empty>No ledger entries</Empty>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Kind</th>
                  <th className="num">Amount</th>
                  <th className="num">Balance after</th>
                  <th>Session</th>
                  <th>Payment</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {ledger.map((e) => (
                  <tr key={e.id}>
                    <td>{dateTime(e.created_at)}</td>
                    <td>
                      <Badge tone={e.kind === 'CHARGE' ? 'warn' : e.kind === 'ADJUSTMENT' ? 'bad' : 'ok'}>{e.kind}</Badge>
                    </td>
                    <td className="num">{rupees(e.amount_paise, { signed: true })}</td>
                    <td className={`num ${e.balance_after > 0 ? 'tone-text-bad' : ''}`}>{rupees(e.balance_after)}</td>
                    <td>{e.session_id ?? '—'}</td>
                    <td>{e.payment_id ?? '—'}</td>
                    <td className="small">{e.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
        {tab === 'passes' &&
          (v.passes.length === 0 ? (
            <Empty>No passes</Empty>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Type</th>
                  <th>From</th>
                  <th>To</th>
                  <th className="num">Amount</th>
                  <th>Status</th>
                  <th>Channel</th>
                </tr>
              </thead>
              <tbody>
                {v.passes.map((p) => (
                  <tr key={p.id}>
                    <td>{p.id}</td>
                    <td>{p.pass_type}</td>
                    <td>{p.starts_on}</td>
                    <td>{p.ends_on}</td>
                    <td className="num">{rupees(p.amount_paise)}</td>
                    <td>
                      <StatusBadge status={p.status} />
                    </td>
                    <td>{p.channel}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </Card>

      {os && (os.entry_images?.plate_crop || os.entry_images?.full_frame) && (
        <Card title="Current session images">
          <div className="img-row">
            <Thumb src={os.entry_images?.plate_crop} alt="entry plate" big />
            <Thumb src={os.entry_images?.full_frame} alt="entry frame" big />
            <Thumb src={os.entry_images?.overview} alt="entry overview" big />
          </div>
        </Card>
      )}

      {adjusting && <AdjustDialog v={v} onClose={() => setAdjusting(false)} onDone={reload} />}
      {contact && <ContactDialog v={v} onClose={() => setContact(false)} onDone={reload} />}
      {moneyAction && (
        <ReasonDialog
          danger
          title={
            moneyAction.kind === 'reverse'
              ? `Reverse cash payment #${moneyAction.p.id} (${rupees(moneyAction.p.amount_paise)})`
              : `Refund UPI payment #${moneyAction.p.id} (${rupees(moneyAction.p.amount_paise)}) through the gateway`
          }
          extra={<p className="muted">This is reported in the daily overrides report and the audit log. The amount goes back onto the vehicle balance.</p>}
          confirmLabel={moneyAction.kind === 'reverse' ? 'Reverse' : 'Refund'}
          onClose={() => setMoneyAction(null)}
          onSubmit={(reason) =>
            run(
              () => (moneyAction.kind === 'reverse' ? api.reversePayment(moneyAction.p.id, reason) : api.refundPayment(moneyAction.p.id, reason)),
              moneyAction.kind === 'reverse' ? 'Cash payment reversed' : 'Refund initiated',
            ).then((ok) => {
              if (ok) void reload();
              return ok;
            })
          }
        />
      )}
    </>
  );
}

function AdjustDialog({ v, onClose, onDone }: { v: VehicleDetail; onClose: () => void; onDone: () => void }) {
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const { run, busy } = useAction();
  const paise = parseRupees(amount);
  const submit = async () => {
    const ok = await run(() => api.adjust(v.id, paise!, reason.trim()), 'Adjustment posted');
    if (ok) {
      onDone();
      onClose();
    }
  };
  return (
    <Modal
      title={`Balance adjustment · ${plate(v.plate)}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy || !paise || !reason.trim()} onClick={submit}>
            Post adjustment
          </button>
        </>
      }
    >
      <p className="muted">
        Current balance {rupees(v.balance_paise)}. Adjustments are ledger entries — nothing is edited. Positive adds dues, negative gives credit.
      </p>
      <Field label="Amount (₹, e.g. -10 to waive ₹10)" hint={paise ? `new balance ${rupees(v.balance_paise + paise)}` : undefined}>
        <input autoFocus value={amount} onChange={(e) => setAmount(e.target.value)} />
      </Field>
      <Field label="Reason (mandatory)">
        <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
    </Modal>
  );
}

function ContactDialog({ v, onClose, onDone }: { v: VehicleDetail; onClose: () => void; onDone: () => void }) {
  const [phone, setPhone] = useState(v.phone ?? '');
  const [name, setName] = useState(v.name ?? '');
  const { run, busy } = useAction();
  const digits = phone.replace(/\D/g, '').slice(-10);
  const submit = async () => {
    const ok = await run(() => api.setContact(v.id, digits, name.trim() || undefined), 'Contact saved');
    if (ok) {
      onDone();
      onClose();
    }
  };
  return (
    <Modal
      title={`Contact · ${plate(v.plate)}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy || digits.length !== 10} onClick={submit}>
            Save
          </button>
        </>
      }
    >
      <Field label="Mobile (10 digits)">
        <input autoFocus inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
      </Field>
      <Field label="Name (optional)">
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
    </Modal>
  );
}

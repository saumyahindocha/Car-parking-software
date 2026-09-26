import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api, type AnprEvent, type Candidate, type Dispute, type ParkingSession, type Payment, type ReviewQueue } from '../api';
import { ago, dateTime, duration, isoToIstLocal, istLocalToIso, minutesBetween, normPlate, parseRupees, pct, plate, rupees, time } from '../format';
import { useSite } from '../site';
import { Badge, Empty, ErrorBox, Field, Kbd, Loading, Modal, StatusBadge, Tabs, Thumb, useAction, useAsync } from '../ui';
import { useLiveRefresh } from '../useLive';
import { userName, useUsers } from '../users';

type TabId = 'events' | 'orphans' | 'claims' | 'disputes' | 'unpaid';
const TAB_ORDER: TabId[] = ['events', 'orphans', 'claims', 'disputes', 'unpaid'];

function isTyping(el: Element | null): boolean {
  if (!el) return false;
  const t = el.tagName;
  return t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT' || (el as HTMLElement).isContentEditable;
}

export default function ReviewPage() {
  const [params, setParams] = useSearchParams();
  const tab = (TAB_ORDER.includes(params.get('tab') as TabId) ? params.get('tab') : 'events') as TabId;
  const setTab = (t: TabId) => setParams({ tab: t }, { replace: true });
  const { data, error, loading, reload, setData } = useAsync(() => api.review(), []);
  const { refresh: refreshSite } = useSite();
  useLiveRefresh(['review.new', 'dispute.new', 'payment.updated'], reload, 2000);
  const [sel, setSel] = useState(0);
  const [help, setHelp] = useState(false);
  const [dialog, setDialog] = useState<null | { kind: 'orphan' | 'claim' | 'dispute'; action?: string; item: ParkingSession | Payment | Dispute }>(null);
  const nav = useNavigate();

  const lists = useMemo(
    () => ({
      events: data?.events ?? [],
      orphans: data?.orphans ?? [],
      claims: [...(data?.offline_claims ?? [])].sort((a, b) => Number(!!b.stale) - Number(!!a.stale)),
      disputes: data?.disputes ?? [],
      unpaid: data?.unpaid_flagged ?? [],
    }),
    [data],
  );
  const current = lists[tab] as unknown[];
  useEffect(() => setSel(0), [tab]);
  useEffect(() => {
    if (sel >= current.length && current.length) setSel(current.length - 1);
  }, [current.length, sel]);

  /** Remove an item locally (instant feedback), then refetch. */
  const done = useCallback(
    (key: keyof ReviewQueue, id: string | number) => {
      setData((d) => (d ? { ...d, [key]: (d[key] as { id: string | number }[]).filter((x) => x.id !== id) } : d));
      setTimeout(() => {
        void reload();
        refreshSite();
      }, 300);
    },
    [reload, setData, refreshSite],
  );

  const evRef = useRef<EventPanelHandle | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (dialog || e.ctrlKey || e.metaKey || e.altKey) return;
      if (isTyping(document.activeElement)) {
        if (e.key === 'Escape') (document.activeElement as HTMLElement).blur();
        return;
      }
      const k = e.key;
      if (k === '?') return setHelp((h) => !h);
      if (k === 'j' || k === 'ArrowDown') {
        e.preventDefault();
        return setSel((s) => Math.min(s + 1, Math.max(0, current.length - 1)));
      }
      if (k === 'k' || k === 'ArrowUp') {
        e.preventDefault();
        return setSel((s) => Math.max(0, s - 1));
      }
      if (k === ']' || k === '[') {
        const i = TAB_ORDER.indexOf(tab) + (k === ']' ? 1 : -1);
        return setTab(TAB_ORDER[(i + TAB_ORDER.length) % TAB_ORDER.length]);
      }
      if (k === 'r') return void reload();
      const item = current[sel];
      if (!item) return;
      if (tab === 'events') {
        if (k === '/') {
          e.preventDefault();
          return evRef.current?.focusPlate();
        }
        if (k === 'Enter') return evRef.current?.acceptTop();
        if (k === 'd') return evRef.current?.discard();
        if (k === 'c') return evRef.current?.confirmCorrection();
        if (/^[1-9]$/.test(k)) return evRef.current?.pick(Number(k) - 1);
      } else if (tab === 'orphans') {
        if (k === 'Enter') setDialog({ kind: 'orphan', action: 'CHARGE', item: item as ParkingSession });
        if (k === 'w') setDialog({ kind: 'orphan', action: 'WAIVE', item: item as ParkingSession });
      } else if (tab === 'claims') {
        if (k === 'Enter') setDialog({ kind: 'claim', action: 'CONFIRM', item: item as Payment });
        if (k === 'f') setDialog({ kind: 'claim', action: 'FAIL', item: item as Payment });
      } else if (tab === 'disputes') {
        if (k === 'Enter') setDialog({ kind: 'dispute', item: item as Dispute });
      } else if (tab === 'unpaid') {
        if (k === 'Enter') nav(`/vehicles/${(item as ParkingSession).vehicle_id}`);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, current, sel, dialog]);

  const c = data?.counts;
  const staleCount = lists.claims.filter((p) => p.stale).length;
  return (
    <div className="page">
      <div className="page-h">
        <h1>Review queue</h1>
        <button className="btn btn-sm" onClick={() => setHelp((h) => !h)}>
          Keyboard shortcuts <Kbd>?</Kbd>
        </button>
        <button className="btn btn-sm" onClick={() => void reload()} disabled={loading}>
          Refresh
        </button>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'events', label: 'Unread / review events', count: lists.events.length, tone: 'bad' },
          { id: 'orphans', label: 'Orphan sessions', count: lists.orphans.length },
          { id: 'claims', label: `Offline UPI claims${staleCount ? ` (${staleCount} stale)` : ''}`, count: lists.claims.length, tone: staleCount ? 'bad' : 'warn' },
          { id: 'disputes', label: 'Open disputes', count: c?.disputes ?? lists.disputes.length },
          { id: 'unpaid', label: 'Unpaid > 30 min', count: lists.unpaid.length, tone: 'bad' },
        ]}
      />
      {help && <CheatSheet onClose={() => setHelp(false)} />}
      {!data && loading ? (
        <Loading />
      ) : tab === 'events' ? (
        <EventsTab events={lists.events} sel={sel} setSel={setSel} panelRef={evRef} onDone={(id) => done('events', id)} />
      ) : tab === 'orphans' ? (
        <OrphansTab rows={lists.orphans} sel={sel} setSel={setSel} open={(item, action) => setDialog({ kind: 'orphan', action, item })} />
      ) : tab === 'claims' ? (
        <ClaimsTab rows={lists.claims} sel={sel} setSel={setSel} open={(item, action) => setDialog({ kind: 'claim', action, item })} />
      ) : tab === 'disputes' ? (
        <DisputesTab rows={lists.disputes} sel={sel} setSel={setSel} open={(item) => setDialog({ kind: 'dispute', item })} />
      ) : (
        <UnpaidTab rows={lists.unpaid} sel={sel} setSel={setSel} />
      )}

      {dialog?.kind === 'orphan' && (
        <OrphanDialog s={dialog.item as ParkingSession} action={dialog.action as 'CHARGE' | 'WAIVE'} onClose={() => setDialog(null)}
          onDone={() => done('orphans', (dialog.item as ParkingSession).id)} />
      )}
      {dialog?.kind === 'claim' && (
        <ClaimDialog p={dialog.item as Payment} action={dialog.action as 'CONFIRM' | 'FAIL'} onClose={() => setDialog(null)}
          onDone={() => done('offline_claims', (dialog.item as Payment).id)} />
      )}
      {dialog?.kind === 'dispute' && (
        <DisputeDialog d={dialog.item as Dispute} onClose={() => setDialog(null)} onDone={() => done('disputes', (dialog.item as Dispute).id)} />
      )}
    </div>
  );
}

function CheatSheet({ onClose }: { onClose: () => void }) {
  const rows: [string, string][] = [
    ['j / ↓', 'next item'],
    ['k / ↑', 'previous item'],
    ['[ / ]', 'previous / next tab'],
    ['Enter', 'events: accept top (or typed) plate · others: primary action'],
    ['1 – 9', 'events: pick candidate N'],
    ['/', 'events: focus the plate input (Enter submits, Esc leaves)'],
    ['d', 'events: discard (not a vehicle / duplicate)'],
    ['c', 'events: confirm a worker correction'],
    ['w', 'orphans: waive'],
    ['f', 'offline claims: mark failed'],
    ['r', 'reload queue'],
    ['?', 'toggle this help'],
  ];
  return (
    <div className="cheatsheet">
      <div className="cheatsheet-h">
        <strong>Keyboard shortcuts</strong>
        <button className="icon-btn" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      <div className="cheatsheet-grid">
        {rows.map(([k, v]) => (
          <div key={k}>
            <Kbd>{k}</Kbd> <span>{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ events
interface EventPanelHandle {
  focusPlate: () => void;
  acceptTop: () => void;
  discard: () => void;
  confirmCorrection: () => void;
  pick: (i: number) => void;
}

function EventsTab({ events, sel, setSel, panelRef, onDone }: {
  events: AnprEvent[];
  sel: number;
  setSel: (i: number) => void;
  panelRef: React.MutableRefObject<EventPanelHandle | null>;
  onDone: (id: string) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    listRef.current?.querySelector('.q-item.selected')?.scrollIntoView({ block: 'nearest' });
  }, [sel]);
  if (!events.length) return <Empty>Nothing to review. Unread and unmatched events appear here as they happen.</Empty>;
  const ev = events[Math.min(sel, events.length - 1)];
  return (
    <div className="review-split">
      <div className="q-list" ref={listRef}>
        {events.map((e, i) => (
          <div key={e.id} className={`q-item ${i === sel ? 'selected' : ''}`} onClick={() => setSel(i)}>
            <Thumb src={e.images.plate_crop} alt="plate" />
            <div className="q-item-b">
              <div className="plate">{e.plate ? plate(e.plate) : <span className="muted">unread</span>}</div>
              <div className="small muted">
                {time(e.ts)} · {e.gate_id} {e.direction}
              </div>
              <div>
                <StatusBadge status={e.status} /> {e.review_reason && e.review_reason !== e.status && <Badge>{e.review_reason.replace(/_/g, ' ')}</Badge>}
              </div>
            </div>
          </div>
        ))}
      </div>
      <EventPanel key={ev.id} ev={ev} handleRef={panelRef} onDone={() => onDone(ev.id)} />
    </div>
  );
}

function EventPanel({ ev, handleRef, onDone }: { ev: AnprEvent; handleRef: React.MutableRefObject<EventPanelHandle | null>; onDone: () => void }) {
  const [typed, setTyped] = useState('');
  const [note, setNote] = useState('');
  const [picked, setPicked] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const { run, busy } = useAction();
  const isCorrection = ev.review_reason === 'WORKER_CORRECTED' && ev.status !== 'UNREAD' && ev.status !== 'REVIEW';

  // de-duplicated candidates: session candidates (AMBIGUOUS) first, then OCR candidates
  const cands = useMemo(() => {
    const seen = new Set<string>();
    const out: Candidate[] = [];
    const all = [...(ev.candidates ?? [])].sort((a, b) => Number(!!b.session_id) - Number(!!a.session_id));
    for (const c of all) {
      const k = `${c.plate}|${c.session_id ?? ''}`;
      if (!c.plate || seen.has(k)) continue;
      seen.add(k);
      out.push(c);
    }
    return out;
  }, [ev]);

  const resolve = async (body: { plate?: string; session_id?: number; discard?: boolean; confirm?: boolean }, msg: string) => {
    if (busy) return;
    const ok = await run(() => api.resolveEvent(ev.id, { ...body, note: note || undefined }), msg);
    if (ok) onDone();
  };
  const accept = (c: Candidate) =>
    resolve(c.session_id ? { session_id: c.session_id } : { plate: c.plate }, `Resolved as ${plate(c.plate)}`);
  const acceptTyped = () => {
    const p = normPlate(typed);
    if (p.length < 6) return;
    void resolve({ plate: p }, `Resolved as ${plate(p)}`);
  };

  handleRef.current = {
    focusPlate: () => inputRef.current?.focus(),
    acceptTop: () => {
      if (isCorrection) return void resolve({ confirm: true }, 'Correction confirmed');
      if (normPlate(typed).length >= 6) return acceptTyped();
      const c = cands[picked ?? 0];
      if (c) void accept(c);
      else inputRef.current?.focus();
    },
    discard: () => {
      if (!isCorrection) void resolve({ discard: true }, 'Event discarded');
    },
    confirmCorrection: () => {
      if (isCorrection) void resolve({ confirm: true }, 'Correction confirmed');
    },
    pick: (i) => {
      if (cands[i]) {
        setPicked(i);
        setTyped(cands[i].plate);
      }
    },
  };

  const imgs = ev.images ?? {};
  return (
    <div className="q-detail">
      <div className="q-detail-h">
        <div>
          <span className="plate plate-lg">{ev.plate ? plate(ev.plate) : 'UNREAD'}</span>{' '}
          <StatusBadge status={ev.status} /> {ev.review_reason && ev.review_reason !== ev.status && <Badge tone="warn">{ev.review_reason.replace(/_/g, ' ')}</Badge>}
        </div>
        <div className="muted small">
          {dateTime(ev.ts, true)} · gate {ev.gate_id} · {ev.direction} · cameras {ev.camera_ids.join(', ') || '—'} · {ev.vehicle_class}
          {ev.confidence ? ` · conf ${pct(ev.confidence)}` : ''} {ev.raw_plate && ev.raw_plate !== ev.plate ? ` · raw ${ev.raw_plate}` : ''}
        </div>
      </div>
      <div className="img-row">
        <figure>
          <Thumb src={imgs.plate_crop} alt="plate crop" big />
          <figcaption>Plate crop</figcaption>
        </figure>
        <figure>
          <Thumb src={imgs.full_frame} alt="full frame" big />
          <figcaption>Full frame</figcaption>
        </figure>
        <figure>
          <Thumb src={imgs.overview} alt="overview" big />
          <figcaption>Overview</figcaption>
        </figure>
        {ev.extra_images.map((x, i) => (
          <figure key={x}>
            <Thumb src={x} alt={`extra ${i + 1}`} big />
            <figcaption>Other camera {i + 1}</figcaption>
          </figure>
        ))}
      </div>

      {isCorrection ? (
        <div className="q-actions">
          <p>
            A worker corrected this read from <strong className="plate">{plate(ev.raw_plate)}</strong> to{' '}
            <strong className="plate">{plate(ev.matched_plate)}</strong>. Check the image and confirm.
          </p>
          <Field label="Note (optional)">
            <input value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <button className="btn btn-primary" disabled={busy} onClick={() => resolve({ confirm: true }, 'Correction confirmed')}>
            Confirm correction <Kbd>c</Kbd>
          </button>
        </div>
      ) : (
        <div className="q-actions">
          <div className="cands">
            <div className="muted small">Candidates</div>
            {cands.length === 0 && <div className="muted">No candidates — type the plate from the image.</div>}
            {cands.map((c, i) => (
              <button key={`${c.plate}-${c.session_id ?? i}`} className={`cand ${picked === i ? 'picked' : ''}`} disabled={busy} onClick={() => accept(c)}>
                <Kbd>{i + 1}</Kbd>
                <span className="plate">{plate(c.plate)}</span>
                {c.confidence !== undefined && <span className="muted small">{pct(c.confidence)}</span>}
                {c.session_id && <Badge tone="info">open session #{c.session_id}{c.distance !== undefined ? ` · d=${c.distance}` : ''}</Badge>}
              </button>
            ))}
          </div>
          <div className="plate-entry">
            <Field label={<>Type plate <Kbd>/</Kbd></>}>
              <input
                ref={inputRef}
                className="plate-input"
                value={typed}
                placeholder="MH43AB1234"
                onChange={(e) => {
                  setTyped(e.target.value.toUpperCase());
                  setPicked(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    acceptTyped();
                  }
                }}
              />
            </Field>
            <Field label="Note (optional)">
              <input value={note} onChange={(e) => setNote(e.target.value)} />
            </Field>
            <div className="btn-row">
              <button className="btn btn-primary" disabled={busy || normPlate(typed).length < 6} onClick={acceptTyped}>
                Use typed plate <Kbd>Enter</Kbd>
              </button>
              <button className="btn btn-danger-outline" disabled={busy} onClick={() => resolve({ discard: true }, 'Event discarded')}>
                Discard <Kbd>d</Kbd>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ orphans
function OrphansTab({ rows, sel, setSel, open }: { rows: ParkingSession[]; sel: number; setSel: (i: number) => void; open: (s: ParkingSession, a: string) => void }) {
  if (!rows.length) return <Empty>No orphan sessions.</Empty>;
  return (
    <div className="table-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th>Image</th>
            <th>Plate</th>
            <th>Type</th>
            <th>Entry</th>
            <th>Exit</th>
            <th>Note</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s, i) => (
            <tr key={s.id} className={i === sel ? 'selected' : ''} onClick={() => setSel(i)}>
              <td>
                <Thumb src={(s.status === 'ORPHAN_EXIT' ? s.exit_images : s.entry_images)?.plate_crop} alt="plate" />
              </td>
              <td className="plate">{plate(s.plate)}</td>
              <td>
                <StatusBadge status={s.status} />
                <div className="muted small">{s.status === 'ORPHAN_ENTRY' ? 'entered again without an exit' : 'exit with no matching entry'}</div>
              </td>
              <td>
                {dateTime(s.entry_at)} {s.entry_gate && <span className="muted">{s.entry_gate}</span>}
              </td>
              <td>
                {dateTime(s.exit_at)} {s.exit_gate && <span className="muted">{s.exit_gate}</span>}
              </td>
              <td className="small">{s.note ?? ''}</td>
              <td className="actions">
                <button className="btn btn-sm btn-primary" onClick={() => open(s, 'CHARGE')}>
                  Charge
                </button>
                <button className="btn btn-sm" onClick={() => open(s, 'WAIVE')}>
                  Waive
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OrphanDialog({ s, action, onClose, onDone }: { s: ParkingSession; action: 'CHARGE' | 'WAIVE'; onClose: () => void; onDone: () => void }) {
  const isEntry = s.status === 'ORPHAN_ENTRY';
  const base = isEntry ? s.entry_at : s.exit_at;
  const guess = base ? new Date(new Date(base).getTime() + (isEntry ? 1 : -1) * 4 * 3600 * 1000) : new Date();
  const [at, setAt] = useState(isoToIstLocal(guess));
  const [note, setNote] = useState('');
  const { run, busy } = useAction();
  const submit = async () => {
    const ok = await run(
      () => api.resolveOrphan(s.id, { action, note, at: action === 'CHARGE' ? istLocalToIso(at) : undefined }),
      action === 'CHARGE' ? 'Session charged' : 'Session waived',
    );
    if (ok) {
      onDone();
      onClose();
    }
  };
  const mins = action === 'CHARGE' ? minutesBetween(isEntry ? s.entry_at : istLocalToIso(at), isEntry ? istLocalToIso(at) : s.exit_at) : null;
  return (
    <Modal
      title={`${action === 'CHARGE' ? 'Charge' : 'Waive'} orphan session · ${plate(s.plate)}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy || !note.trim()} onClick={submit}>
            {action === 'CHARGE' ? 'Charge' : 'Waive'}
          </button>
        </>
      }
    >
      <p className="muted">
        {isEntry ? `Entered ${dateTime(s.entry_at)} and never seen leaving.` : `Left ${dateTime(s.exit_at)} with no recorded entry.`}
      </p>
      {action === 'CHARGE' && (
        <Field label={isEntry ? 'Estimated exit time (IST)' : 'Estimated entry time (IST)'} hint={mins !== null ? `≈ ${duration(mins)} parked` : undefined}>
          <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
        </Field>
      )}
      <Field label="Note (mandatory)">
        <textarea rows={3} autoFocus value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
    </Modal>
  );
}

// ------------------------------------------------------------------ offline claims
function ClaimsTab({ rows, sel, setSel, open }: { rows: Payment[]; sel: number; setSel: (i: number) => void; open: (p: Payment, a: string) => void }) {
  const users = useUsers();
  if (!rows.length) return <Empty>No unconfirmed offline UPI claims.</Empty>;
  return (
    <div className="table-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th>Claimed</th>
            <th>Plate</th>
            <th className="num">Amount</th>
            <th>Txn ref</th>
            <th>Purpose</th>
            <th>Collected by</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p, i) => (
            <tr key={p.id} className={`${p.stale ? 'row-bad' : ''} ${i === sel ? 'selected' : ''}`} onClick={() => setSel(i)}>
              <td>
                {dateTime(p.created_at)} <span className="muted small">{ago(p.created_at)}</span>
                {p.stale && (
                  <div>
                    <Badge tone="bad">STALE &gt; 24 h</Badge>
                  </div>
                )}
              </td>
              <td className="plate">{plate(p.plate)}</td>
              <td className="num">{rupees(p.amount_paise)}</td>
              <td className="mono">{p.txn_ref}</td>
              <td>{p.purpose}</td>
              <td>{userName(users, p.collected_by)}</td>
              <td className="actions">
                <button className="btn btn-sm btn-primary" onClick={() => open(p, 'CONFIRM')}>
                  Confirm with UTR
                </button>
                <button className="btn btn-sm btn-danger-outline" onClick={() => open(p, 'FAIL')}>
                  Not received
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ClaimDialog({ p, action, onClose, onDone }: { p: Payment; action: 'CONFIRM' | 'FAIL'; onClose: () => void; onDone: () => void }) {
  const [utr, setUtr] = useState('');
  const [note, setNote] = useState('');
  const { run, busy } = useAction();
  const valid = note.trim() && (action === 'FAIL' || utr.trim());
  const submit = async () => {
    const ok = await run(() => api.resolveClaim(p.id, { action, utr: utr.trim() || undefined, note }), action === 'CONFIRM' ? 'Payment confirmed' : 'Marked as not received');
    if (ok) {
      onDone();
      onClose();
    }
  };
  return (
    <Modal
      title={`${action === 'CONFIRM' ? 'Confirm' : 'Fail'} offline UPI claim · ${rupees(p.amount_paise)} · ${plate(p.plate)}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className={`btn ${action === 'CONFIRM' ? 'btn-primary' : 'btn-danger'}`} disabled={busy || !valid} onClick={submit}>
            {action === 'CONFIRM' ? 'Confirm payment' : 'Mark failed'}
          </button>
        </>
      }
    >
      <p className="muted">
        Txn ref <span className="mono">{p.txn_ref}</span>, claimed {dateTime(p.created_at)}. {action === 'FAIL' && 'The amount goes back onto the vehicle balance.'}
      </p>
      {action === 'CONFIRM' && (
        <Field label="UTR / bank reference (from the bank statement)">
          <input autoFocus value={utr} onChange={(e) => setUtr(e.target.value)} />
        </Field>
      )}
      <Field label="Note (mandatory)">
        <textarea rows={2} autoFocus={action === 'FAIL'} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
    </Modal>
  );
}

// ------------------------------------------------------------------ disputes
function DisputesTab({ rows, sel, setSel, open }: { rows: Dispute[]; sel: number; setSel: (i: number) => void; open: (d: Dispute) => void }) {
  if (!rows.length) return <Empty>No open disputes.</Empty>;
  return (
    <div className="table-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th>Raised</th>
            <th>Plate</th>
            <th>Claim</th>
            <th>Worker on duty</th>
            <th>Raised by</th>
            <th className="num">Balance</th>
            <th>Note</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d, i) => (
            <tr key={d.id} className={i === sel ? 'selected' : ''} onClick={() => setSel(i)}>
              <td>{dateTime(d.created_at)}</td>
              <td className="plate">{plate(d.plate)}</td>
              <td>
                {rupees(d.claimed_paise)} {d.claimed_mode} {d.claimed_when && <span className="muted small">at {d.claimed_when}</span>}
              </td>
              <td>
                {d.worker_name ?? <span className="muted">unattributed</span>} {d.zone_id && <span className="muted small">zone {d.zone_id}</span>}
              </td>
              <td>{d.raised_by_role}</td>
              <td className="num">{rupees(d.balance_paise)}</td>
              <td className="small">{d.note}</td>
              <td className="actions">
                <button className="btn btn-sm btn-primary" onClick={() => open(d)}>
                  Resolve
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DisputeDialog({ d, onClose, onDone }: { d: Dispute; onClose: () => void; onDone: () => void }) {
  const [outcome, setOutcome] = useState('UPHELD');
  const [note, setNote] = useState('');
  const [adjust, setAdjust] = useState(d.claimed_paise ? String(-(d.claimed_paise / 100)) : '');
  const [doAdjust, setDoAdjust] = useState(false);
  const { run, busy } = useAction();
  const adj = doAdjust ? parseRupees(adjust) : null;
  const submit = async () => {
    const ok = await run(() => api.resolveDispute(d.id, { outcome, note, adjust_paise: adj || undefined }), `Dispute ${outcome.toLowerCase()}`);
    if (ok) {
      onDone();
      onClose();
    }
  };
  return (
    <Modal
      title={`Resolve dispute #${d.id} · ${plate(d.plate)}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy || !note.trim() || (doAdjust && adj === null)} onClick={submit}>
            Resolve
          </button>
        </>
      }
    >
      <p className="muted">
        Customer claims {rupees(d.claimed_paise)} paid by {d.claimed_mode}. Worker on duty: {d.worker_name ?? 'unknown'}. Current balance{' '}
        {rupees(d.balance_paise)}.
      </p>
      <div className="seg">
        {['UPHELD', 'REJECTED', 'UNRESOLVED'].map((o) => (
          <label key={o} className={`seg-item ${outcome === o ? 'active' : ''}`}>
            <input type="radio" name="outcome" checked={outcome === o} onChange={() => setOutcome(o)} />
            {o === 'UPHELD' ? 'Upheld (customer is right)' : o === 'REJECTED' ? 'Rejected' : 'Unresolved'}
          </label>
        ))}
      </div>
      <Field label="Resolution note (mandatory)">
        <textarea rows={3} autoFocus value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <label className="check">
        <input type="checkbox" checked={doAdjust} onChange={(e) => setDoAdjust(e.target.checked)} /> Also post a balance adjustment
      </label>
      {doAdjust && (
        <Field label="Adjustment in ₹ (negative = credit to customer)" hint={adj !== null ? `posts ${rupees(adj, { signed: true })}` : 'enter an amount'}>
          <input value={adjust} onChange={(e) => setAdjust(e.target.value)} />
        </Field>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ unpaid
function UnpaidTab({ rows, sel, setSel }: { rows: ParkingSession[]; sel: number; setSel: (i: number) => void }) {
  const nav = useNavigate();
  if (!rows.length) return <Empty>No sessions are unpaid 30 minutes after entry.</Empty>;
  return (
    <div className="table-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th>Plate</th>
            <th>Entry</th>
            <th>Inside for</th>
            <th>Gate</th>
            <th>Zone</th>
            <th>Match</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s, i) => (
            <tr key={s.id} className={`clickable ${i === sel ? 'selected' : ''}`} onClick={() => (i === sel ? nav(`/vehicles/${s.vehicle_id}`) : setSel(i))}>
              <td className="plate">{plate(s.plate)}</td>
              <td>{dateTime(s.entry_at)}</td>
              <td>{duration(minutesBetween(s.entry_at))}</td>
              <td>{s.entry_gate}</td>
              <td>{s.zone_id ?? '—'}</td>
              <td>{s.entry_match ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

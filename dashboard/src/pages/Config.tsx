import { useEffect, useMemo, useState } from 'react';
import { api, type Camera, type Gate, type PassType, type Role, type ScheduleRow, type Settings, type Tariff, type User, type Zone } from '../api';
import { useAuth } from '../auth';
import { dateTime, duration, istDate, isoToIstLocal, istLocalToIso, parseRupees, rupees } from '../format';
import { Badge, Card, Empty, ErrorBox, Field, Loading, Modal, StatusBadge, Tabs, useAction, useAsync } from '../ui';
import { invalidateUsers } from '../users';

type TabId = 'settings' | 'gates' | 'classes' | 'tariffs' | 'passes' | 'zones' | 'users';

export default function ConfigPage() {
  const [tab, setTab] = useState<TabId>('settings');
  const { isAdmin } = useAuth();
  return (
    <div className="page">
      <div className="page-h">
        <h1>Configuration</h1>
        {!isAdmin && <Badge tone="info">Read-only — only an admin can change configuration (zone assignments are allowed)</Badge>}
      </div>
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'settings', label: 'Settings' },
          { id: 'gates', label: 'Gates & cameras' },
          { id: 'classes', label: 'Vehicle classes' },
          { id: 'tariffs', label: 'Tariffs' },
          { id: 'passes', label: 'Pass types' },
          { id: 'zones', label: 'Zones & assignments' },
          { id: 'users', label: 'Users & roles' },
        ]}
      />
      {tab === 'settings' && <SettingsForm readOnly={!isAdmin} />}
      {tab === 'gates' && <Gates readOnly={!isAdmin} />}
      {tab === 'classes' && <Classes readOnly={!isAdmin} />}
      {tab === 'tariffs' && <Tariffs readOnly={!isAdmin} />}
      {tab === 'passes' && <PassTypes readOnly={!isAdmin} />}
      {tab === 'zones' && <Zones readOnly={!isAdmin} />}
      {tab === 'users' && <Users readOnly={!isAdmin} />}
    </div>
  );
}

// ------------------------------------------------------------------ settings
const GROUPS: { title: string; keys: string[] }[] = [
  { title: 'Site & receipts', keys: ['lot_name', 'lot_address', 'gstin', 'gst_rate_percent', 'receipt_footer', 'upi_vpa', 'upi_payee_name'] },
  { title: 'Plate matching', keys: ['approx_tolerance', 'dedupe_seconds', 'merge_window_seconds', 'min_confidence', 'approx_regular_max_confidence', 'state_codes'] },
  { title: 'Cash', keys: ['cash_enabled', 'cash_desk_only', 'cash_desk_user_ids', 'cash_limit_paise', 'cash_warn_ratio'] },
  { title: 'Alerts & revenue protection', keys: ['alert_balance_threshold_paise', 'unpaid_flag_minutes', 'to_collect_hours', 'offline_claim_review_hours'] },
  { title: 'Passes & worker app', keys: ['pass_one_open_session', 'pass_candidate_visits', 'pass_reminder_days', 'pass_expiry_warn_days', 'duration_buttons'] },
  { title: 'Data retention (days)', keys: ['retention_plate_images_days', 'retention_full_frames_days', 'retention_events_days'] },
  { title: 'Watchdog', keys: ['camera_stall_seconds', 'internet_down_alert_seconds'] },
];

const LABELS: Record<string, string> = {
  lot_name: 'Lot name', lot_address: 'Address', gstin: 'GSTIN', gst_rate_percent: 'GST rate (%)', receipt_footer: 'Receipt footer text',
  upi_vpa: 'UPI VPA (payee)', upi_payee_name: 'UPI payee name', approx_tolerance: 'Approximate-match tolerance (edits)',
  dedupe_seconds: 'Same-plate de-dupe window (s)', merge_window_seconds: 'Cross-camera merge window (s)', min_confidence: 'Minimum OCR confidence',
  approx_regular_max_confidence: 'Regular-vehicle approx match: max read confidence', state_codes: 'Valid state codes',
  cash_enabled: 'Accept cash', cash_desk_only: 'Cash desk only (stricter mode)', cash_desk_user_ids: 'Cash desk users',
  cash_limit_paise: 'Cash-in-hand limit per worker', cash_warn_ratio: 'Warn at share of limit (0–1)',
  alert_balance_threshold_paise: 'Exit alert / defaulter balance threshold', unpaid_flag_minutes: 'Flag unpaid after (min)',
  to_collect_hours: '"To collect" list window (h)', offline_claim_review_hours: 'Offline UPI claim → review after (h)',
  pass_one_open_session: 'Pass: one open session at a time', pass_candidate_visits: 'Pass candidate after N visits in 30 days',
  pass_reminder_days: 'Pass reminder days before expiry', pass_expiry_warn_days: 'Exit display warns N days before expiry',
  duration_buttons: 'Worker app duration buttons (minutes)', retention_plate_images_days: 'Plate images', retention_full_frames_days: 'Full frames',
  retention_events_days: 'ANPR event rows', camera_stall_seconds: 'Camera stall alert after (s)', internet_down_alert_seconds: 'Internet-down alert after (s)',
};

function SettingsForm({ readOnly }: { readOnly: boolean }) {
  const { data, error, loading, reload } = useAsync(() => api.settings(), []);
  const users = useAsync(() => api.users(), []);
  const [draft, setDraft] = useState<Settings>({});
  const { run, busy } = useAction();
  useEffect(() => {
    if (data) setDraft(data);
  }, [data]);
  if (!data) return error ? <ErrorBox error={error} onRetry={reload} /> : loading ? <Loading /> : null;

  const known = new Set(GROUPS.flatMap((g) => g.keys));
  const others = Object.keys(data).filter((k) => !known.has(k) && !k.startsWith('_'));
  const groups = others.length ? [...GROUPS, { title: 'Other', keys: others }] : GROUPS;
  const changed = Object.keys(draft).filter((k) => !k.startsWith('_') && JSON.stringify(draft[k]) !== JSON.stringify(data[k]));
  const save = () =>
    run(async () => {
      await api.saveSettings(Object.fromEntries(changed.map((k) => [k, draft[k]])));
      await reload();
    }, `Saved ${changed.length} setting${changed.length > 1 ? 's' : ''}`);

  return (
    <>
      <div className="settings-grid">
        {groups.map((g) => (
          <Card key={g.title} title={g.title}>
            {g.keys
              .filter((k) => k in data)
              .map((k) => (
                <SettingField key={k} k={k} value={draft[k]} original={data[k]} readOnly={readOnly} users={users.data ?? []}
                  onChange={(v) => setDraft((d) => ({ ...d, [k]: v }))} />
              ))}
          </Card>
        ))}
      </div>
      {!readOnly && (
        <div className="sticky-save">
          <span className="muted">{changed.length ? `${changed.length} unsaved change${changed.length > 1 ? 's' : ''}: ${changed.join(', ')}` : 'No changes'}</span>
          <button className="btn" disabled={!changed.length} onClick={() => setDraft(data)}>
            Discard
          </button>
          <button className="btn btn-primary" disabled={busy || !changed.length} onClick={save}>
            Save settings
          </button>
        </div>
      )}
    </>
  );
}

function SettingField({ k, value, original, readOnly, users, onChange }: {
  k: string; value: unknown; original: unknown; readOnly: boolean; users: User[]; onChange: (v: unknown) => void;
}) {
  const label = LABELS[k] ?? k;
  const dirty = JSON.stringify(value) !== JSON.stringify(original);
  const [text, setText] = useState<string>(() => toText(k, value));
  useEffect(() => setText(toText(k, value)), [k, value]);
  const cls = `setting ${dirty ? 'dirty' : ''}`;

  if (typeof original === 'boolean')
    return (
      <label className={`check ${cls}`}>
        <input type="checkbox" disabled={readOnly} checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {label}
      </label>
    );
  if (k === 'cash_desk_user_ids') {
    const ids = (value as number[]) ?? [];
    return (
      <div className="field">
        <span>{label}</span>
        <div className={`chips ${cls}`}>
          {users
            .filter((u) => u.role !== 'GUARD')
            .map((u) => (
              <label key={u.id} className="chip">
                <input type="checkbox" disabled={readOnly} checked={ids.includes(u.id)}
                  onChange={(e) => onChange(e.target.checked ? [...ids, u.id] : ids.filter((x) => x !== u.id))} />
                {u.name}
              </label>
            ))}
        </div>
        <small className="muted">Only these users may record cash when “cash desk only” is on</small>
      </div>
    );
  }
  const commit = (t: string) => {
    const v = fromText(k, t, original);
    if (v !== undefined) onChange(v);
  };
  const invalid = fromText(k, text, original) === undefined;
  return (
    <Field label={label} hint={k.endsWith('_paise') ? 'in ₹' : Array.isArray(original) ? 'comma-separated' : undefined}>
      {k === 'receipt_footer' || k === 'state_codes' ? (
        <textarea rows={k === 'state_codes' ? 3 : 2} className={`${cls} ${invalid ? 'invalid' : ''}`} readOnly={readOnly} value={text}
          onChange={(e) => {
            setText(e.target.value);
            commit(e.target.value);
          }} />
      ) : (
        <input className={`${cls} ${invalid ? 'invalid' : ''}`} readOnly={readOnly} value={text}
          inputMode={typeof original === 'number' ? 'decimal' : undefined}
          onChange={(e) => {
            setText(e.target.value);
            commit(e.target.value);
          }} />
      )}
    </Field>
  );
}

export function toText(k: string, v: unknown): string {
  if (v === null || v === undefined) return '';
  if (k.endsWith('_paise') && typeof v === 'number') return String(v / 100);
  if (Array.isArray(v)) return v.join(', ');
  return String(v);
}

/** Parse a settings text box back to the type of the original value; undefined = invalid. */
export function fromText(k: string, t: string, original: unknown): unknown {
  if (k.endsWith('_paise')) {
    const p = parseRupees(t);
    return p === null ? undefined : p;
  }
  if (typeof original === 'number') {
    const n = Number(t);
    return t.trim() === '' || Number.isNaN(n) ? undefined : n;
  }
  if (Array.isArray(original)) {
    const parts = t.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);
    const numeric = original.length ? typeof original[0] === 'number' : parts.every((x) => /^\d+$/.test(x));
    if (numeric) {
      const nums = parts.map(Number);
      return nums.some((n) => Number.isNaN(n)) ? undefined : nums;
    }
    return parts.map((x) => x.toUpperCase());
  }
  return t;
}

// ------------------------------------------------------------------ gates & cameras
function Gates({ readOnly }: { readOnly: boolean }) {
  const { data, error, loading, reload } = useAsync(() => api.gates(), []);
  const [editCam, setEditCam] = useState<Camera | null>(null);
  const [newGate, setNewGate] = useState(false);
  if (!data) return error ? <ErrorBox error={error} onRetry={reload} /> : loading ? <Loading /> : null;
  return (
    <>
      {data.map((g) => (
        <GateCard key={g.id} gate={g} readOnly={readOnly} onSaved={reload} onEditCam={setEditCam} />
      ))}
      {!readOnly && (
        <div className="btn-row">
          <button className="btn" onClick={() => setNewGate(true)}>
            Add gate
          </button>
        </div>
      )}
      {newGate && (
        <GateCardModal onClose={() => setNewGate(false)} onSaved={() => (setNewGate(false), reload())} />
      )}
      {editCam && <CameraDialog cam={editCam} gates={data} readOnly={readOnly} onClose={() => setEditCam(null)} onSaved={reload} />}
    </>
  );
}

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function GateCard({ gate, readOnly, onSaved, onEditCam }: { gate: Gate; readOnly: boolean; onSaved: () => void; onEditCam: (c: Camera) => void }) {
  const [g, setG] = useState(gate);
  const { run, busy } = useAction();
  useEffect(() => setG(gate), [gate]);
  const dirty = JSON.stringify({ ...g, cameras: undefined }) !== JSON.stringify({ ...gate, cameras: undefined });
  const setRow = (i: number, patch: Partial<ScheduleRow>) => setG((x) => ({ ...x, schedule: x.schedule.map((r, j) => (j === i ? { ...r, ...patch } : r)) }));
  const save = () =>
    run(async () => {
      const { cameras: _c, ...body } = g;
      void _c;
      await api.saveGate(body);
      onSaved();
    }, `Gate ${g.id} saved`);
  return (
    <Card
      title={
        <>
          {g.id} · {g.name} {!g.enabled && <Badge>disabled</Badge>}
        </>
      }
      actions={!readOnly && <button className="btn btn-sm btn-primary" disabled={!dirty || busy} onClick={save}>Save gate</button>}
    >
      <div className="form-grid">
        <Field label="Name">
          <input readOnly={readOnly} value={g.name} onChange={(e) => setG({ ...g, name: e.target.value })} />
        </Field>
        <Field label="Default direction">
          <select disabled={readOnly} value={g.direction} onChange={(e) => setG({ ...g, direction: e.target.value as Gate['direction'] })}>
            <option>IN</option>
            <option>OUT</option>
            <option>BOTH</option>
          </select>
        </Field>
        <label className="check">
          <input type="checkbox" disabled={readOnly} checked={g.enabled} onChange={(e) => setG({ ...g, enabled: e.target.checked })} /> Enabled
        </label>
      </div>
      <h4>Time-of-day schedule (first matching row wins; otherwise the default direction)</h4>
      {g.schedule.length === 0 ? (
        <p className="muted small">No schedule — the gate always uses {g.direction}.</p>
      ) : (
        <table className="tbl tbl-compact">
          <thead>
            <tr>
              <th>From</th>
              <th>To</th>
              <th>Direction</th>
              <th>Days (none = every day)</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {g.schedule.map((r, i) => {
              const days = (r.days as number[] | undefined) ?? [];
              return (
                <tr key={i}>
                  <td>
                    <input type="time" readOnly={readOnly} value={r.from} onChange={(e) => setRow(i, { from: e.target.value })} />
                  </td>
                  <td>
                    <input type="time" readOnly={readOnly} value={r.to === '24:00' ? '23:59' : r.to} onChange={(e) => setRow(i, { to: e.target.value })} />
                  </td>
                  <td>
                    <select disabled={readOnly} value={r.direction} onChange={(e) => setRow(i, { direction: e.target.value as ScheduleRow['direction'] })}>
                      <option>IN</option>
                      <option>OUT</option>
                      <option>BOTH</option>
                    </select>
                  </td>
                  <td>
                    <div className="chips">
                      {DAYS.map((d, di) => (
                        <label key={d} className="chip">
                          <input type="checkbox" disabled={readOnly} checked={days.includes(di)}
                            onChange={(e) => {
                              const nd = e.target.checked ? [...days, di].sort() : days.filter((x) => x !== di);
                              setRow(i, { days: nd.length ? nd : undefined });
                            }} />
                          {d}
                        </label>
                      ))}
                    </div>
                  </td>
                  <td>
                    {!readOnly && (
                      <button className="btn btn-sm" onClick={() => setG({ ...g, schedule: g.schedule.filter((_, j) => j !== i) })}>
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {!readOnly && (
        <button className="btn btn-sm" onClick={() => setG({ ...g, schedule: [...g.schedule, { from: '07:00', to: '11:00', direction: 'IN' }] })}>
          Add schedule row
        </button>
      )}
      <h4>Cameras</h4>
      <table className="tbl tbl-compact">
        <thead>
          <tr>
            <th>ID</th>
            <th>Role</th>
            <th>Side</th>
            <th>RTSP</th>
            <th>Capture line</th>
            <th>In vector</th>
            <th>Enabled</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {gate.cameras.map((c) => (
            <tr key={c.id}>
              <td className="mono">{c.id}</td>
              <td>{c.role}</td>
              <td>{c.side}</td>
              <td className="mono small">{c.rtsp_url || '—'}</td>
              <td className="mono small">{JSON.stringify(c.capture_line)}</td>
              <td className="mono small">{JSON.stringify(c.in_vector)}</td>
              <td>{c.enabled ? 'yes' : 'no'}</td>
              <td>
                <button className="btn btn-sm" onClick={() => onEditCam(c)}>
                  {readOnly ? 'View' : 'Edit'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!readOnly && (
        <button className="btn btn-sm" onClick={() => onEditCam({ id: `${gate.id}-`, gate_id: gate.id, role: 'ANPR', side: 'LEFT', rtsp_url: '', roi: [], capture_line: [], in_vector: [0, 1], enabled: true })}>
          Add camera
        </button>
      )}
    </Card>
  );
}

function GateCardModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [id, setId] = useState('G3');
  const [name, setName] = useState('');
  const [direction, setDirection] = useState<Gate['direction']>('BOTH');
  const { run, busy } = useAction();
  return (
    <Modal title="Add gate" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || !id || !name} onClick={() => run(() => api.saveGate({ id, name, direction, schedule: [], enabled: true }), 'Gate added').then((ok) => ok && onSaved())}>Add</button></>}>
      <Field label="Gate ID"><input value={id} onChange={(e) => setId(e.target.value.toUpperCase())} /></Field>
      <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <Field label="Direction">
        <select value={direction} onChange={(e) => setDirection(e.target.value as Gate['direction'])}><option>IN</option><option>OUT</option><option>BOTH</option></select>
      </Field>
    </Modal>
  );
}

function CameraDialog({ cam, gates, readOnly, onClose, onSaved }: { cam: Camera; gates: Gate[]; readOnly: boolean; onClose: () => void; onSaved: () => void }) {
  const [c, setC] = useState(cam);
  const [json, setJson] = useState({ roi: JSON.stringify(cam.roi), capture_line: JSON.stringify(cam.capture_line), in_vector: JSON.stringify(cam.in_vector) });
  const { run, busy } = useAction();
  const parsed = useMemo(() => {
    try {
      return { roi: JSON.parse(json.roi), capture_line: JSON.parse(json.capture_line), in_vector: JSON.parse(json.in_vector) };
    } catch {
      return null;
    }
  }, [json]);
  const save = () =>
    run(async () => {
      await api.saveCamera({ ...c, ...parsed! });
      onSaved();
      onClose();
    }, `Camera ${c.id} saved`);
  return (
    <Modal wide title={`Camera ${cam.id}`} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Close</button>
        {!readOnly && <button className="btn btn-primary" disabled={busy || !parsed || !c.id} onClick={save}>Save camera</button>}</>}>
      <div className="form-grid">
        <Field label="Camera ID"><input readOnly={readOnly || cam.id.length > 3} value={c.id} onChange={(e) => setC({ ...c, id: e.target.value })} /></Field>
        <Field label="Gate">
          <select disabled={readOnly} value={c.gate_id} onChange={(e) => setC({ ...c, gate_id: e.target.value })}>
            {gates.map((g) => <option key={g.id}>{g.id}</option>)}
          </select>
        </Field>
        <Field label="Role">
          <select disabled={readOnly} value={c.role} onChange={(e) => setC({ ...c, role: e.target.value })}><option>ANPR</option><option>OVERVIEW</option></select>
        </Field>
        <Field label="Side">
          <select disabled={readOnly} value={c.side} onChange={(e) => setC({ ...c, side: e.target.value })}><option>LEFT</option><option>RIGHT</option><option>CENTER</option></select>
        </Field>
      </div>
      <Field label="RTSP URL"><input className="mono" readOnly={readOnly} value={c.rtsp_url} onChange={(e) => setC({ ...c, rtsp_url: e.target.value })} /></Field>
      <div className="form-grid">
        <Field label="Region of interest — polygon [[x,y],…] (JSON)">
          <textarea className={`mono ${parsed ? '' : 'invalid'}`} rows={3} readOnly={readOnly} value={json.roi} onChange={(e) => setJson({ ...json, roi: e.target.value })} />
        </Field>
        <Field label="Capture line [[x1,y1],[x2,y2]] (JSON)">
          <textarea className="mono" rows={3} readOnly={readOnly} value={json.capture_line} onChange={(e) => setJson({ ...json, capture_line: e.target.value })} />
        </Field>
        <Field label="IN travel vector [dx,dy] (JSON)" hint="direction of travel that counts as IN, in image coordinates">
          <input className="mono" readOnly={readOnly} value={json.in_vector} onChange={(e) => setJson({ ...json, in_vector: e.target.value })} />
        </Field>
      </div>
      {!parsed && <div className="error-box">One of the JSON fields is not valid JSON</div>}
      <label className="check"><input type="checkbox" disabled={readOnly} checked={c.enabled} onChange={(e) => setC({ ...c, enabled: e.target.checked })} /> Enabled</label>
      {parsed && <Geometry roi={parsed.roi} line={parsed.capture_line} vec={parsed.in_vector} />}
    </Modal>
  );
}

/** Tiny SVG preview of ROI + capture line on a 1280×720 frame. */
function Geometry({ roi, line, vec }: { roi: unknown; line: unknown; vec: unknown }) {
  const pts = Array.isArray(roi) ? (roi as number[][]).filter((p) => Array.isArray(p) && p.length === 2) : [];
  const ln = Array.isArray(line) && line.length === 2 ? (line as number[][]) : null;
  const v = Array.isArray(vec) && vec.length === 2 ? (vec as number[]) : null;
  const mid = ln ? [(ln[0][0] + ln[1][0]) / 2, (ln[0][1] + ln[1][1]) / 2] : [640, 360];
  const norm = v ? Math.hypot(v[0], v[1]) || 1 : 1;
  return (
    <svg className="geometry" viewBox="0 0 1280 720" role="img" aria-label="camera geometry preview">
      <rect width="1280" height="720" className="geo-frame" />
      {pts.length > 2 && <polygon points={pts.map((p) => p.join(',')).join(' ')} className="geo-roi" />}
      {ln && <line x1={ln[0][0]} y1={ln[0][1]} x2={ln[1][0]} y2={ln[1][1]} className="geo-line" />}
      {v && <line x1={mid[0]} y1={mid[1]} x2={mid[0] + (v[0] / norm) * 120} y2={mid[1] + (v[1] / norm) * 120} className="geo-vec" markerEnd="url(#arrow)" />}
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" className="geo-arrow" />
        </marker>
      </defs>
      <text x="16" y="40" className="geo-text">ROI (shaded) · capture line (solid) · IN direction (arrow)</text>
    </svg>
  );
}

// ------------------------------------------------------------------ vehicle classes
function Classes({ readOnly }: { readOnly: boolean }) {
  const { data, error, reload } = useAsync(() => api.vehicleClasses(), []);
  const { run } = useAction();
  return (
    <Card title="Vehicle classes" pad={false}>
      <ErrorBox error={error} />
      <table className="tbl">
        <thead>
          <tr>
            <th>Code</th>
            <th>Name</th>
            <th>Enabled</th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((v) => (
            <tr key={v.code}>
              <td className="mono">{v.code}</td>
              <td>{v.name}</td>
              <td>
                <label className="switch">
                  <input type="checkbox" disabled={readOnly} checked={v.enabled}
                    onChange={(e) => {
                      const en = e.target.checked;
                      if (!en || window.confirm(`Enable ${v.name}? ANPR will start opening ${v.name.toLowerCase()} sessions using its tariff.`))
                        void run(() => api.updateVehicleClass(v.code, { enabled: en }), `${v.name} ${en ? 'enabled' : 'disabled'}`).then(reload);
                    }} />
                  <span>{v.enabled ? 'On' : 'Off'}</span>
                </label>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

// ------------------------------------------------------------------ tariffs
type TariffDraft = Omit<Tariff, 'id' | 'version' | 'created_by' | 'created_at'>;
const PREVIEW_MINUTES = [30, 120, 130, 180, 360, 720, 780, 1440, 2160];

function Tariffs({ readOnly }: { readOnly: boolean }) {
  const { data, error, loading, reload } = useAsync(() => api.tariffs(), []);
  const [draft, setDraft] = useState<TariffDraft | null>(null);
  if (!data) return error ? <ErrorBox error={error} onRetry={reload} /> : loading ? <Loading /> : null;
  const classes = Array.from(new Set(data.map((t) => t.vehicle_class)));
  const now = Date.now();
  const inForce = (vc: string) =>
    data.filter((t) => t.vehicle_class === vc && new Date(t.effective_from).getTime() <= now).sort((a, b) => b.version - a.version)[0];
  const startNew = (vc: string) => {
    const base = inForce(vc) ?? data.find((t) => t.vehicle_class === vc);
    const { id: _i, version: _v, created_by: _c, created_at: _ca, ...rest } = base!;
    void _i; void _v; void _c; void _ca;
    setDraft({ ...rest, notes: '', effective_from: new Date(now + 15 * 60000).toISOString() });
  };
  return (
    <>
      {classes.map((vc) => (
        <Card key={vc} title={`${vc} tariff versions`} pad={false}
          actions={!readOnly && <button className="btn btn-sm btn-primary" onClick={() => startNew(vc)}>New version</button>}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Ver.</th>
                <th>Effective from</th>
                <th>First slab</th>
                <th className="num">Per extra hour</th>
                <th className="num">Grace</th>
                <th>Block cap</th>
                <th className="num">Daily cap</th>
                <th className="num">Overnight</th>
                <th className="num">Free min</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {data
                .filter((t) => t.vehicle_class === vc)
                .map((t) => (
                  <tr key={t.id} className={inForce(vc)?.id === t.id ? 'row-ok' : ''}>
                    <td>
                      v{t.version} {inForce(vc)?.id === t.id && <Badge tone="ok">in force</Badge>}
                      {new Date(t.effective_from).getTime() > now && <Badge tone="info">scheduled</Badge>}
                    </td>
                    <td>{dateTime(t.effective_from)}</td>
                    <td>
                      {rupees(t.first_slab_paise)} up to {duration(t.first_slab_minutes)}
                    </td>
                    <td className="num">{rupees(t.per_hour_paise)}</td>
                    <td className="num">{t.grace_minutes} min</td>
                    <td>{t.block_cap_paise ? `${rupees(t.block_cap_paise)} / ${duration(t.block_minutes)}` : '—'}</td>
                    <td className="num">{rupees(t.daily_cap_paise)}</td>
                    <td className="num">{t.overnight_paise ? `${rupees(t.overnight_paise)} after ${t.overnight_cutoff_hour}:00` : '—'}</td>
                    <td className="num">{t.free_minutes}</td>
                    <td className="small">{t.notes}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </Card>
      ))}
      {draft && <TariffDialog draft={draft} onClose={() => setDraft(null)} onSaved={reload} />}
    </>
  );
}

function TariffDialog({ draft: init, onClose, onSaved }: { draft: TariffDraft; onClose: () => void; onSaved: () => void }) {
  const [t, setT] = useState(init);
  const [eff, setEff] = useState(isoToIstLocal(init.effective_from));
  const [preview, setPreview] = useState<Record<number, number | string>>({});
  const { run, busy } = useAction();
  const money = (k: keyof TariffDraft, label: string, nullable = false) => (
    <Field label={label}>
      <input defaultValue={t[k] === null ? '' : String((t[k] as number) / 100)} placeholder={nullable ? 'none' : undefined}
        onChange={(e) => {
          const v = e.target.value.trim();
          const p = v === '' && nullable ? null : parseRupees(v);
          if (p !== null || nullable) setT((x) => ({ ...x, [k]: p }));
        }} />
    </Field>
  );
  const int = (k: keyof TariffDraft, label: string) => (
    <Field label={label}>
      <input type="number" min={0} value={t[k] as number} onChange={(e) => setT((x) => ({ ...x, [k]: parseInt(e.target.value || '0', 10) }))} />
    </Field>
  );
  // live preview: charge for a set of durations starting now (debounced)
  useEffect(() => {
    const id = setTimeout(() => {
      const entry = new Date();
      entry.setSeconds(0, 0);
      PREVIEW_MINUTES.forEach((m) => {
        api
          .previewTariff({ ...t, effective_from: istLocalToIso(eff), entry: entry.toISOString(), exit: new Date(entry.getTime() + m * 60000).toISOString() })
          .then((r) => setPreview((p) => ({ ...p, [m]: r.charge_paise })))
          .catch((e) => setPreview((p) => ({ ...p, [m]: String(e.message ?? 'error') })));
      });
    }, 350);
    return () => clearTimeout(id);
  }, [t, eff]);
  const save = () =>
    run(async () => {
      await api.createTariff({ ...t, effective_from: istLocalToIso(eff) });
      onSaved();
      onClose();
    }, 'New tariff version created');
  return (
    <Modal wide title={`New ${t.vehicle_class} tariff version`} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy} onClick={save}>Create version</button></>}>
      <p className="muted small">Past charges never change: a session is charged by the tariff in force at its entry time.</p>
      <div className="tariff-layout">
        <div className="form-grid">
          <Field label="Effective from (IST)"><input type="datetime-local" value={eff} onChange={(e) => setEff(e.target.value)} /></Field>
          {int('first_slab_minutes', 'First slab (minutes)')}
          {money('first_slab_paise', 'First slab charge (₹)')}
          {money('per_hour_paise', 'Per additional hour (₹)')}
          {int('grace_minutes', 'Grace per slab boundary (min)')}
          {int('block_minutes', 'Cap block length (min)')}
          {money('block_cap_paise', 'Cap per block (₹)', true)}
          {money('daily_cap_paise', 'Daily cap (₹)', true)}
          {money('overnight_paise', 'Overnight charge (₹)')}
          {int('overnight_cutoff_hour', 'Overnight cutoff hour (0–23)')}
          {int('free_minutes', 'Free minutes')}
          <Field label="Notes"><input value={t.notes ?? ''} onChange={(e) => setT({ ...t, notes: e.target.value })} /></Field>
        </div>
        <div>
          <h4>Live preview (entry now)</h4>
          <table className="tbl tbl-compact">
            <thead>
              <tr>
                <th>Parked</th>
                <th className="num">Charge</th>
              </tr>
            </thead>
            <tbody>
              {PREVIEW_MINUTES.map((m) => (
                <tr key={m}>
                  <td>{duration(m)}</td>
                  <td className="num">{typeof preview[m] === 'number' ? rupees(preview[m] as number) : <span className="muted small">{preview[m] ?? '…'}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ pass types
function PassTypes({ readOnly }: { readOnly: boolean }) {
  const { data, error, reload } = useAsync(() => api.passTypes(), []);
  const [edit, setEdit] = useState<PassType | Omit<PassType, 'id'> | null>(null);
  return (
    <Card title="Pass types & prices" pad={false}
      actions={!readOnly && <button className="btn btn-sm btn-primary" onClick={() => setEdit({ vehicle_class: 'BIKE', name: '', period_unit: 'MONTH', period_value: 1, price_paise: 50000, active: true, is_default: false })}>New pass type</button>}>
      <ErrorBox error={error} />
      <table className="tbl">
        <thead>
          <tr>
            <th>Class</th>
            <th>Name</th>
            <th>Period</th>
            <th className="num">Price</th>
            <th>Active</th>
            <th>Default</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((p) => (
            <tr key={p.id} className={p.active ? '' : 'row-muted'}>
              <td>{p.vehicle_class}</td>
              <td>{p.name}</td>
              <td>
                {p.period_value} {p.period_unit.toLowerCase()}
                {p.period_value > 1 ? 's' : ''}
              </td>
              <td className="num">{rupees(p.price_paise)}</td>
              <td>{p.active ? 'yes' : 'no'}</td>
              <td>{p.is_default ? <Badge tone="info">default</Badge> : ''}</td>
              <td>{!readOnly && <button className="btn btn-sm" onClick={() => setEdit(p)}>Edit</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {edit && <PassTypeDialog p={edit} onClose={() => setEdit(null)} onSaved={reload} />}
    </Card>
  );
}

function PassTypeDialog({ p, onClose, onSaved }: { p: PassType | Omit<PassType, 'id'>; onClose: () => void; onSaved: () => void }) {
  const [x, setX] = useState(p);
  const [price, setPrice] = useState(String(p.price_paise / 100));
  const { run, busy } = useAction();
  const paise = parseRupees(price);
  const save = () =>
    run(async () => {
      const { id, ...body } = { id: undefined, ...x, price_paise: paise! } as PassType;
      if (id) await api.updatePassType(id, body);
      else await api.createPassType(body);
      onSaved();
      onClose();
    }, 'Pass type saved');
  return (
    <Modal title={'id' in p ? `Edit ${p.name}` : 'New pass type'} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy || !x.name || !paise} onClick={save}>Save</button></>}>
      <div className="form-grid">
        <Field label="Vehicle class">
          <select value={x.vehicle_class} onChange={(e) => setX({ ...x, vehicle_class: e.target.value })}><option>BIKE</option><option>CAR</option></select>
        </Field>
        <Field label="Name"><input value={x.name} onChange={(e) => setX({ ...x, name: e.target.value })} /></Field>
        <Field label="Period length"><input type="number" min={1} value={x.period_value} onChange={(e) => setX({ ...x, period_value: parseInt(e.target.value || '1', 10) })} /></Field>
        <Field label="Period unit">
          <select value={x.period_unit} onChange={(e) => setX({ ...x, period_unit: e.target.value })}><option>MONTH</option><option>DAY</option></select>
        </Field>
        <Field label="Price (₹)"><input value={price} onChange={(e) => setPrice(e.target.value)} /></Field>
      </div>
      <label className="check"><input type="checkbox" checked={x.active} onChange={(e) => setX({ ...x, active: e.target.checked })} /> Active (offered for sale)</label>
      <label className="check"><input type="checkbox" checked={x.is_default} onChange={(e) => setX({ ...x, is_default: e.target.checked })} /> Default for this class</label>
    </Modal>
  );
}

// ------------------------------------------------------------------ zones
function Zones({ readOnly }: { readOnly: boolean }) {
  const zones = useAsync(() => api.zones(), []);
  const users = useAsync(() => api.users(), []);
  const gates = useAsync(() => api.gates(), []);
  const [date, setDate] = useState(istDate());
  const asg = useAsync(() => api.assignments(date), [date]);
  const [editZone, setEditZone] = useState<Zone | Omit<Zone, 'id'> | null>(null);
  const [assign, setAssign] = useState(false);
  const zname = (id: number) => zones.data?.find((z) => z.id === id)?.name ?? `#${id}`;
  return (
    <>
      <Card title="Zones" pad={false}
        actions={!readOnly && <button className="btn btn-sm btn-primary" onClick={() => setEditZone({ name: '', gate_id: null, description: '' })}>New zone</button>}>
        <ErrorBox error={zones.error} />
        <table className="tbl">
          <thead>
            <tr>
              <th>#</th>
              <th>Name</th>
              <th>Gate (for attribution)</th>
              <th>Description</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {(zones.data ?? []).map((z) => (
              <tr key={z.id}>
                <td>{z.id}</td>
                <td>{z.name}</td>
                <td>{z.gate_id ?? '—'}</td>
                <td>{z.description}</td>
                <td>{!readOnly && <button className="btn btn-sm" onClick={() => setEditZone(z)}>Edit</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Zone assignments" pad={false}
        actions={
          <>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value || istDate())} />
            <button className="btn btn-sm btn-primary" onClick={() => setAssign(true)}>Assign worker</button>
          </>
        }>
        <ErrorBox error={asg.error} />
        {(asg.data ?? []).length === 0 ? (
          <Empty>No assignments on {date}</Empty>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Zone</th>
                <th>Worker</th>
                <th>From</th>
                <th>To</th>
                <th>Shift</th>
              </tr>
            </thead>
            <tbody>
              {(asg.data ?? []).map((a) => (
                <tr key={a.id}>
                  <td>{zname(a.zone_id)}</td>
                  <td>{a.user_name}</td>
                  <td>{dateTime(a.starts_at)}</td>
                  <td>{dateTime(a.ends_at)}</td>
                  <td>{a.shift_label}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {editZone && <ZoneDialog z={editZone} gates={gates.data ?? []} onClose={() => setEditZone(null)} onSaved={zones.reload} />}
      {assign && <AssignDialog zones={zones.data ?? []} users={(users.data ?? []).filter((u) => u.role === 'WORKER' || u.role === 'SUPERVISOR')} date={date}
        onClose={() => setAssign(false)} onSaved={asg.reload} />}
    </>
  );
}

function ZoneDialog({ z, gates, onClose, onSaved }: { z: Zone | Omit<Zone, 'id'>; gates: Gate[]; onClose: () => void; onSaved: () => void }) {
  const [x, setX] = useState(z);
  const { run, busy } = useAction();
  const save = () =>
    run(async () => {
      const body = { name: x.name, gate_id: x.gate_id || null, description: x.description };
      if ('id' in x) await api.updateZone(x.id, body);
      else await api.createZone(body);
      onSaved();
      onClose();
    }, 'Zone saved');
  return (
    <Modal title={'id' in z ? `Edit ${z.name}` : 'New zone'} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy || !x.name} onClick={save}>Save</button></>}>
      <Field label="Name"><input value={x.name} onChange={(e) => setX({ ...x, name: e.target.value })} /></Field>
      <Field label="Gate" hint="unpaid sessions entering through this gate are attributed to the worker covering this zone">
        <select value={x.gate_id ?? ''} onChange={(e) => setX({ ...x, gate_id: e.target.value || null })}>
          <option value="">—</option>
          {gates.map((g) => <option key={g.id} value={g.id}>{g.id} · {g.name}</option>)}
        </select>
      </Field>
      <Field label="Description"><input value={x.description ?? ''} onChange={(e) => setX({ ...x, description: e.target.value })} /></Field>
    </Modal>
  );
}

function AssignDialog({ zones, users, date, onClose, onSaved }: { zones: Zone[]; users: User[]; date: string; onClose: () => void; onSaved: () => void }) {
  const [zone, setZone] = useState(zones[0]?.id ?? 0);
  const [user, setUser] = useState(users[0]?.id ?? 0);
  const [from, setFrom] = useState(`${date}T06:00`);
  const [to, setTo] = useState(`${date}T14:00`);
  const [label, setLabel] = useState('Morning');
  const { run, busy } = useAction();
  const presets: [string, string, string][] = [['Morning', '06:00', '14:00'], ['Evening', '14:00', '22:00']];
  const save = () =>
    run(async () => {
      await api.assign({ zone_id: zone, user_id: user, starts_at: istLocalToIso(from), ends_at: istLocalToIso(to), shift_label: label });
      onSaved();
      onClose();
    }, 'Worker assigned');
  return (
    <Modal title="Assign worker to zone" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy || !zone || !user || to <= from} onClick={save}>Assign</button></>}>
      <div className="form-grid">
        <Field label="Zone"><select value={zone} onChange={(e) => setZone(Number(e.target.value))}>{zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}</select></Field>
        <Field label="Worker"><select value={user} onChange={(e) => setUser(Number(e.target.value))}>{users.map((u) => <option key={u.id} value={u.id}>{u.name} ({u.role.toLowerCase()})</option>)}</select></Field>
        <Field label="From (IST)"><input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label="To (IST)"><input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
        <Field label="Shift label"><input value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
      </div>
      <div className="btn-group">
        {presets.map(([l, a, b]) => (
          <button key={l} className="btn btn-sm" onClick={() => (setLabel(l), setFrom(`${date}T${a}`), setTo(`${date}T${b}`))}>{l} {a}–{b}</button>
        ))}
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ users
const ROLES: Role[] = ['ADMIN', 'SUPERVISOR', 'WORKER', 'GUARD'];

function Users({ readOnly }: { readOnly: boolean }) {
  const { data, error, reload } = useAsync(() => api.users(), []);
  const [edit, setEdit] = useState<User | null | 'new'>(null);
  const { run } = useAction();
  const done = () => {
    invalidateUsers();
    void reload();
  };
  return (
    <Card title="Users & roles" pad={false} actions={!readOnly && <button className="btn btn-sm btn-primary" onClick={() => setEdit('new')}>New user</button>}>
      <ErrorBox error={error} />
      <table className="tbl">
        <thead>
          <tr>
            <th>Name</th>
            <th>Username</th>
            <th>Role</th>
            <th>Phone</th>
            <th>Phone bound</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((u) => (
            <tr key={u.id} className={u.active ? '' : 'row-muted'}>
              <td>{u.name}</td>
              <td className="mono">{u.username}</td>
              <td><Badge tone={u.role === 'ADMIN' ? 'bad' : u.role === 'SUPERVISOR' ? 'warn' : 'muted'}>{u.role}</Badge></td>
              <td>{u.phone ?? '—'}</td>
              <td>{u.device_bound ? 'yes' : 'no'}</td>
              <td><StatusBadge status={u.active ? 'ACTIVE' : 'DISABLED'} /></td>
              <td className="actions">
                {!readOnly && (
                  <>
                    <button className="btn btn-sm" onClick={() => setEdit(u)}>Edit</button>
                    {u.device_bound && (
                      <button className="btn btn-sm" onClick={() => window.confirm(`Unbind ${u.name}'s phone? They can then log in from a new phone.`) &&
                        void run(() => api.resetDevice(u.id), 'Device binding reset').then(done)}>
                        Reset phone
                      </button>
                    )}
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {edit && <UserDialog u={edit === 'new' ? null : edit} onClose={() => setEdit(null)} onSaved={done} />}
    </Card>
  );
}

function UserDialog({ u, onClose, onSaved }: { u: User | null; onClose: () => void; onSaved: () => void }) {
  const [x, setX] = useState({ username: u?.username ?? '', name: u?.name ?? '', role: (u?.role ?? 'WORKER') as Role, phone: u?.phone ?? '', active: u?.active ?? true });
  const [pin, setPin] = useState('');
  const [password, setPassword] = useState('');
  const { run, busy } = useAction();
  const pinOk = !pin || /^\d{4,}$/.test(pin);
  const needsSecret = !u && !pin && !password;
  const save = () =>
    run(async () => {
      const body = { ...x, phone: x.phone || undefined, pin: pin || undefined, password: password || undefined };
      if (u) await api.updateUser(u.id, body);
      else await api.createUser(body);
      onSaved();
      onClose();
    }, u ? 'User updated' : 'User created');
  return (
    <Modal title={u ? `Edit ${u.name}` : 'New user'} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy || !x.username || !x.name || !pinOk || needsSecret} onClick={save}>Save</button></>}>
      <div className="form-grid">
        <Field label="Username"><input readOnly={!!u} value={x.username} onChange={(e) => setX({ ...x, username: e.target.value.trim() })} /></Field>
        <Field label="Full name"><input value={x.name} onChange={(e) => setX({ ...x, name: e.target.value })} /></Field>
        <Field label="Role"><select value={x.role} onChange={(e) => setX({ ...x, role: e.target.value as Role })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></Field>
        <Field label="Phone"><input value={x.phone} onChange={(e) => setX({ ...x, phone: e.target.value })} /></Field>
        <Field label={u ? 'New PIN (leave blank to keep)' : 'PIN (phone app login, 4+ digits)'}>
          <input className={pinOk ? '' : 'invalid'} inputMode="numeric" value={pin} onChange={(e) => setPin(e.target.value)} />
        </Field>
        <Field label={u ? 'New password (leave blank to keep)' : 'Password (dashboard login)'}>
          <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
      </div>
      <label className="check"><input type="checkbox" checked={x.active} onChange={(e) => setX({ ...x, active: e.target.checked })} /> Active</label>
      {needsSecret && <p className="muted small">Set a PIN or a password.</p>}
    </Modal>
  );
}

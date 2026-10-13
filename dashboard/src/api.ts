/** Typed client for the edge backend. Token lives in localStorage (guarded); any 401 logs the user out. */

// ------------------------------------------------------------------ types (mirror backend/app/api/serial.py etc.)
export type Role = 'ADMIN' | 'SUPERVISOR' | 'WORKER' | 'GUARD';

export interface User {
  id: number;
  username: string;
  name: string;
  role: Role;
  phone: string | null;
  device_bound: boolean;
  active: boolean;
}

export interface Candidate {
  plate: string;
  confidence?: number;
  session_id?: number;
  distance?: number;
  source?: string;
}

export interface AnprEvent {
  id: string;
  gate_id: string;
  camera_ids: string[];
  direction: 'IN' | 'OUT';
  wrong_way: boolean;
  vehicle_class: string;
  ts: string;
  raw_plate: string | null;
  plate: string | null;
  confidence: number | null;
  candidates: Candidate[];
  status: string;
  review_reason: string | null;
  vehicle_id: number | null;
  session_id: number | null;
  match_type: string | null;
  match_distance: number | null;
  matched_plate: string | null;
  latency_ms: number | null;
  images: Record<string, string>;
  extra_images: string[];
}

export interface ParkingSession {
  id: number;
  vehicle_id: number | null;
  plate: string | null;
  display_plate: string | null;
  vehicle_class: string;
  status: string;
  entry_at: string | null;
  exit_at: string | null;
  entry_gate: string | null;
  exit_gate: string | null;
  est_duration_minutes: number | null;
  charge_paise: number | null;
  zone_id: number | null;
  parked_location: string | null;
  pass_id: number | null;
  entry_match: string | null;
  exit_match: string | null;
  unpaid_flagged: boolean;
  note: string | null;
  entry_event_id: string | null;
  exit_event_id: string | null;
  entry_images?: Record<string, string>;
  exit_images?: Record<string, string>;
}

export interface Payment {
  id: number;
  vehicle_id: number;
  plate: string | null;
  session_id: number | null;
  pass_id: number | null;
  purpose: string;
  mode: 'UPI' | 'CASH';
  amount_paise: number;
  base_paise: number | null;
  dues_paise: number | null;
  duration_minutes: number | null;
  status: string;
  txn_ref: string | null;
  upi_uri: string | null;
  offline: boolean;
  collected_by: number | null;
  channel: string | null;
  created_at: string | null;
  confirmed_at: string | null;
  utr: string | null;
  receipt: { code: string; number: string; link: string | null; channel: string | null; delivery_status: string } | null;
  limit_breach: boolean;
  status_note: string | null;
  stale?: boolean;
}

export interface Dispute {
  id: number;
  vehicle_id: number;
  plate: string | null;
  session_id: number | null;
  claimed_paise: number | null;
  claimed_mode: string;
  claimed_when: string | null;
  zone_id: number | null;
  worker_id: number | null;
  worker_name: string | null;
  raised_by_role: string;
  status: string;
  note: string | null;
  resolution_note: string | null;
  created_at: string;
  balance_paise: number | null;
}

export interface PassInfo {
  id: number;
  vehicle_id: number;
  plate: string;
  display_plate: string;
  vehicle_class: string;
  pass_type: string;
  pass_type_id: number;
  starts_on: string;
  ends_on: string;
  starts_at: string;
  ends_at: string;
  amount_paise: number;
  status: string;
  channel: string | null;
  phone: string | null;
}

export interface LedgerEntry {
  id: number;
  kind: string;
  amount_paise: number;
  session_id: number | null;
  payment_id: number | null;
  pass_id: number | null;
  reason: string | null;
  created_at: string;
}

export interface Vehicle {
  id: number;
  plate: string;
  display_plate: string;
  vehicle_class: string;
  first_seen: string | null;
  last_seen: string | null;
  phone: string | null;
  name: string | null;
  notes: string | null;
  balance_paise: number;
  pending_claims_paise: number;
  pass: { id: number; ends_at: string; starts_at: string } | null;
  open_session: ParkingSession | null;
  distance?: number;
  exact?: boolean;
}

export interface VehicleDetail extends Vehicle {
  pass_candidate: boolean;
  sessions: ParkingSession[];
  payments: Payment[];
  ledger: LedgerEntry[];
  passes: PassInfo[];
  ledger_balance_paise: number;
}

export interface Holding {
  user_id: number;
  name: string | null;
  shift_id: number | null;
  zone_id: number | null;
  cash_in_hand_paise: number;
  limit_paise: number;
  warn: boolean;
  blocked: boolean;
}

export interface CashRecon {
  date: string;
  collected_paise: number;
  reversed_paise: number;
  handed_over_paise: number;
  handover_variance_paise: number;
  deposited_paise: number;
  bank_credited_paise: number;
  gap_collected_vs_handed_paise: number;
  gap_handed_vs_deposited_paise: number;
  gap_deposited_vs_credited_paise: number | null;
  deposits: { id: number; amount_paise: number; slip_ref: string; bank_status: string; bank_credited_paise: number | null }[];
  per_worker: { user_id: number; name: string | null; collected_paise: number; handed_over_paise: number; gap_paise: number }[];
}

export interface Handover {
  id: number;
  from_user: number;
  from_name: string | null;
  to_user: number | null;
  shift_id: number;
  expected_paise: number;
  declared_paise: number;
  declared_denoms: Record<string, number>;
  counted_paise: number | null;
  counted_denoms: Record<string, number> | null;
  variance_paise: number | null;
  status: string;
  photo_path: string | null;
  note: string | null;
  declared_at: string;
  confirmed_at: string | null;
}

export interface Deposit {
  id: number;
  business_date: string;
  amount_paise: number;
  slip_ref: string;
  slip_photo_path: string | null;
  deposited_by: number;
  deposited_at: string | null;
  bank_status: string;
  bank_credited_paise: number | null;
  note: string | null;
}

export interface Device {
  id: string;
  kind: string;
  gate_id: string | null;
  name: string | null;
  last_seen: string | null;
  age_s: number | null;
  online: boolean;
  metrics: Record<string, unknown> | null;
}

export interface ReviewCounts {
  unread: number;
  review: number;
  orphans: number;
  offline_claims: number;
  disputes: number;
}

export interface RevenueDay {
  date?: string;
  walkin_upi_paise?: number;
  walkin_cash_paise?: number;
  pass_upi_paise?: number;
  pass_cash_paise?: number;
  claimed_offline_paise?: number;
  charges_paise?: number;
  sessions?: number;
  pass_sessions?: number;
  payments?: number;
  total_paise?: number;
}

export interface LiveData {
  now: string;
  date: string;
  occupancy: Record<string, { total: number; OPEN: number; PREPAID: number; PASS: number }>;
  movements: { gate_id: string; direction: string; hour: number; count: number }[];
  latest_events: AnprEvent[];
  devices: Device[];
  internet: { online: boolean | null; since?: string; gateway_online?: boolean; checked_at?: string; alerted?: boolean };
  gateway: { name: string; online: boolean | null };
  review_counts: ReviewCounts;
  unpaid_flagged: number;
  cash: { holdings: Holding[]; reconciliation: CashRecon };
  revenue_today: RevenueDay;
}

export interface ReviewQueue {
  events: AnprEvent[];
  orphans: ParkingSession[];
  offline_claims: Payment[];
  disputes: Dispute[];
  unpaid_flagged: ParkingSession[];
  counts: ReviewCounts;
}

export interface Alert {
  id: number;
  kind: string;
  severity: string;
  gate_id: string | null;
  session_id: number | null;
  vehicle_id: number | null;
  event_id: string | null;
  message: string | null;
  data: Record<string, unknown> | null;
  created_at: string;
  acknowledged_by: number | null;
  acknowledged_at: string | null;
  note: string | null;
}

export interface WorkerRow {
  shift_id: number;
  user_id: number;
  name: string | null;
  zone_id: number | null;
  opened_at: string;
  closed_at: string | null;
  collections: number;
  upi_paise: number;
  cash_paise: number;
  cash_count: number;
  cash_share: number;
  zone_sessions: number;
  unpaid_in_zone: number;
  unpaid_rate: number;
  disputes_total: number;
  disputes_upheld: number;
  disputes_unresolved: number;
  disputes_rejected: number;
  handover_variance_paise: number;
  handovers: number;
  shift_variance_paise: number;
  cash_receipts_to_phone: number;
  cash_receipts_shown: number;
  cash_receipt_phone_share: number | null;
  limit_breaches: number;
  flags: string[];
}

export interface WorkerComparison {
  rows: WorkerRow[];
  averages: { cash_share?: number; unpaid_rate?: number; disputes?: number };
}

export interface DefaulterRow {
  vehicle_id: number;
  plate: string;
  display_plate: string;
  balance_paise: number;
  last_seen: string;
  phone: string | null;
  visits: number;
}

export interface Defaulters {
  defaulters: DefaulterRow[];
  unrecovered_one_time: DefaulterRow[];
  defaulters_total_paise: number;
  unrecovered_total_paise: number;
}

export interface UpiRecon {
  date?: string;
  lines: number;
  confirmed_offline: number[];
  amount_mismatch: { payment_id: number; txn_ref: string; system_paise: number; settled_paise: number }[];
  unknown_credits: { txn_ref: string; amount_paise: number; parsed: unknown }[];
  missing_from_settlement: { payment_id: number; txn_ref: string; amount_paise: number }[];
}

export interface Tariff {
  id: number;
  vehicle_class: string;
  version: number;
  effective_from: string;
  first_slab_minutes: number;
  first_slab_paise: number;
  per_hour_paise: number;
  grace_minutes: number;
  block_minutes: number;
  block_cap_paise: number | null;
  daily_cap_paise: number | null;
  overnight_paise: number;
  overnight_cutoff_hour: number;
  free_minutes: number;
  notes: string | null;
  created_by: number | null;
  created_at: string | null;
}

export interface PassType {
  id: number;
  vehicle_class: string;
  name: string;
  period_unit: string;
  period_value: number;
  price_paise: number;
  active: boolean;
  is_default: boolean;
}

export interface Camera {
  id: string;
  gate_id: string;
  role: string;
  side: string;
  rtsp_url: string;
  roi: number[][];
  capture_line: number[][];
  in_vector: number[];
  enabled: boolean;
}

export interface ScheduleRow {
  from: string;
  to: string;
  direction: 'IN' | 'OUT' | 'BOTH';
  [k: string]: unknown;
}

export interface Gate {
  id: string;
  name: string;
  direction: 'IN' | 'OUT' | 'BOTH';
  schedule: ScheduleRow[];
  enabled: boolean;
  cameras: Camera[];
}

export interface Zone {
  id: number;
  name: string;
  gate_id: string | null;
  description: string | null;
}

export interface ZoneAssignment {
  id: number;
  zone_id: number;
  user_id: number;
  user_name: string;
  starts_at: string;
  ends_at: string;
  shift_label: string;
}

export interface AuditRow {
  id: number;
  table: string;
  row_id: string;
  action: string;
  user_id: number | null;
  before: unknown;
  after: unknown;
  created_at: string;
}

export type Settings = Record<string, unknown>;

// ------------------------------------------------------------------ token storage
const TOKEN_KEY = 'park.dashboard.token';
const USER_KEY = 'park.dashboard.user';
let memToken: string | null = null;

export function getToken(): string | null {
  if (memToken) return memToken;
  try {
    memToken = window.localStorage.getItem(TOKEN_KEY);
  } catch {
    /* storage blocked: keep in memory only */
  }
  return memToken;
}

export function setToken(tok: string | null, user?: User | null): void {
  memToken = tok;
  try {
    if (tok) window.localStorage.setItem(TOKEN_KEY, tok);
    else window.localStorage.removeItem(TOKEN_KEY);
    if (user) window.localStorage.setItem(USER_KEY, JSON.stringify(user));
    else if (!tok) window.localStorage.removeItem(USER_KEY);
  } catch {
    /* ignore */
  }
}

export function getCachedUser(): User | null {
  try {
    const s = window.localStorage.getItem(USER_KEY);
    return s ? (JSON.parse(s) as User) : null;
  } catch {
    return null;
  }
}

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();
/** Register a callback run whenever the server answers 401 (token expired / user disabled). */
export function onUnauthorized(fn: Listener): () => void {
  unauthorizedListeners.add(fn);
  return () => unauthorizedListeners.delete(fn);
}

// ------------------------------------------------------------------ core request
export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
    this.name = 'ApiError';
  }
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export function buildUrl(path: string, query?: Query): string {
  if (!query) return path;
  const qs = Object.entries(query)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join('&');
  return qs ? `${path}${path.includes('?') ? '&' : '?'}${qs}` : path;
}

function detailMessage(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d))
      return d
        .map((x) => (x && typeof x === 'object' && 'msg' in x ? `${(x as { loc?: unknown[] }).loc?.slice(-1)[0] ?? ''}: ${(x as { msg: string }).msg}` : String(x)))
        .join('; ');
  }
  return fallback;
}

export interface RequestOpts {
  query?: Query;
  body?: unknown;
  form?: FormData;
  raw?: boolean; // return the Response (for blobs)
  signal?: AbortSignal;
}

export async function request<T = unknown>(method: string, path: string, opts: RequestOpts = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const tok = getToken();
  if (tok) headers['Authorization'] = `Bearer ${tok}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), { method, headers, body, signal: opts.signal });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    throw new ApiError(0, 'Cannot reach the edge server');
  }
  if (res.status === 401 && !path.startsWith('/api/auth/login')) {
    setToken(null);
    unauthorizedListeners.forEach((fn) => fn());
  }
  if (!res.ok) {
    let parsed: unknown = undefined;
    try {
      parsed = await res.json();
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detailMessage(parsed, `${res.status} ${res.statusText}`), parsed);
  }
  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  const txt = await res.text();
  return (txt ? JSON.parse(txt) : null) as T;
}

export const get = <T>(path: string, query?: Query, signal?: AbortSignal) => request<T>('GET', path, { query, signal });
export const post = <T>(path: string, body?: unknown, query?: Query) => request<T>('POST', path, { body, query });
export const put = <T>(path: string, body?: unknown) => request<T>('PUT', path, { body });
export const postForm = <T>(path: string, form: FormData) => request<T>('POST', path, { form });

/** Image src with the token appended (img tags cannot send headers). */
export function imgUrl(path: string | null | undefined): string | undefined {
  if (!path) return undefined;
  const tok = getToken();
  return tok ? buildUrl(path, { token: tok }) : path;
}

/** Fetch with the auth header and hand the result to the browser as a file download. */
export async function download(path: string, query: Query | undefined, filename: string): Promise<void> {
  const res = await request<Response>('GET', path, { query, raw: true });
  const blob = await res.blob();
  saveBlob(blob, filename);
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export function wsUrl(topics?: string[]): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return buildUrl(`${proto}//${window.location.host}/ws`, { token: getToken() ?? '', topics: topics?.join(',') });
}

// ------------------------------------------------------------------ endpoints
export interface ImportRow {
  row: number;
  plate: string;
  display_plate: string;
  status: 'ok' | 'warning' | 'error';
  messages: string[];
  actions: string[];
}
export interface ImportSummary {
  rows: number;
  importable: number;
  errors: number;
  warnings: number;
  new_vehicles: number;
  contacts: number;
  passes: number;
  opening_balances: number;
  opening_dues_paise: number;
  opening_credit_paise: number;
}
export interface ImportResult {
  summary: ImportSummary;
  rows: ImportRow[];
  committed: boolean;
  batch?: string;
  imported_rows?: number;
}

export const api = {
  importCustomers: (file: File, commit: boolean, skipErrors = false) => {
    const f = new FormData();
    f.append('file', file);
    return request<ImportResult>('POST', '/api/import/customers', { form: f, query: { commit, skip_errors: skipErrors } });
  },
  login: (username: string, password: string) => post<{ token: string; user: User }>('/api/auth/login', { username, password }),
  me: () => get<User>('/api/auth/me'),

  live: () => get<LiveData>('/api/dashboard/live'),
  devices: () => get<Device[]>('/api/devices'),

  review: () => get<ReviewQueue>('/api/review'),
  resolveEvent: (id: string, body: { plate?: string; session_id?: number; discard?: boolean; confirm?: boolean; note?: string }) =>
    post(`/api/review/events/${encodeURIComponent(id)}/resolve`, body),
  resolveOrphan: (id: number, body: { action: 'CHARGE' | 'WAIVE'; at?: string; note: string }) =>
    post<ParkingSession>(`/api/review/sessions/${id}/resolve`, body),
  resolveClaim: (id: number, body: { action: 'CONFIRM' | 'FAIL'; utr?: string; note: string }) =>
    post<Payment>(`/api/review/payments/${id}/resolve`, body),

  disputes: (q?: { status?: string; worker_id?: number }) => get<Dispute[]>('/api/disputes', q),
  resolveDispute: (id: number, body: { outcome: string; note: string; adjust_paise?: number | null }) =>
    post<Dispute>(`/api/disputes/${id}/resolve`, body),

  holdings: () => get<Holding[]>('/api/cash/holdings'),
  handovers: (status: string | null = 'PENDING') => get<Handover[]>('/api/cash/handovers', { status: status ?? '' }),
  confirmHandover: (id: number, counted: Record<string, number>, photo: File, note?: string) => {
    const f = new FormData();
    f.append('counted_denominations', JSON.stringify(counted));
    if (note) f.append('note', note);
    f.append('photo', photo);
    return postForm<Handover>(`/api/cash/handovers/${id}/confirm`, f);
  },
  rejectHandover: (id: number, note: string) => post<Handover>(`/api/cash/handovers/${id}/reject`, { note }),
  deposits: () => get<Deposit[]>('/api/cash/deposits'),
  recordDeposit: (d: { business_date: string; amount_paise: number; slip_ref: string; note?: string; photo: File }) => {
    const f = new FormData();
    f.append('business_date', d.business_date);
    f.append('amount_paise', String(d.amount_paise));
    f.append('slip_ref', d.slip_ref);
    if (d.note) f.append('note', d.note);
    f.append('slip_photo', d.photo);
    return postForm<Deposit>('/api/cash/deposits', f);
  },
  bankCredit: (id: number, credited_paise: number, note?: string) =>
    post(`/api/cash/deposits/${id}/bank-credit`, { credited_paise, note }),
  cashRecon: (date?: string) => get<CashRecon>('/api/cash/reconciliation', { date }),

  reversePayment: (id: number, reason: string) => post<Payment>(`/api/payments/${id}/reverse`, { reason }),
  refundPayment: (id: number, reason: string, amount_paise?: number) =>
    post<Payment>(`/api/payments/${id}/refund`, { reason, amount_paise }),
  adjust: (vehicleId: number, amount_paise: number, reason: string) =>
    post<{ vehicle_id: number; balance_paise: number }>(`/api/vehicles/${vehicleId}/adjust`, { amount_paise, reason }),

  searchVehicles: (q: string) => get<Vehicle[]>('/api/vehicles/search', { q }),
  vehicle: (id: number) => get<VehicleDetail>(`/api/vehicles/${id}`),
  setContact: (id: number, phone: string, name?: string) => post<Vehicle>(`/api/vehicles/${id}/contact`, { phone, name }),
  session: (id: number) => get<ParkingSession>(`/api/sessions/${id}`),

  report: <T = unknown>(name: string, start?: string, end?: string) => get<T>(`/api/reports/${name}`, { start, end }),
  upiReconRun: (date?: string) => post<UpiRecon>('/api/reconciliation/upi/run', undefined, { date }),
  upiReconUpload: (file: File) => {
    const f = new FormData();
    f.append('file', file);
    return postForm<UpiRecon>('/api/reconciliation/upi/upload', f);
  },

  alerts: (q: { kind?: string; open_only?: boolean; hours?: number } = {}) => get<Alert[]>('/api/alerts', q),
  ackAlert: (id: number, note?: string) => post<Alert>(`/api/alerts/${id}/ack`, { note }),

  settings: () => get<Settings>('/api/config/settings'),
  saveSettings: (s: Settings) => put<Settings>('/api/config/settings', s),
  tariffs: () => get<Tariff[]>('/api/config/tariffs'),
  createTariff: (t: Omit<Tariff, 'id' | 'version' | 'created_by' | 'created_at'>) => post<Tariff>('/api/config/tariffs', t),
  previewTariff: (t: Omit<Tariff, 'id' | 'version' | 'created_by' | 'created_at'> & { entry: string; exit: string }) =>
    post<{ charge_paise: number }>('/api/config/tariffs/preview', t),
  passTypes: () => get<PassType[]>('/api/config/pass-types'),
  createPassType: (p: Omit<PassType, 'id'>) => post<PassType>('/api/config/pass-types', p),
  updatePassType: (id: number, p: Omit<PassType, 'id'>) => put<PassType>(`/api/config/pass-types/${id}`, p),
  vehicleClasses: () => get<{ code: string; name: string; enabled: boolean }[]>('/api/config/vehicle-classes'),
  updateVehicleClass: (code: string, body: { name?: string; enabled?: boolean }) => put(`/api/config/vehicle-classes/${code}`, body),
  gates: () => get<Gate[]>('/api/config/gates'),
  saveGate: (g: Omit<Gate, 'cameras'>) => put('/api/config/gates', g),
  saveCamera: (c: Camera) => put('/api/config/cameras', c),
  zones: () => get<Zone[]>('/api/zones'),
  createZone: (z: Omit<Zone, 'id'>) => post<{ id: number }>('/api/zones', z),
  updateZone: (id: number, z: Omit<Zone, 'id'>) => put(`/api/zones/${id}`, z),
  assignments: (date?: string) => get<ZoneAssignment[]>('/api/zones/assignments', { date }),
  assign: (a: { zone_id: number; user_id: number; starts_at: string; ends_at: string; shift_label: string }) =>
    post<{ id: number }>('/api/zones/assignments', a),

  users: () => get<User[]>('/api/users'),
  createUser: (u: { username: string; name: string; role: Role; pin?: string; password?: string; phone?: string; active: boolean }) =>
    post<{ id: number }>('/api/users', u),
  updateUser: (id: number, u: { username: string; name: string; role: Role; pin?: string; password?: string; phone?: string | null; active: boolean }) =>
    put(`/api/users/${id}`, u),
  resetDevice: (id: number) => post(`/api/users/${id}/reset-device`),

  audit: (q: { table?: string; row_id?: string; limit?: number } = {}) => get<AuditRow[]>('/api/audit', q),

  privacyExport: (vehicleId: number) => get<Record<string, unknown>>(`/api/privacy/vehicles/${vehicleId}/export`),
  privacyErase: (vehicleId: number, reason: string) => post<Record<string, unknown>>(`/api/privacy/vehicles/${vehicleId}/erase`, { reason }),
};

/** Formatting helpers. Money is integer paise; timestamps are ISO UTC and displayed in Asia/Kolkata. */

export const TZ = 'Asia/Kolkata';

const inr0 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** ₹ amount from paise. Whole rupees print without decimals ("₹1,250"), otherwise two decimals. */
export function rupees(paise: number | null | undefined, opts: { signed?: boolean; blank?: string } = {}): string {
  if (paise === null || paise === undefined || Number.isNaN(paise)) return opts.blank ?? '—';
  const neg = paise < 0;
  const abs = Math.abs(paise);
  const body = abs % 100 === 0 ? inr0.format(abs / 100) : inr2.format(abs / 100);
  const sign = neg ? '−' : opts.signed && paise > 0 ? '+' : '';
  return `${sign}₹${body}`;
}

/** Parse a rupee string typed by a user ("1,250.50", "₹20") into integer paise. Returns null if invalid. */
export function parseRupees(input: string): number | null {
  const s = input.replace(/[₹,\s]/g, '');
  if (!/^-?\d+(\.\d{1,2})?$/.test(s)) return null;
  return Math.round(parseFloat(s) * 100);
}

function toDate(v: string | Date | null | undefined): Date | null {
  if (!v) return null;
  const d = v instanceof Date ? v : new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

const fmtCache = new Map<string, Intl.DateTimeFormat>();
function fmt(opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = JSON.stringify(opts);
  let f = fmtCache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour12: false, ...opts });
    fmtCache.set(key, f);
  }
  return f;
}

function parts(d: Date, opts: Intl.DateTimeFormatOptions): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of fmt(opts).formatToParts(d)) out[p.type] = p.value;
  return out;
}

/** "26 Sep 14:05" in IST */
export function dateTime(v: string | Date | null | undefined, withSeconds = false): string {
  const d = toDate(v);
  if (!d) return '—';
  const p = parts(d, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', ...(withSeconds ? { second: '2-digit' } : {}) });
  const h = p.hour === '24' ? '00' : p.hour;
  return `${p.day} ${p.month} ${h}:${p.minute}${withSeconds ? ':' + p.second : ''}`;
}

/** "14:05:09" in IST */
export function time(v: string | Date | null | undefined, withSeconds = true): string {
  const d = toDate(v);
  if (!d) return '—';
  const p = parts(d, { hour: '2-digit', minute: '2-digit', ...(withSeconds ? { second: '2-digit' } : {}) });
  const h = p.hour === '24' ? '00' : p.hour;
  return `${h}:${p.minute}${withSeconds ? ':' + p.second : ''}`;
}

/** YYYY-MM-DD of the instant in IST (the backend's business date). */
export function istDate(v: string | Date = new Date()): string {
  const d = toDate(v) ?? new Date();
  const p = parts(d, { year: 'numeric', month: '2-digit', day: '2-digit' });
  return `${p.year}-${p.month}-${p.day}`;
}

/** Add days to a YYYY-MM-DD string. */
export function addDays(ymd: string, n: number): string {
  const [y, m, d] = ymd.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + n));
  return dt.toISOString().slice(0, 10);
}

/** Convert an IST wall-clock "YYYY-MM-DDTHH:mm" (from <input type=datetime-local>) into an ISO UTC string. */
export function istLocalToIso(local: string): string {
  const [date, t = '00:00'] = local.split('T');
  const [y, m, d] = date.split('-').map(Number);
  const [hh, mm] = t.split(':').map(Number);
  return new Date(Date.UTC(y, m - 1, d, hh - 5, mm - 30)).toISOString();
}

/** ISO UTC → IST wall-clock "YYYY-MM-DDTHH:mm" for datetime-local inputs. */
export function isoToIstLocal(v: string | Date): string {
  const d = toDate(v) ?? new Date();
  const p = parts(d, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  const h = p.hour === '24' ? '00' : p.hour;
  return `${p.year}-${p.month}-${p.day}T${h}:${p.minute}`;
}

/** Human duration from minutes: "45 min", "2 h 05 min", "1 d 3 h". */
export function duration(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || Number.isNaN(minutes)) return '—';
  const m = Math.max(0, Math.round(minutes));
  if (m < 60) return `${m} min`;
  if (m < 1440) {
    const h = Math.floor(m / 60);
    const r = m % 60;
    return r ? `${h} h ${String(r).padStart(2, '0')} min` : `${h} h`;
  }
  const d = Math.floor(m / 1440);
  const h = Math.floor((m % 1440) / 60);
  return h ? `${d} d ${h} h` : `${d} d`;
}

/** Minutes between two instants (b defaults to now). */
export function minutesBetween(a: string | Date | null | undefined, b: string | Date | null | undefined = new Date()): number | null {
  const da = toDate(a);
  const db = toDate(b);
  if (!da || !db) return null;
  return (db.getTime() - da.getTime()) / 60000;
}

/** "12 s ago", "5 min ago", "3 h ago" */
export function ago(v: string | Date | null | undefined, now: Date = new Date()): string {
  const d = toDate(v);
  if (!d) return 'never';
  const s = Math.round((now.getTime() - d.getTime()) / 1000);
  if (s < 0) return 'just now';
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function pct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

/** Display plate with a space after the state/RTO block: MH43AB1234 → "MH 43 AB 1234". */
export function plate(p: string | null | undefined): string {
  if (!p) return '—';
  const m = /^([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{4})$/.exec(p);
  if (m) return [m[1], m[2], m[3], m[4]].filter(Boolean).join(' ');
  const bh = /^(\d{2})(BH)(\d{4})([A-Z]{1,2})$/.exec(p);
  if (bh) return `${bh[1]} ${bh[2]} ${bh[3]} ${bh[4]}`;
  return p;
}

/** Normalise user input the way the backend does (uppercase, alphanumerics only). */
export function normPlate(s: string): string {
  return s.toUpperCase().replace(/[^A-Z0-9]/g, '');
}

/** snake_case / SCREAMING → "Title case" for column headers. */
export function humanize(key: string): string {
  const s = key.replace(/_paise$/, '').replace(/_/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

/** Guess how to render a report cell from its key and value. */
export function cell(key: string, v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'number') {
    if (key.endsWith('_paise')) return rupees(v);
    if (/(rate|share)$/.test(key) || key.startsWith('traffic_share')) return pct(v, 1);
    return Number.isInteger(v) ? v.toLocaleString('en-IN') : v.toFixed(2);
  }
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(v)) return dateTime(v);
  if (Array.isArray(v)) return v.length ? v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ') : '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

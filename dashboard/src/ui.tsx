/** Small shared UI kit: cards, stats, badges, tables, modals, tabs, toasts, async hook. */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { ApiError, imgUrl } from './api';
import { cell, humanize } from './format';

// ------------------------------------------------------------------ async data
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const reload = useCallback(async () => {
    const my = ++seq.current;
    setLoading(true);
    try {
      const d = await fnRef.current();
      if (my === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (my === seq.current) setError(errMsg(e));
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, reload, setData };
}

export function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

/** Poll `fn` every `ms` while the tab is visible. */
export function useInterval(fn: () => void, ms: number) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const id = setInterval(() => {
      if (typeof document === 'undefined' || document.visibilityState !== 'hidden') ref.current();
    }, ms);
    return () => clearInterval(id);
  }, [ms]);
}

// ------------------------------------------------------------------ toasts
export type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'muted';
interface Toast {
  id: number;
  text: ReactNode;
  tone: Tone;
  title?: string;
  sticky?: boolean;
}
const ToastCtx = createContext<{ notify: (text: ReactNode, tone?: Tone, opts?: { title?: string; sticky?: boolean }) => void }>({
  notify: () => undefined,
});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const dismiss = (id: number) => setToasts((t) => t.filter((x) => x.id !== id));
  const notify = useCallback((text: ReactNode, tone: Tone = 'info', opts: { title?: string; sticky?: boolean } = {}) => {
    const id = next.current++;
    setToasts((t) => [...t.slice(-5), { id, text, tone, ...opts }]);
    if (!opts.sticky) setTimeout(() => dismiss(id), tone === 'bad' ? 9000 : 5000);
  }, []);
  return (
    <ToastCtx.Provider value={{ notify }}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast tone-${t.tone}`}>
            <div className="toast-body">
              {t.title && <strong>{t.title}</strong>}
              <div>{t.text}</div>
            </div>
            <button className="icon-btn" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/** Wrap an action: shows success/error toast, returns true on success. */
export function useAction() {
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async (fn: () => Promise<unknown>, success?: string): Promise<boolean> => {
      setBusy(true);
      try {
        await fn();
        if (success) notify(success, 'ok');
        return true;
      } catch (e) {
        notify(errMsg(e), 'bad', { title: 'Action failed' });
        return false;
      } finally {
        setBusy(false);
      }
    },
    [notify],
  );
  return { run, busy };
}

// ------------------------------------------------------------------ layout bits
export function Card(props: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={`card ${props.className ?? ''}`}>
      {(props.title || props.actions) && (
        <header className="card-h">
          <h2>{props.title}</h2>
          <div className="card-actions">{props.actions}</div>
        </header>
      )}
      <div className={props.pad === false ? '' : 'card-b'}>{props.children}</div>
    </section>
  );
}

export function Stat(props: { label: ReactNode; value: ReactNode; sub?: ReactNode; tone?: Tone; onClick?: () => void }) {
  return (
    <div className={`stat ${props.tone ? 'tone-' + props.tone : ''} ${props.onClick ? 'clickable' : ''}`} onClick={props.onClick}>
      <div className="stat-l">{props.label}</div>
      <div className="stat-v">{props.value}</div>
      {props.sub !== undefined && <div className="stat-s">{props.sub}</div>}
    </div>
  );
}

export function Badge({ tone = 'muted', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge tone-${tone}`} title={title}>
      {children}
    </span>
  );
}

const STATUS_TONES: Record<string, Tone> = {
  MATCHED: 'ok', RESOLVED: 'ok', CONFIRMED: 'ok', CLOSED: 'ok', SETTLED: 'ok', ACTIVE: 'ok', CREDITED: 'ok', PASS: 'info',
  PREPAID: 'ok', UPHELD: 'warn', REJECTED: 'muted', UNREAD: 'bad', REVIEW: 'warn', WRONG_WAY: 'bad', DUPLICATE: 'muted',
  MERGED: 'muted', DISCARDED: 'muted', OPEN: 'warn', PENDING: 'warn', INITIATED: 'muted', CLAIMED_OFFLINE: 'warn',
  FAILED: 'bad', REFUNDED: 'muted', REVERSED: 'bad', ORPHAN_EXIT: 'bad', ORPHAN_ENTRY: 'bad', MISMATCH: 'bad',
  UNRESOLVED: 'bad', EXPIRED: 'muted', CRIT: 'bad', WARN: 'warn', INFO: 'info',
};
export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="muted">—</span>;
  return <Badge tone={STATUS_TONES[status] ?? 'muted'}>{status.replace(/_/g, ' ')}</Badge>;
}

export function Dot({ ok, title }: { ok: boolean | null | undefined; title?: string }) {
  return <span className={`dot ${ok === null || ok === undefined ? 'dot-unknown' : ok ? 'dot-ok' : 'dot-bad'}`} title={title} />;
}

export function ErrorBox({ error, onRetry }: { error: string | null | undefined; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div className="error-box" role="alert">
      <span>{error}</span>
      {onRetry && (
        <button className="btn btn-sm" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading() {
  return <div className="loading">Loading…</div>;
}

// ------------------------------------------------------------------ images
export function Thumb({ src, alt, className, big }: { src: string | null | undefined; alt: string; className?: string; big?: boolean }) {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  if (!src || failed) return <div className={`thumb thumb-empty ${big ? 'thumb-big' : ''} ${className ?? ''}`}>no image</div>;
  const url = imgUrl(src);
  return (
    <>
      <img
        className={`thumb ${big ? 'thumb-big' : ''} ${className ?? ''}`}
        src={url}
        alt={alt}
        loading="lazy"
        onError={() => setFailed(true)}
        onClick={() => setOpen(true)}
      />
      {open && (
        <div className="lightbox" onClick={() => setOpen(false)}>
          <img src={url} alt={alt} />
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------ modal
export function Modal(props: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const { onClose } = props;
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [onClose]);
  return (
    <div className="modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${props.wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true">
        <header className="modal-h">
          <h3>{props.title}</h3>
          <button className="icon-btn" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="modal-b">{props.children}</div>
        {props.footer && <footer className="modal-f">{props.footer}</footer>}
      </div>
    </div>
  );
}

/** Ask for a mandatory reason (reversal, refund, waive…) and optionally an amount. */
export function ReasonDialog(props: {
  title: string;
  label?: string;
  confirmLabel?: string;
  danger?: boolean;
  extra?: ReactNode;
  onSubmit: (reason: string) => Promise<boolean> | boolean;
  onClose: () => void;
}) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!reason.trim()) return;
    setBusy(true);
    const ok = await props.onSubmit(reason.trim());
    setBusy(false);
    if (ok) props.onClose();
  };
  return (
    <Modal
      title={props.title}
      onClose={props.onClose}
      footer={
        <>
          <button className="btn" onClick={props.onClose}>
            Cancel
          </button>
          <button className={`btn ${props.danger ? 'btn-danger' : 'btn-primary'}`} disabled={!reason.trim() || busy} onClick={submit}>
            {props.confirmLabel ?? 'Confirm'}
          </button>
        </>
      }
    >
      {props.extra}
      <label className="field">
        <span>{props.label ?? 'Reason (mandatory, recorded in the audit log)'}</span>
        <textarea autoFocus rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
    </Modal>
  );
}

// ------------------------------------------------------------------ tabs
export function Tabs<T extends string>(props: { tabs: { id: T; label: ReactNode; count?: number; tone?: Tone }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {props.tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={props.value === t.id}
          className={`tab ${props.value === t.id ? 'active' : ''}`}
          onClick={() => props.onChange(t.id)}
        >
          {t.label}
          {t.count !== undefined && <span className={`count ${t.count ? 'tone-' + (t.tone ?? 'warn') : ''}`}>{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ generic table for report rows
export function AutoTable({
  rows,
  columns,
  highlight,
  render,
  empty = 'No rows',
  maxHeight,
}: {
  rows: Record<string, unknown>[];
  columns?: string[];
  highlight?: (r: Record<string, unknown>) => Tone | undefined;
  render?: Record<string, (v: unknown, r: Record<string, unknown>) => ReactNode>;
  empty?: string;
  maxHeight?: number;
}) {
  if (!rows.length) return <Empty>{empty}</Empty>;
  const cols = columns ?? Object.keys(rows[0]);
  return (
    <div className="table-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <table className="tbl">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} className={isNumCol(c, rows) ? 'num' : ''}>
                {humanize(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const tone = highlight?.(r);
            return (
              <tr key={i} className={tone ? `row-${tone}` : ''}>
                {cols.map((c) => (
                  <td key={c} className={isNumCol(c, rows) ? 'num' : ''}>
                    {render?.[c] ? render[c](r[c], r) : cell(c, r[c])}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function isNumCol(c: string, rows: Record<string, unknown>[]): boolean {
  const v = rows.find((r) => r[c] !== null && r[c] !== undefined)?.[c];
  return typeof v === 'number';
}

export function Field({ label, children, hint }: { label: ReactNode; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small className="muted">{hint}</small>}
    </label>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd>{children}</kbd>;
}

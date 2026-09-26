import { useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, saveBlob, type Vehicle } from '../api';
import { dateTime, istDate, normPlate, plate, rupees } from '../format';
import { Badge, Card, Empty, ErrorBox, Field, Modal, useAction, useAsync } from '../ui';

export default function PrivacyPage() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState('');
  const vid = params.get('vehicle');
  const [results, setResults] = useState<Vehicle[] | null>(null);
  const { run, busy } = useAction();
  const selected = useAsync(() => (vid ? api.vehicle(Number(vid)) : Promise.resolve(null)), [vid]);
  const [erasing, setErasing] = useState(false);
  const [lastErase, setLastErase] = useState<Record<string, unknown> | null>(null);

  const search = (e: FormEvent) => {
    e.preventDefault();
    const n = normPlate(q);
    if (n) void run(async () => setResults(await api.searchVehicles(n)));
  };
  const exportJson = (v: Vehicle) =>
    run(async () => {
      const data = await api.privacyExport(v.id);
      saveBlob(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }), `personal-data-${v.plate}-${istDate()}.json`);
    }, 'Export downloaded');

  const v = selected.data;
  return (
    <div className="page">
      <div className="page-h">
        <h1>Privacy (DPDP Act)</h1>
        <span className="muted">Export or erase a customer's personal data on request. Financial records are kept; personal fields and images are removed.</span>
      </div>
      <Card title="Find the vehicle">
        <form className="search" onSubmit={search}>
          <input className="plate-input" placeholder="Plate" value={q} onChange={(e) => setQ(e.target.value.toUpperCase())} />
          <button className="btn btn-primary" disabled={busy}>
            Search
          </button>
        </form>
        {results &&
          (results.length === 0 ? (
            <Empty>No match</Empty>
          ) : (
            <div className="chips">
              {results.map((r) => (
                <button key={r.id} className={`chip chip-btn ${String(r.id) === vid ? 'active' : ''}`} onClick={() => setParams({ vehicle: String(r.id) })}>
                  <span className="plate">{plate(r.plate)}</span> {r.exact ? '' : <span className="muted small">d={r.distance}</span>}
                </button>
              ))}
            </div>
          ))}
      </Card>
      <ErrorBox error={selected.error} />
      {v && (
        <Card title={<span className="plate">{plate(v.plate)}</span>}>
          <div className="kv">
            <div><span>Class</span>{v.vehicle_class}</div>
            <div><span>Phone</span>{v.phone ?? '—'}</div>
            <div><span>Name</span>{v.name ?? '—'}</div>
            <div><span>Notes</span>{v.notes ?? '—'}</div>
            <div><span>First / last seen</span>{dateTime(v.first_seen)} / {dateTime(v.last_seen)}</div>
            <div><span>Balance</span>{rupees(v.balance_paise)}</div>
            <div><span>Records</span>{v.sessions.length} sessions · {v.payments.length} payments · {v.passes.length} passes</div>
          </div>
          <div className="btn-row">
            <button className="btn btn-primary" disabled={busy} onClick={() => exportJson(v)}>
              Download JSON export
            </button>
            <button className="btn btn-danger" disabled={busy} onClick={() => setErasing(true)}>
              Erase personal data…
            </button>
          </div>
          {lastErase && (
            <div className="ok-box">
              Erased. <span className="mono small">{JSON.stringify(lastErase)}</span>
            </div>
          )}
        </Card>
      )}
      {erasing && v && (
        <EraseDialog
          plateText={v.plate}
          onClose={() => setErasing(false)}
          onSubmit={(reason) =>
            run(async () => {
              setLastErase(await api.privacyErase(v.id, reason));
              await selected.reload();
            }, 'Personal data erased')
          }
        />
      )}
    </div>
  );
}

function EraseDialog({ plateText, onClose, onSubmit }: { plateText: string; onClose: () => void; onSubmit: (reason: string) => Promise<boolean> }) {
  const [reason, setReason] = useState('');
  const [typed, setTyped] = useState('');
  const ok = reason.trim() && normPlate(typed) === plateText;
  return (
    <Modal
      title="Erase personal data"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-danger" disabled={!ok} onClick={() => onSubmit(reason.trim()).then((done) => done && onClose())}>
            Erase permanently
          </button>
        </>
      }
    >
      <p>
        This deletes all ANPR images of <strong className="plate">{plate(plateText)}</strong> and removes the phone number, name and notes from the vehicle, its
        payments and receipts. Sessions, payments and ledger entries stay (accounting requirement) but are no longer linked to a person.{' '}
        <Badge tone="bad">cannot be undone</Badge>
      </p>
      <Field label="Reason / request reference (mandatory)">
        <textarea rows={2} autoFocus value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <Field label={`Type the plate ${plateText} to confirm`}>
        <input className="plate-input" value={typed} onChange={(e) => setTyped(e.target.value.toUpperCase())} />
      </Field>
    </Modal>
  );
}

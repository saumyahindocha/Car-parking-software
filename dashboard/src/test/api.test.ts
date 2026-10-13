import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, buildUrl, getToken, imgUrl, onUnauthorized, request, setToken } from '../api';
import { backoffMs } from '../useLive';

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async (_url: string, _init?: RequestInit) =>
    new Response(body === undefined ? '' : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  );
  vi.stubGlobal('fetch', fn);
  return fn;
}

describe('api client', () => {
  beforeEach(() => setToken(null));
  afterEach(() => vi.unstubAllGlobals());

  it('builds query strings, skipping empty values', () => {
    expect(buildUrl('/api/x', { a: 1, b: '', c: null, d: 'x y' })).toBe('/api/x?a=1&d=x%20y');
    expect(buildUrl('/api/x?z=1', { a: 2 })).toBe('/api/x?z=1&a=2');
    expect(buildUrl('/api/x')).toBe('/api/x');
  });

  it('stores the token and sends it as a bearer header', async () => {
    setToken('tok123');
    expect(getToken()).toBe('tok123');
    expect(localStorage.getItem('park.dashboard.token')).toBe('tok123');
    const f = mockFetch(200, { ok: true });
    await request('GET', '/api/health');
    const init = f.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok123');
  });

  it('sends JSON bodies', async () => {
    const f = mockFetch(200, { vehicle_id: 1, balance_paise: 0 });
    await api.adjust(1, -1000, 'goodwill');
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/vehicles/1/adjust');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ amount_paise: -1000, reason: 'goodwill' });
  });

  it('sends multipart for handover confirmation', async () => {
    const f = mockFetch(200, {});
    await api.confirmHandover(7, { '100': 2 }, new File(['x'], 'p.jpg', { type: 'image/jpeg' }), 'ok');
    const init = f.mock.calls[0][1] as RequestInit;
    const form = init.body as FormData;
    expect(form.get('counted_denominations')).toBe('{"100":2}');
    expect(form.get('note')).toBe('ok');
    expect(form.get('photo')).toBeInstanceOf(File);
    expect((init.headers as Record<string, string>)['Content-Type']).toBeUndefined();
  });

  it('raises ApiError with the FastAPI detail', async () => {
    mockFetch(400, { detail: 'note required' });
    await expect(request('POST', '/api/x', { body: {} })).rejects.toMatchObject({ status: 400, message: 'note required' });
    mockFetch(422, { detail: [{ loc: ['body', 'reason'], msg: 'field required' }] });
    await expect(request('POST', '/api/x')).rejects.toThrow('reason: field required');
  });

  it('logs out on 401', async () => {
    setToken('old');
    const cb = vi.fn();
    const off = onUnauthorized(cb);
    mockFetch(401, { detail: 'not authenticated' });
    await expect(request('GET', '/api/review')).rejects.toBeInstanceOf(ApiError);
    expect(cb).toHaveBeenCalledOnce();
    expect(getToken()).toBeNull();
    off();
  });

  it('does not treat a failed login as a logout', async () => {
    const cb = vi.fn();
    const off = onUnauthorized(cb);
    mockFetch(401, { detail: 'invalid credentials' });
    await expect(api.login('a', 'b')).rejects.toThrow('invalid credentials');
    expect(cb).not.toHaveBeenCalled();
    off();
  });

  it('reports network failures', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    await expect(request('GET', '/api/x')).rejects.toMatchObject({ status: 0 });
  });

  it('adds the token to image URLs', () => {
    setToken('t/1');
    expect(imgUrl('/api/images/a.jpg')).toBe('/api/images/a.jpg?token=t%2F1');
    expect(imgUrl(null)).toBeUndefined();
  });

  it('survives blocked localStorage', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => setToken('mem')).not.toThrow();
    expect(getToken()).toBe('mem');
    spy.mockRestore();
  });
});

describe('websocket backoff', () => {
  it('grows exponentially and caps at 30 s', () => {
    expect(backoffMs(0)).toBeGreaterThanOrEqual(800);
    expect(backoffMs(0)).toBeLessThanOrEqual(1200);
    expect(backoffMs(3)).toBeGreaterThanOrEqual(6400);
    expect(backoffMs(20)).toBeLessThanOrEqual(36000);
  });
});

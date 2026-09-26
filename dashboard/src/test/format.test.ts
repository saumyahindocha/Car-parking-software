import { describe, expect, it } from 'vitest';
import { addDays, ago, cell, dateTime, duration, humanize, istDate, isoToIstLocal, istLocalToIso, normPlate, parseRupees, pct, plate, rupees, time } from '../format';
import { fromText, toText } from '../pages/Config';

describe('rupees', () => {
  it('formats whole rupees without decimals and Indian grouping', () => {
    expect(rupees(1000)).toBe('₹10');
    expect(rupees(12345600)).toBe('₹1,23,456');
    expect(rupees(0)).toBe('₹0');
  });
  it('keeps paise when not whole', () => {
    expect(rupees(1050)).toBe('₹10.50');
  });
  it('signs', () => {
    expect(rupees(-2000)).toBe('−₹20');
    expect(rupees(500, { signed: true })).toBe('+₹5');
    expect(rupees(null)).toBe('—');
  });
  it('parses user input to paise', () => {
    expect(parseRupees('₹1,250.5')).toBe(125050);
    expect(parseRupees('-10')).toBe(-1000);
    expect(parseRupees('12.345')).toBeNull();
    expect(parseRupees('abc')).toBeNull();
  });
});

describe('IST time helpers', () => {
  const iso = '2026-09-26T18:35:09Z'; // 00:05:09 IST next day
  it('formats in Asia/Kolkata', () => {
    expect(time(iso)).toBe('00:05:09');
    expect(dateTime(iso)).toMatch(/^27 Sep\S* 00:05$/); // ICU prints "Sep" or "Sept"
    expect(dateTime(iso, true)).toMatch(/00:05:09$/);
    expect(istDate(iso)).toBe('2026-09-27');
  });
  it('round-trips datetime-local values', () => {
    expect(istLocalToIso('2026-09-27T00:05')).toBe('2026-09-26T18:35:00.000Z');
    expect(isoToIstLocal('2026-09-26T18:35:00Z')).toBe('2026-09-27T00:05');
    expect(istLocalToIso('2026-01-01T03:00')).toBe('2025-12-31T21:30:00.000Z');
  });
  it('adds days across month ends', () => {
    expect(addDays('2026-02-28', 1)).toBe('2026-03-01');
    expect(addDays('2026-01-01', -1)).toBe('2025-12-31');
  });
  it('relative time', () => {
    const now = new Date('2026-09-26T12:00:00Z');
    expect(ago('2026-09-26T11:59:30Z', now)).toBe('30 s ago');
    expect(ago('2026-09-26T11:00:00Z', now)).toBe('1 h ago');
    expect(ago(null, now)).toBe('never');
  });
});

describe('duration & misc', () => {
  it('durations', () => {
    expect(duration(45)).toBe('45 min');
    expect(duration(125)).toBe('2 h 05 min');
    expect(duration(120)).toBe('2 h');
    expect(duration(1500)).toBe('1 d 1 h');
    expect(duration(null)).toBe('—');
  });
  it('plates', () => {
    expect(plate('MH43AB1234')).toBe('MH 43 AB 1234');
    expect(plate('22BH1234AB')).toBe('22 BH 1234 AB');
    expect(plate('XYZ')).toBe('XYZ');
    expect(normPlate('mh 43-ab 1234')).toBe('MH43AB1234');
  });
  it('report cells', () => {
    expect(cell('amount_paise', 2500)).toBe('₹25');
    expect(cell('cash_share', 0.25)).toBe('25.0%');
    expect(cell('flags', [])).toBe('—');
    expect(cell('ok', true)).toBe('yes');
    expect(humanize('handed_over_paise')).toBe('Handed over');
    expect(pct(0.5)).toBe('50%');
  });
});

describe('settings text parsing', () => {
  it('paise settings are edited in rupees', () => {
    expect(toText('cash_limit_paise', 200000)).toBe('2000');
    expect(fromText('cash_limit_paise', '2500', 200000)).toBe(250000);
    expect(fromText('cash_limit_paise', 'x', 200000)).toBeUndefined();
  });
  it('arrays keep their element type', () => {
    expect(fromText('duration_buttons', '120, 240,480', [120])).toEqual([120, 240, 480]);
    expect(fromText('state_codes', 'mh, ka', ['MH'])).toEqual(['MH', 'KA']);
    expect(fromText('dedupe_seconds', '', 60)).toBeUndefined();
  });
});

/** Chart wrappers (recharts) using the CSS series tokens so light/dark both work. */
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

export const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)', 'var(--series-5)', 'var(--series-6)'];

export interface BarSeries {
  key: string;
  label: string;
  color?: string;
}

export function Bars({
  data,
  x,
  series,
  height = 240,
  stacked = false,
  yFormat,
  xFormat,
}: {
  data: Record<string, unknown>[];
  x: string;
  series: BarSeries[];
  height?: number;
  stacked?: boolean;
  yFormat?: (v: number) => string;
  xFormat?: (v: unknown) => string;
}) {
  return (
    <div className="chart" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barGap={2} barCategoryGap="18%">
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey={x} tickLine={false} axisLine={{ stroke: 'var(--border)' }} tick={{ fill: 'var(--text-2)', fontSize: 11 }}
            tickFormatter={xFormat as ((v: unknown) => string) | undefined} interval="preserveStartEnd" />
          <YAxis tickLine={false} axisLine={false} tick={{ fill: 'var(--text-2)', fontSize: 11 }} width={yFormat ? 64 : 36}
            tickFormatter={yFormat} allowDecimals={false} />
          <Tooltip
            cursor={{ fill: 'var(--hover)' }}
            contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12, color: 'var(--text)' }}
            labelFormatter={(l) => (xFormat ? xFormat(l) : String(l))}
            formatter={(v: number, name: string) => [yFormat ? yFormat(v) : v, name]}
          />
          {series.length > 1 && <Legend iconType="square" iconSize={10} wrapperStyle={{ fontSize: 12 }}
            formatter={(v: string) => <span style={{ color: 'var(--text-2)' }}>{v}</span>} />}
          {series.map((s, i) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color ?? SERIES[i % SERIES.length]} stackId={stacked ? 'a' : undefined}
              radius={stacked ? (i === series.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]) : [4, 4, 0, 0]}
              stroke="var(--surface)" strokeWidth={stacked ? 1 : 0} maxBarSize={28} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

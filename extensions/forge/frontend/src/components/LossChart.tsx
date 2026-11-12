/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useMemo, useRef, useState, type PointerEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { Point } from '@/lib/forge';

const WIDTH = 640;
const HEIGHT = 240;
const PAD = { top: 12, right: 16, bottom: 28, left: 52 };

/** About four round tick values, the first at or below min and the last at
 * or above max, so the line stays inside the grid. */
function ticks(min: number, max: number): number[] {
  if (min === max) return [min];
  const raw = (max - min) / 4;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? raw;
  const first = Math.floor(min / step) * step;
  const out = [];
  for (let v = first; v < max + step - step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

function format(v: number): string {
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toPrecision(3);
}

/** The training loss by step: one 2px line, a crosshair with the value under
 * the pointer, and the values as a table for reading without the chart. */
export function LossChart({ points, evalPoints = [] }: { points: Point[]; evalPoints?: Point[] }) {
  const { t } = useTranslation();
  const svg = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  const geometry = useMemo(() => {
    if (points.length === 0) return null;
    const steps = [...points, ...evalPoints].map((p) => p.step);
    const values = [...points, ...evalPoints].map((p) => p.value);
    const x0 = Math.min(...steps);
    const x1 = Math.max(...steps);
    const yTicks = ticks(Math.min(0, ...values), Math.max(...values));
    const y0 = Math.min(0, yTicks[0]);
    const y1 = Math.max(yTicks[yTicks.length - 1], ...values);
    const x = (s: number) => PAD.left + (x1 === x0 ? 0.5 : (s - x0) / (x1 - x0)) * (WIDTH - PAD.left - PAD.right);
    const y = (v: number) => HEIGHT - PAD.bottom - ((v - y0) / (y1 - y0 || 1)) * (HEIGHT - PAD.top - PAD.bottom);
    const line = (series: Point[]) => series.map((p, i) => `${i ? 'L' : 'M'}${x(p.step).toFixed(1)},${y(p.value).toFixed(1)}`).join('');
    const path = line(points);
    const evalPath = line(evalPoints);
    return { x, y, path, evalPath, yTicks, xTicks: ticks(x0, x1).filter((s) => Number.isInteger(s) && s >= x0 && s <= x1) };
  }, [points, evalPoints]);

  if (!geometry) return <p className="text-sm text-muted-foreground">{t('run.noLossYet')}</p>;
  const { x, y, path, evalPath, yTicks, xTicks } = geometry;
  const evalAt = new Map(evalPoints.map((p) => [p.step, p.value]));

  const onMove = (event: PointerEvent<SVGSVGElement>) => {
    const box = svg.current?.getBoundingClientRect();
    if (!box) return;
    const at = ((event.clientX - box.left) / box.width) * WIDTH;
    let nearest = 0;
    points.forEach((p, i) => {
      if (Math.abs(x(p.step) - at) < Math.abs(x(points[nearest].step) - at)) nearest = i;
    });
    setHover(nearest);
  };

  const point = hover === null ? null : points[hover];

  return (
    <div className="space-y-2">
      <div className="relative">
        <svg
          ref={svg}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="w-full h-auto touch-none"
          role="img"
          aria-label={t('run.lossChartLabel', { count: points.length })}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        >
          {yTicks.map((v) => (
            <g key={v}>
              <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(v)} y2={y(v)} className="stroke-border" strokeWidth={1} />
              <text x={PAD.left - 8} y={y(v)} textAnchor="end" dominantBaseline="middle" className="fill-muted-foreground text-[11px]">
                {format(v)}
              </text>
            </g>
          ))}
          {xTicks.map((s) => (
            <text key={s} x={x(s)} y={HEIGHT - 8} textAnchor="middle" className="fill-muted-foreground text-[11px]">
              {s}
            </text>
          ))}
          <path d={path} fill="none" className="stroke-primary" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {evalPoints.length > 0 && (
            <g>
              <path d={evalPath} fill="none" className="stroke-foreground" strokeWidth={2} strokeDasharray="6 4" strokeLinejoin="round" />
              {evalPoints.map((p) => (
                <circle key={p.step} cx={x(p.step)} cy={y(p.value)} r={4} className="fill-foreground stroke-background" strokeWidth={2}>
                  <title>{`${t('run.evalLoss')} ${format(p.value)} · ${t('run.step')} ${p.step}`}</title>
                </circle>
              ))}
            </g>
          )}
          {point && (
            <g>
              <line x1={x(point.step)} x2={x(point.step)} y1={PAD.top} y2={HEIGHT - PAD.bottom} className="stroke-muted-foreground" strokeWidth={1} strokeDasharray="3 3" />
              <circle cx={x(point.step)} cy={y(point.value)} r={4} className="fill-primary stroke-background" strokeWidth={2} />
            </g>
          )}
          {/* The whole plot is the hit target, not the 2px line */}
          <rect x={PAD.left} y={PAD.top} width={WIDTH - PAD.left - PAD.right} height={HEIGHT - PAD.top - PAD.bottom} fill="transparent" />
        </svg>
        {point && (
          <div
            className="pointer-events-none absolute top-1 rounded-md border bg-popover px-2 py-1 text-xs shadow-sm"
            style={{ left: `${(x(point.step) / WIDTH) * 100}%`, transform: 'translateX(-50%)' }}
          >
            <span className="text-muted-foreground">{t('run.step')} {point.step}</span>{' '}
            <span className="font-medium">{t('run.loss')} {format(point.value)}</span>
            {evalAt.has(point.step) && <span className="font-medium"> · {t('run.evalLoss')} {format(evalAt.get(point.step) as number)}</span>}
          </div>
        )}
      </div>
      {evalPoints.length > 0 && (
        <div className="flex gap-4 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <svg width="18" height="6" aria-hidden="true"><line x1="0" x2="18" y1="3" y2="3" className="stroke-primary" strokeWidth={2} /></svg>
            {t('run.trainLoss')}
          </span>
          <span className="inline-flex items-center gap-1">
            <svg width="18" height="6" aria-hidden="true"><line x1="0" x2="18" y1="3" y2="3" className="stroke-foreground" strokeWidth={2} strokeDasharray="6 4" /></svg>
            {t('run.evalLoss')}
          </span>
        </div>
      )}
      <details className="text-sm">
        <summary className="cursor-pointer text-muted-foreground">{t('run.lossTable')}</summary>
        <div className="max-h-48 overflow-auto mt-2">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground">
                <th className="text-left font-normal">{t('run.step')}</th>
                <th className="text-right font-normal">{t('run.loss')}</th>
                {evalPoints.length > 0 && <th className="text-right font-normal">{t('run.evalLoss')}</th>}
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.step}>
                  <td>{p.step}</td>
                  <td className="text-right tabular-nums">{format(p.value)}</td>
                  {evalPoints.length > 0 && (
                    <td className="text-right tabular-nums">{evalAt.has(p.step) ? format(evalAt.get(p.step) as number) : ''}</td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

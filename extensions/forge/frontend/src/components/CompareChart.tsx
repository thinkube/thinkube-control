/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useMemo, useRef, useState, type PointerEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { Point } from '@/lib/forge';

const WIDTH = 640;
const HEIGHT = 260;
const PAD = { top: 12, right: 16, bottom: 28, left: 52 };

// The categorical slots in their fixed order, light and dark: a run keeps its colour
export const SERIES = [
  { stroke: 'stroke-[#2a78d6] dark:stroke-[#3987e5]', fill: 'fill-[#2a78d6] dark:fill-[#3987e5]', swatch: 'bg-[#2a78d6] dark:bg-[#3987e5]' },
  { stroke: 'stroke-[#eb6834] dark:stroke-[#d95926]', fill: 'fill-[#eb6834] dark:fill-[#d95926]', swatch: 'bg-[#eb6834] dark:bg-[#d95926]' },
  { stroke: 'stroke-[#1baf7a] dark:stroke-[#199e70]', fill: 'fill-[#1baf7a] dark:fill-[#199e70]', swatch: 'bg-[#1baf7a] dark:bg-[#199e70]' },
  { stroke: 'stroke-[#eda100] dark:stroke-[#c98500]', fill: 'fill-[#eda100] dark:fill-[#c98500]', swatch: 'bg-[#eda100] dark:bg-[#c98500]' },
];

export interface Series {
  name: string;
  points: Point[];
}

function ticks(min: number, max: number): number[] {
  if (min === max) return [min];
  const raw = (max - min) / 4;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? raw;
  const out = [];
  for (let v = Math.floor(min / step) * step; v < max + step - step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

function format(v: number): string {
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toPrecision(3);
}

/** Several runs' curves of one metric on one axis, each in its slot's colour. */
export function CompareChart({ series }: { series: Series[] }) {
  const { t } = useTranslation();
  const svg = useRef<SVGSVGElement>(null);
  const [hoverStep, setHoverStep] = useState<number | null>(null);

  const geometry = useMemo(() => {
    const all = series.flatMap((s) => s.points);
    if (all.length === 0) return null;
    const x0 = Math.min(...all.map((p) => p.step));
    const x1 = Math.max(...all.map((p) => p.step));
    const yTicks = ticks(Math.min(0, ...all.map((p) => p.value)), Math.max(...all.map((p) => p.value)));
    const y0 = Math.min(0, yTicks[0]);
    const y1 = Math.max(yTicks[yTicks.length - 1], ...all.map((p) => p.value));
    const x = (s: number) => PAD.left + (x1 === x0 ? 0.5 : (s - x0) / (x1 - x0)) * (WIDTH - PAD.left - PAD.right);
    const y = (v: number) => HEIGHT - PAD.bottom - ((v - y0) / (y1 - y0 || 1)) * (HEIGHT - PAD.top - PAD.bottom);
    const paths = series.map((s) => s.points.map((p, i) => `${i ? 'L' : 'M'}${x(p.step).toFixed(1)},${y(p.value).toFixed(1)}`).join(''));
    const steps = [...new Set(all.map((p) => p.step))].sort((a, b) => a - b);
    return { x, y, paths, yTicks, steps, xTicks: ticks(x0, x1).filter((s) => Number.isInteger(s) && s >= x0 && s <= x1) };
  }, [series]);

  if (!geometry) return <p className="text-sm text-muted-foreground">{t('compare.noPoints')}</p>;
  const { x, y, paths, yTicks, steps, xTicks } = geometry;

  const onMove = (event: PointerEvent<SVGSVGElement>) => {
    const box = svg.current?.getBoundingClientRect();
    if (!box) return;
    const at = ((event.clientX - box.left) / box.width) * WIDTH;
    setHoverStep(steps.reduce((best, s) => (Math.abs(x(s) - at) < Math.abs(x(best) - at) ? s : best), steps[0]));
  };

  const valueAt = (s: Series, step: number) => s.points.find((p) => p.step === step)?.value;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
        {series.map((s, i) => (
          <span key={s.name} className="inline-flex items-center gap-1">
            <span className={`inline-block h-0.5 w-4 ${SERIES[i].swatch}`} />
            {s.name}
          </span>
        ))}
      </div>
      <div className="relative">
        <svg
          ref={svg}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="w-full h-auto touch-none"
          role="img"
          aria-label={t('compare.chartLabel', { count: series.length })}
          onPointerMove={onMove}
          onPointerLeave={() => setHoverStep(null)}
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
          {paths.map((d, i) => (
            <path key={series[i].name} d={d} fill="none" className={SERIES[i].stroke} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {hoverStep !== null && (
            <g>
              <line x1={x(hoverStep)} x2={x(hoverStep)} y1={PAD.top} y2={HEIGHT - PAD.bottom} className="stroke-muted-foreground" strokeWidth={1} strokeDasharray="3 3" />
              {series.map((s, i) => {
                const v = valueAt(s, hoverStep);
                return v === undefined ? null : (
                  <circle key={s.name} cx={x(hoverStep)} cy={y(v)} r={4} className={`${SERIES[i].fill} stroke-background`} strokeWidth={2} />
                );
              })}
            </g>
          )}
          <rect x={PAD.left} y={PAD.top} width={WIDTH - PAD.left - PAD.right} height={HEIGHT - PAD.top - PAD.bottom} fill="transparent" />
        </svg>
        {hoverStep !== null && (
          <div
            className="pointer-events-none absolute top-1 rounded-md border bg-popover px-2 py-1 text-xs shadow-sm space-y-0.5"
            style={{ left: `${(x(hoverStep) / WIDTH) * 100}%`, transform: 'translateX(-50%)' }}
          >
            <div className="text-muted-foreground">
              {t('run.step')} {hoverStep}
            </div>
            {series.map((s, i) => {
              const v = valueAt(s, hoverStep);
              return v === undefined ? null : (
                <div key={s.name} className="flex items-center gap-1">
                  <span className={`inline-block h-2 w-2 rounded-full ${SERIES[i].swatch}`} />
                  <span>{s.name}</span>
                  <span className="font-medium tabular-nums ml-auto pl-2">{format(v)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

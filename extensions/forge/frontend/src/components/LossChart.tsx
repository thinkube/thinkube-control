/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useMemo, useRef, useState, type PointerEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { Point } from '@/lib/forge';
import { SERIES } from '@/components/CompareChart';

/** The width drawn before the chart's box is measured. */
const START_WIDTH = 640;
const HEIGHT = 320;
const PAD = { top: 10, right: 16, bottom: 28, left: 52 };
export const SMOOTHING = [0, 0.6, 0.9, 0.97, 0.99];
const LOSS = SERIES[0];
const EVAL = SERIES[1];

/** About `count` round tick values covering min to max. */
function niceTicks(min: number, max: number, count: number): number[] {
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const raw = (max - min) / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const ratio = raw / power;
  const step = (ratio >= 7.5 ? 10 : ratio >= 3.5 ? 5 : ratio >= 1.5 ? 2 : 1) * power;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

function format(v: number): string {
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toPrecision(3);
}

function formatStep(s: number): string {
  return s >= 10000 ? `${(s / 1000).toFixed(s >= 100000 ? 0 : 1)}k` : String(Math.round(s));
}

/** The exponential moving average of the values, as TensorBoard smooths a curve. */
export function smooth(points: Point[], weight: number): Point[] {
  let last: number | null = null;
  return points.map((p) => {
    last = last === null ? p.value : last * weight + p.value * (1 - weight);
    return { step: p.step, value: last };
  });
}

/** At most the lowest and the highest point of each pixel column, in step order: a long run's
 * line keeps its spikes without one path segment per step. */
function thin(points: Point[], columns: number): Point[] {
  if (points.length <= columns * 2) return points;
  const size = points.length / columns;
  const out: Point[] = [];
  for (let c = 0; c < columns; c++) {
    const part = points.slice(Math.floor(c * size), Math.floor((c + 1) * size));
    if (part.length === 0) continue;
    let lo = part[0];
    let hi = part[0];
    for (const p of part) {
      if (p.value < lo.value) lo = p;
      if (p.value > hi.value) hi = p;
    }
    if (lo === hi) out.push(lo);
    else if (lo.step < hi.step) out.push(lo, hi);
    else out.push(hi, lo);
  }
  return out;
}

function nearest(points: Point[], step: number): Point | null {
  if (points.length === 0) return null;
  let lo = 0;
  let hi = points.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].step < step) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(points[lo - 1].step - step) <= Math.abs(points[lo].step - step)) lo--;
  return points[lo];
}

/** The training loss by step: the raw loss faint behind its smoothed line, the evaluation loss
 * as points, a crosshair with the values under the pointer, and the values as a table for
 * reading without the chart. */
export function LossChart({ points, evalPoints = [] }: { points: Point[]; evalPoints?: Point[] }) {
  const { t } = useTranslation();
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(START_WIDTH);
  const [weight, setWeight] = useState(0.9);
  const [hoverStep, setHoverStep] = useState<number | null>(null);
  const [tableOpen, setTableOpen] = useState(false);

  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setWidth(Math.round(entry.contentRect.width));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const smoothed = useMemo(() => (weight > 0 ? smooth(points, weight) : null), [points, weight]);

  const geometry = useMemo(() => {
    const all = [...points, ...evalPoints];
    if (all.length === 0) return null;
    const innerWidth = width - PAD.left - PAD.right;
    const innerHeight = HEIGHT - PAD.top - PAD.bottom;
    const xMax = Math.max(1, ...all.map((p) => p.step));
    let yMin = Math.min(...all.map((p) => p.value));
    let yMax = Math.max(...all.map((p) => p.value));
    const pad = (yMax - yMin) * 0.05 || Math.abs(yMax) * 0.05 || 1;
    yMin = yMin >= 0 ? Math.max(0, yMin - pad) : yMin - pad;
    yMax += pad;
    const yTicks = niceTicks(yMin, yMax, 5);
    yMin = Math.min(yMin, yTicks[0]);
    yMax = Math.max(yMax, yTicks[yTicks.length - 1]);
    // A tick label has the decimals its spacing needs, no more
    const decimals = yTicks.length > 1 ? Math.max(0, -Math.floor(Math.log10(yTicks[1] - yTicks[0]) + 1e-9)) : 2;
    const x = (s: number) => PAD.left + (s / xMax) * innerWidth;
    const y = (v: number) => PAD.top + innerHeight - ((v - yMin) / (yMax - yMin)) * innerHeight;
    const columns = Math.max(50, Math.floor(innerWidth));
    const line = (series: Point[]) =>
      series.map((p, i) => `${i ? 'L' : 'M'}${x(p.step).toFixed(1)},${y(p.value).toFixed(1)}`).join('');
    const xTicks = niceTicks(0, xMax, Math.max(2, Math.floor(innerWidth / 90))).filter((s) => s <= xMax);
    return {
      x, y, xMax, innerWidth, innerHeight, yTicks, decimals, xTicks,
      raw: line(thin(points, columns)),
      smooth: smoothed ? line(thin(smoothed, columns)) : null,
      evalPath: line(evalPoints),
    };
  }, [points, evalPoints, smoothed, width]);

  if (!geometry) return <p className="text-sm text-muted-foreground">{t('run.noLossYet')}</p>;
  const { x, y, xMax, innerWidth, innerHeight, yTicks, decimals, xTicks } = geometry;

  const onMove = (event: PointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    setHoverStep(((event.clientX - rect.left - PAD.left) / innerWidth) * xMax);
  };

  const raw = hoverStep === null ? null : nearest(points, hoverStep);
  const smoothAt = hoverStep === null || !smoothed ? null : nearest(smoothed, hoverStep);
  const anchor = raw ?? (hoverStep === null ? null : nearest(evalPoints, hoverStep));
  // An evaluation point is shown with the step under the pointer when it is within 6 pixels of it
  const evalNear = anchor ? nearest(evalPoints, anchor.step) : null;
  const evalShown = evalNear && anchor && Math.abs(evalNear.step - anchor.step) <= (xMax / innerWidth) * 6 ? evalNear : null;
  const lossDot = smoothAt ?? raw;
  const evalAt = new Map(evalPoints.map((p) => [p.step, p.value]));
  const tipLeft = anchor ? x(anchor.step) : 0;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {smoothed ? (
          <>
            <Swatch className={LOSS.stroke} label={t('run.smoothedLoss')} />
            <Swatch className={LOSS.stroke} opacity={0.3} label={t('run.trainLoss')} />
          </>
        ) : (
          <Swatch className={LOSS.stroke} label={t('run.trainLoss')} />
        )}
        {evalPoints.length > 0 && <Swatch className={EVAL.stroke} label={t('run.evalLoss')} />}
        <label className="ml-auto inline-flex items-center gap-2">
          {t('run.smoothing')}
          <select
            className="rounded-md border bg-background px-1 py-0.5 text-foreground"
            value={weight}
            onChange={(event) => setWeight(Number(event.target.value))}
          >
            {SMOOTHING.map((w) => (
              <option key={w} value={w}>{w === 0 ? t('run.smoothingNone') : w}</option>
            ))}
          </select>
        </label>
      </div>
      <div ref={box} className="relative">
        <svg
          width="100%"
          height={HEIGHT}
          viewBox={`0 0 ${width} ${HEIGHT}`}
          className="block touch-none overflow-visible"
          role="img"
          aria-label={t('run.lossChartLabel', { count: points.length })}
          onPointerMove={onMove}
          onPointerLeave={() => setHoverStep(null)}
        >
          {yTicks.map((v) => (
            <g key={v}>
              <line x1={PAD.left} x2={PAD.left + innerWidth} y1={y(v)} y2={y(v)} className="stroke-border" strokeWidth={1} />
              <text x={PAD.left - 8} y={y(v)} textAnchor="end" dominantBaseline="middle" className="fill-muted-foreground text-[11px] tabular-nums">
                {v.toFixed(decimals)}
              </text>
            </g>
          ))}
          {xTicks.map((s) => (
            <text key={s} x={x(s)} y={HEIGHT - 8} textAnchor="middle" className="fill-muted-foreground text-[11px] tabular-nums">
              {formatStep(s)}
            </text>
          ))}
          <path
            data-series="loss"
            d={geometry.raw}
            fill="none"
            className={LOSS.stroke}
            strokeWidth={geometry.smooth ? 1 : 2}
            strokeOpacity={geometry.smooth ? 0.3 : 1}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          {geometry.smooth && (
            <path data-series="smoothed" d={geometry.smooth} fill="none" className={LOSS.stroke} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          )}
          {evalPoints.length > 0 && (
            <g>
              <path data-series="eval" d={geometry.evalPath} fill="none" className={EVAL.stroke} strokeWidth={2} strokeLinejoin="round" />
              {evalPoints.map((p) => (
                <circle key={p.step} cx={x(p.step)} cy={y(p.value)} r={4} className={`${EVAL.fill} stroke-background`} strokeWidth={2} />
              ))}
            </g>
          )}
          {anchor && (
            <g>
              <line x1={x(anchor.step)} x2={x(anchor.step)} y1={PAD.top} y2={PAD.top + innerHeight} className="stroke-muted-foreground" strokeWidth={1} />
              {lossDot && <circle cx={x(lossDot.step)} cy={y(lossDot.value)} r={4} className={`${LOSS.fill} stroke-background`} strokeWidth={2} />}
              {evalShown && <circle cx={x(evalShown.step)} cy={y(evalShown.value)} r={5} className={`${EVAL.fill} stroke-background`} strokeWidth={2} />}
            </g>
          )}
          {/* The whole plot is the hit target, not the 2px line */}
          <rect x={PAD.left} y={PAD.top} width={innerWidth} height={innerHeight} fill="transparent" />
        </svg>
        {anchor && (
          <div
            className="pointer-events-none absolute rounded-md border bg-popover px-2 py-1 text-xs shadow-sm tabular-nums whitespace-nowrap"
            style={tipLeft > width * 0.6
              ? { right: `${width - tipLeft + 12}px`, top: PAD.top + 4 }
              : { left: `${tipLeft + 12}px`, top: PAD.top + 4 }}
          >
            <div className="font-medium">{t('run.step')} {anchor.step}</div>
            {raw && <div>{t('run.loss')} {format(raw.value)}</div>}
            {smoothAt && <div className="text-muted-foreground">{t('run.smoothed')} {format(smoothAt.value)}</div>}
            {evalShown && (
              <div>
                {t('run.evalLoss')} {format(evalShown.value)}
                {evalShown.step !== anchor.step && <span className="text-muted-foreground"> ({t('run.step')} {evalShown.step})</span>}
              </div>
            )}
          </div>
        )}
      </div>
      <details className="text-sm" onToggle={(event) => setTableOpen(event.currentTarget.open)}>
        <summary className="cursor-pointer text-muted-foreground">{t('run.lossTable')}</summary>
        {/* A long run has tens of thousands of steps: the rows are made only while the table is open */}
        {tableOpen && (
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
        )}
      </details>
    </div>
  );
}

function Swatch({ className, opacity = 1, label }: { className: string; opacity?: number; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <svg width="18" height="6" aria-hidden="true">
        <line x1="0" x2="18" y1="3" y2="3" className={className} strokeWidth={2} strokeOpacity={opacity} />
      </svg>
      {label}
    </span>
  );
}

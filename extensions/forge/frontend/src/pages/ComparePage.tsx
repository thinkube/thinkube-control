/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router-dom';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkErrorAlert } from 'thinkube-style/components/feedback';
import { TkRadioGroup, TkRadioGroupItem } from 'thinkube-style/components/forms-inputs';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import { CompareChart, SERIES } from '@/components/CompareChart';
import { failure, forge, type Point, type Progress, type Run } from '@/lib/forge';

type Metric = 'eval_loss' | 'loss';

function last(points: Point[] | undefined): string {
  return points && points.length ? points[points.length - 1].value.toPrecision(3) : '–';
}

function lowest(points: Point[] | undefined): string {
  return points && points.length ? Math.min(...points.map((p) => p.value)).toPrecision(3) : '–';
}

/** A run's decision as Meep²'s plan states it, such as its LoRA rank or learning rate. */
function setting(run: Run, decision: string): string {
  const plan = run.progress?.plan ?? run.plan;
  const found = plan?.decisions.find((d) => d.name === decision);
  return found ? String(found.value) : '–';
}

/** Up to four runs side by side: their curves on one axis, and what each trained on. */
export default function ComparePage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const ids = (params.get('runs') ?? '').split(',').filter(Boolean).slice(0, SERIES.length);
  const [runs, setRuns] = useState<{ run: Run; progress: Progress | null }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [metric, setMetric] = useState<Metric>('eval_loss');
  const key = ids.join(',');

  useEffect(() => {
    Promise.all(key.split(',').filter(Boolean).map(async (id) => ({ run: await forge.run(id), progress: await forge.progress(id) })))
      .then(setRuns)
      .catch((e) => setError(failure(e)));
  }, [key]);

  const series = runs.map(({ run, progress }) => ({ name: run.name, points: progress?.metrics[metric] ?? [] }));

  return (
    <TkPageWrapper>
      <div>
        <Link to="/" className="text-sm text-muted-foreground inline-flex items-center gap-1">
          <ArrowLeft className="h-4 w-4" />
          {t('nav.runs')}
        </Link>
        <h1 className="text-2xl font-bold mt-2">{t('compare.title')}</h1>
      </div>
      {error && <TkErrorAlert title={t('compare.loadFailed')}>{error}</TkErrorAlert>}
      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{t(metric === 'eval_loss' ? 'run.evalLoss' : 'run.trainLoss')}</TkCardTitle>
          <TkCardDescription>{t('compare.help')}</TkCardDescription>
        </TkCardHeader>
        <TkCardContent className="space-y-4">
          <TkRadioGroup value={metric} onValueChange={(v) => setMetric(v as Metric)} className="flex gap-4">
            <label className="flex items-center gap-2 text-sm">
              <TkRadioGroupItem value="eval_loss" />
              {t('run.evalLoss')}
            </label>
            <label className="flex items-center gap-2 text-sm">
              <TkRadioGroupItem value="loss" />
              {t('run.trainLoss')}
            </label>
          </TkRadioGroup>
          <CompareChart series={series} />
        </TkCardContent>
      </TkCard>
      <TkCard>
        <TkCardContent className="pt-6 overflow-x-auto">
          <TkTable>
            <TkTableHeader>
              <TkTableRow>
                <TkTableHead>{t('compare.run')}</TkTableHead>
                <TkTableHead>{t('compare.dataset')}</TkTableHead>
                <TkTableHead>{t('compare.method')}</TkTableHead>
                <TkTableHead>{t('compare.steps')}</TkTableHead>
                <TkTableHead className="text-right">{t('compare.lastLoss')}</TkTableHead>
                <TkTableHead className="text-right">{t('compare.lastEval')}</TkTableHead>
                <TkTableHead className="text-right">{t('compare.bestEval')}</TkTableHead>
              </TkTableRow>
            </TkTableHeader>
            <TkTableBody>
              {runs.map(({ run, progress }, i) => (
                <TkTableRow key={run.id}>
                  <TkTableCell>
                    <span className="inline-flex items-center gap-2">
                      <span className={`inline-block h-2 w-2 rounded-full ${SERIES[i].swatch}`} />
                      <Link className="underline" to={`/runs/${run.id}`}>
                        {run.name}
                      </Link>
                    </span>
                  </TkTableCell>
                  <TkTableCell>{run.dataset ? `${run.dataset.name} v${run.dataset_version} · ${run.dataset.train.split}` : '–'}</TkTableCell>
                  <TkTableCell>
                    {t(`method.${run.method}`)}
                    {` · r${setting(run, 'rank')} · lr ${setting(run, 'learning_rate')}`}
                  </TkTableCell>
                  <TkTableCell className="tabular-nums">{progress?.metrics.loss.length ?? 0}</TkTableCell>
                  <TkTableCell className="text-right tabular-nums">{last(progress?.metrics.loss)}</TkTableCell>
                  <TkTableCell className="text-right tabular-nums">{last(progress?.metrics.eval_loss)}</TkTableCell>
                  <TkTableCell className="text-right tabular-nums">{lowest(progress?.metrics.eval_loss)}</TkTableCell>
                </TkTableRow>
              ))}
            </TkTableBody>
          </TkTable>
        </TkCardContent>
      </TkCard>
    </TkPageWrapper>
  );
}

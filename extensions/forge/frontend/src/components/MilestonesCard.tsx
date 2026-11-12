/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useTranslation } from 'react-i18next';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import type { MilestoneRecord, RunMilestones } from '@/lib/forge';

/** A run's milestones: each one's step and its share of the run, its scores by group, and the next one. */
export function MilestonesCard({ settings, made, next }: {
  settings: RunMilestones;
  made: MilestoneRecord[];
  next: number | null | undefined;
}) {
  const { t } = useTranslation();
  const groups = [...new Set(made.flatMap((m) => Object.keys(m.scores ?? {})))].sort();
  const columns = groups.flatMap((g) => settings.metrics.map((metric) => ({ group: g, metric })));
  const evalSet = settings.eval;

  return (
    <TkCard>
      <TkCardHeader>
        <TkCardTitle>{t('milestones.title')}</TkCardTitle>
        <TkCardDescription>
          {t('milestones.every', { pct: +(settings.every * 100).toFixed(2) })}
          {evalSet
            ? ` · ${t('milestones.scoredOn', {
              name: evalSet.name, version: evalSet.version, split: evalSet.split, rows: evalSet.rows.toLocaleString(),
              metrics: settings.metrics.join(', '),
            })}`
            : ` · ${t('milestones.adapterOnly')}`}
        </TkCardDescription>
      </TkCardHeader>
      <TkCardContent className="space-y-2">
        {made.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('milestones.none')}</p>
        ) : (
          <TkTable>
            <TkTableHeader>
              <TkTableRow>
                <TkTableHead>{t('milestones.step')}</TkTableHead>
                <TkTableHead>{t('milestones.share')}</TkTableHead>
                {columns.map((c) => (
                  <TkTableHead key={`${c.group}/${c.metric}`}>
                    {groups.length > 1 || c.group !== 'all' ? `${c.group} ${c.metric}` : c.metric}
                  </TkTableHead>
                ))}
                <TkTableHead>{t('milestones.seconds')}</TkTableHead>
              </TkTableRow>
            </TkTableHeader>
            <TkTableBody>
              {made.map((m) => (
                <TkTableRow key={m.step}>
                  <TkTableCell>{m.step.toLocaleString()}</TkTableCell>
                  <TkTableCell>{Math.round((m.step / m.planned_steps) * 100)}%</TkTableCell>
                  {columns.map((c) => (
                    <TkTableCell key={`${c.group}/${c.metric}`}>
                      {m.scores?.[c.group]?.[c.metric]?.toFixed(2) ?? '–'}
                    </TkTableCell>
                  ))}
                  <TkTableCell>{Math.round(m.seconds)}</TkTableCell>
                </TkTableRow>
              ))}
            </TkTableBody>
          </TkTable>
        )}
        {next != null && <p className="text-xs text-muted-foreground">{t('milestones.next', { step: next.toLocaleString() })}</p>}
      </TkCardContent>
    </TkCard>
  );
}

/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useTranslation } from 'react-i18next';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import { epochMetrics, type EpochScores } from '@/lib/forge';

/** A share from 0 to 1 as a percentage with two decimals */
function percent(value: number | undefined): string {
  return value === undefined ? '–' : `${(value * 100).toFixed(2)}`;
}

/**
 * A tagging run's scores at the end of each epoch on its evaluation data: each task's accuracy and the parser's
 * UAS and LAS, in percent. The epoch with the best score (the mean of the accuracies and LAS, which Meep²'s keep
 * "best" ranks by) is marked.
 */
export function EpochScoresCard({ records, epochs }: { records: EpochScores[]; epochs: number | null | undefined }) {
  const { t } = useTranslation();
  const metrics = epochMetrics(records);
  const best = records.reduce<EpochScores | null>(
    (top, r) => (r.score !== undefined && (top === null || r.score > (top.score ?? -1)) ? r : top), null);

  return (
    <TkCard>
      <TkCardHeader>
        <TkCardTitle>{t('epochScores.title')}</TkCardTitle>
        <TkCardDescription>{t('epochScores.description')}</TkCardDescription>
      </TkCardHeader>
      <TkCardContent className="space-y-2">
        {records.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('epochScores.none')}</p>
        ) : (
          <TkTable>
            <TkTableHeader>
              <TkTableRow>
                <TkTableHead>{t('epochScores.epoch')}</TkTableHead>
                <TkTableHead>{t('epochScores.step')}</TkTableHead>
                {metrics.map((m) => <TkTableHead key={m}>{m.toUpperCase()}</TkTableHead>)}
                <TkTableHead>{t('epochScores.score')}</TkTableHead>
              </TkTableRow>
            </TkTableHeader>
            <TkTableBody>
              {records.map((r) => (
                <TkTableRow key={r.epoch} data-best={r === best ? 'true' : undefined}
                  className={r === best ? 'font-semibold' : undefined}>
                  <TkTableCell>{epochs ? `${r.epoch} / ${epochs}` : r.epoch}</TkTableCell>
                  <TkTableCell>{r.step.toLocaleString()}</TkTableCell>
                  {metrics.map((m) => <TkTableCell key={m}>{percent(r[m])}</TkTableCell>)}
                  <TkTableCell>{percent(r.score)}</TkTableCell>
                </TkTableRow>
              ))}
            </TkTableBody>
          </TkTable>
        )}
        {best && <p className="text-xs text-muted-foreground">{t('epochScores.best', { epoch: best.epoch })}</p>}
      </TkCardContent>
    </TkCard>
  );
}

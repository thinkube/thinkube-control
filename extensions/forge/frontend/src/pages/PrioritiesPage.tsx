/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkErrorAlert, TkInfoAlert, TkLoader } from 'thinkube-style/components/feedback';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { PrioritySelect } from '@/components/PrioritySelect';
import { failure, forge, type KindPriority } from '@/lib/forge';

/** The priority of each kind of work on a GPU node, which the queue compares with a run's. */
export default function PrioritiesPage() {
  const { t } = useTranslation();
  const [kinds, setKinds] = useState<KindPriority[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    forge.priorities().then(setKinds).catch((e) => setError(failure(e)));
  }, []);

  const change = async (kind: string, priority: number) => {
    try {
      setKinds(await forge.setPriorities({ [kind]: priority }));
    } catch (e) {
      toast.error(failure(e));
    }
  };

  return (
    <TkPageWrapper>
      <div>
        <h1 className="text-2xl font-bold">{t('priority.pageTitle')}</h1>
        <p className="text-muted-foreground mt-1">{t('priority.subtitle')}</p>
      </div>
      {error && <TkErrorAlert>{error}</TkErrorAlert>}
      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{t('priority.rule')}</TkCardTitle>
          <TkCardDescription>{t('priority.ruleHelp')}</TkCardDescription>
        </TkCardHeader>
        <TkCardContent className="space-y-4">
          {kinds === null ? (
            <TkLoader />
          ) : (
            <div className="overflow-x-auto">
              <TkTable>
                <TkTableHeader>
                  <TkTableRow>
                    <TkTableHead>{t('priority.kind')}</TkTableHead>
                    <TkTableHead>{t('priority.title')}</TkTableHead>
                    <TkTableHead>{t('priority.default')}</TkTableHead>
                    <TkTableHead>{t('priority.stoppedBy')}</TkTableHead>
                  </TkTableRow>
                </TkTableHeader>
                <TkTableBody>
                  {kinds.map((k) => (
                    <TkTableRow key={k.kind}>
                      <TkTableCell className="font-medium">{t(`priority.kinds.${k.kind}`)}</TkTableCell>
                      <TkTableCell>
                        <PrioritySelect value={k.priority} label={t('priority.of', { name: t(`priority.kinds.${k.kind}`) })} onChange={(p) => change(k.kind, p)} />
                      </TkTableCell>
                      <TkTableCell className="text-sm">{t(`priority.value.${k.default}`)}</TkTableCell>
                      <TkTableCell className="text-sm text-muted-foreground">{t(`priority.stop.${k.kind}`)}</TkTableCell>
                    </TkTableRow>
                  ))}
                </TkTableBody>
              </TkTable>
            </div>
          )}
          <TkInfoAlert>{t('priority.trainingMode')}</TkInfoAlert>
        </TkCardContent>
      </TkCard>
    </TkPageWrapper>
  );
}

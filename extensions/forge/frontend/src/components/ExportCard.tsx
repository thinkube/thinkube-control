/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState } from 'react';
import { PackageOpen } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { TkLoadingButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback';
import { TkLabel, TkSelect, TkSelectContent, TkSelectItem, TkSelectTrigger, TkSelectValue } from 'thinkube-style/components/forms-inputs';
import { RunStatusBadge } from '@/components/RunStatusBadge';
import { failure, forge, OPEN_STATES, type GpuNode, type Run } from '@/lib/forge';

/** The run's model as a GGUF file that llama.cpp serves: converted by a notebook job without a GPU. */
export function ExportCard({ run, nodes, onChange }: { run: Run; nodes: GpuNode[]; onChange: (run: Run) => void }) {
  const { t } = useTranslation();
  const [quantization, setQuantization] = useState<'Q8_0' | 'F16'>('Q8_0');
  const [node, setNode] = useState(run.node);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const exported = run.export;
  const canStart = !exported || ['error', 'cancelled', 'lost'].includes(exported.status);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      onChange(await forge.exportRun(run.id, quantization, node));
    } catch (e) {
      setError(failure(e));
    } finally {
      setStarting(false);
    }
  };

  return (
    <TkCard>
      <TkCardHeader>
        <TkCardTitle>{t('export.title')}</TkCardTitle>
        <TkCardDescription>{t('export.help')}</TkCardDescription>
      </TkCardHeader>
      <TkCardContent className="space-y-4">
        {exported && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <RunStatusBadge status={exported.status} />
            <span className="break-all">{exported.model_id}</span>
            <span className="text-muted-foreground">
              {exported.quantization} · {exported.node}
            </span>
          </div>
        )}
        {exported?.error && <TkErrorAlert title={t('export.failed')}>{exported.error}</TkErrorAlert>}
        {exported && OPEN_STATES.includes(exported.status) && <TkInfoAlert>{t('export.running')}</TkInfoAlert>}
        {exported?.status === 'completed' && <TkInfoAlert>{t('export.done', { id: exported.model_id })}</TkInfoAlert>}
        {canStart && (
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <TkLabel>{t('export.quantization')}</TkLabel>
              <TkSelect value={quantization} onValueChange={(v) => setQuantization(v as 'Q8_0' | 'F16')}>
                <TkSelectTrigger className="w-56">
                  <TkSelectValue />
                </TkSelectTrigger>
                <TkSelectContent>
                  <TkSelectItem value="Q8_0">{t('export.q8')}</TkSelectItem>
                  <TkSelectItem value="F16">{t('export.f16')}</TkSelectItem>
                </TkSelectContent>
              </TkSelect>
            </div>
            <div className="space-y-1">
              <TkLabel>{t('export.node')}</TkLabel>
              <TkSelect value={node} onValueChange={setNode}>
                <TkSelectTrigger className="w-48">
                  <TkSelectValue />
                </TkSelectTrigger>
                <TkSelectContent>
                  {nodes.map((n) => (
                    <TkSelectItem key={n.name} value={n.name}>
                      {n.name}
                    </TkSelectItem>
                  ))}
                </TkSelectContent>
              </TkSelect>
            </div>
            <TkLoadingButton loading={starting} onClick={start}>
              <PackageOpen className="h-4 w-4" />
              {t('export.start')}
            </TkLoadingButton>
          </div>
        )}
        {error && <TkErrorAlert title={t('export.failed')}>{error}</TkErrorAlert>}
      </TkCardContent>
    </TkCard>
  );
}

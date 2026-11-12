/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState } from 'react';
import { Cpu } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { TkLoadingButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback';
import {
  TkInput,
  TkLabel,
  TkSelect,
  TkSelectContent,
  TkSelectItem,
  TkSelectTrigger,
  TkSelectValue,
} from 'thinkube-style/components/forms-inputs';
import { RunStatusBadge } from '@/components/RunStatusBadge';
import { failure, forge, OPEN_STATES, type GpuNode, type Run } from '@/lib/forge';

const DEFAULT_ROWS = 512;

/** The run's model in NVFP4 for vLLM on Blackwell GPUs: quantized by a notebook job with one GPU of such a node. */
export function Nvfp4Card({ run, nodes, onChange }: { run: Run; nodes: GpuNode[]; onChange: (run: Run) => void }) {
  const { t } = useTranslation();
  const capable = nodes.filter((n) => n.nvfp4);
  // The nodes arrive after the card is drawn: until a node is chosen, the run's own node when it computes NVFP4, else the first that does
  const [picked, setNode] = useState('');
  const node = picked || (capable.some((n) => n.name === run.node) ? run.node : (capable[0]?.name ?? ''));
  const [rows, setRows] = useState(DEFAULT_ROWS);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const exported = run.nvfp4;
  const canStart = !exported || ['error', 'cancelled', 'lost'].includes(exported.status);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      onChange(await forge.exportNvfp4(run.id, node, rows));
    } catch (e) {
      setError(failure(e));
    } finally {
      setStarting(false);
    }
  };

  return (
    <TkCard>
      <TkCardHeader>
        <TkCardTitle>{t('nvfp4.title')}</TkCardTitle>
        <TkCardDescription>{t('nvfp4.help')}</TkCardDescription>
      </TkCardHeader>
      <TkCardContent className="space-y-4">
        {exported && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <RunStatusBadge status={exported.status} />
            <span className="break-all">{exported.model_id}</span>
            <span className="text-muted-foreground">{t('nvfp4.state', { rows: exported.calibration_rows, node: exported.node })}</span>
          </div>
        )}
        {exported?.error && <TkErrorAlert title={t('nvfp4.failed')}>{exported.error}</TkErrorAlert>}
        {exported && OPEN_STATES.includes(exported.status) && <TkInfoAlert>{t('nvfp4.running')}</TkInfoAlert>}
        {exported?.status === 'completed' && <TkInfoAlert>{t('nvfp4.done', { id: exported.model_id })}</TkInfoAlert>}
        {canStart &&
          (capable.length === 0 ? (
            <TkInfoAlert>{t('nvfp4.noNode')}</TkInfoAlert>
          ) : (
            <div className="flex flex-wrap items-end gap-3">
              <div className="space-y-1">
                <TkLabel>{t('nvfp4.node')}</TkLabel>
                <TkSelect value={node} onValueChange={setNode}>
                  <TkSelectTrigger className="w-48">
                    <TkSelectValue />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {capable.map((n) => (
                      <TkSelectItem key={n.name} value={n.name}>
                        {n.name} · {n.compute_capability}
                      </TkSelectItem>
                    ))}
                  </TkSelectContent>
                </TkSelect>
              </div>
              <div className="space-y-1">
                <TkLabel htmlFor="nvfp4-rows">{t('nvfp4.rows')}</TkLabel>
                <TkInput
                  id="nvfp4-rows"
                  className="w-32"
                  type="number"
                  min={16}
                  max={8192}
                  step={64}
                  value={rows}
                  onChange={(e) => setRows(Number(e.target.value))}
                />
              </div>
              <TkLoadingButton loading={starting} disabled={!node} onClick={start}>
                <Cpu className="h-4 w-4" />
                {t('nvfp4.start')}
              </TkLoadingButton>
              <p className="w-full text-xs text-muted-foreground">{t('nvfp4.rowsHelp')}</p>
            </div>
          ))}
        {error && <TkErrorAlert title={t('nvfp4.failed')}>{error}</TkErrorAlert>}
      </TkCardContent>
    </TkCard>
  );
}

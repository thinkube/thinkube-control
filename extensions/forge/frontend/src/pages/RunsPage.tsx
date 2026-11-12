/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowDown, ArrowUp, CalendarClock, Gauge, GitCompare, Pause, Play, Plus, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { TkBadge, TkButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkErrorAlert, TkInfoAlert, TkLoader, TkWarningAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { TkCheckbox } from 'thinkube-style/components/forms-inputs';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import { PrioritySelect } from '@/components/PrioritySelect';
import { RunStateBadge } from '@/components/RunStateBadge';
import { TrainingProgress } from '@/components/TrainingProgress';
import { failure, forge, RUN_STATES_OPEN, type QueueNode, type Run, type SlotWindow, type WorkerStatus } from '@/lib/forge';

const POLL_MS = 10000;

function when(window: SlotWindow): string {
  const start = new Date(window.start);
  const end = new Date(window.end);
  return `${start.toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })} – ${end.toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })}`;
}

function NodeQueue({ node, onChange }: { node: QueueNode; onChange: () => void }) {
  const { t } = useTranslation();
  const waiting = node.runs.filter((r) => r.status === 'queued' || r.status === 'paused');

  const act = async (action: () => Promise<unknown>) => {
    try {
      await action();
      onChange();
    } catch (e) {
      toast.error(failure(e));
    }
  };

  const move = (run: Run, by: number) => {
    const ids = waiting.map((r) => r.id);
    const at = ids.indexOf(run.id);
    const to = at + by;
    if (to < 0 || to >= ids.length) return;
    [ids[at], ids[to]] = [ids[to], ids[at]];
    act(() => forge.reorder(node.node, ids));
  };

  return (
    <TkCard>
      <TkCardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <TkCardTitle>{node.node}</TkCardTitle>
          {node.training_mode && <TkBadge status="active">{t('queue.trainingMode')}</TkBadge>}
        </div>
        <TkCardDescription>
          {node.slot
            ? t('queue.slotNow', { window: when(node.slot) })
            : node.next_slot
              ? t('queue.slotNext', { window: when(node.next_slot) })
              : t('queue.noSlot')}
        </TkCardDescription>
      </TkCardHeader>
      <TkCardContent className="space-y-3">
        {node.note && <TkInfoAlert>{node.note}</TkInfoAlert>}
        {node.runs.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('queue.empty')}</p>
        ) : (
          <div className="overflow-x-auto">
            <TkTable>
              <TkTableHeader>
                <TkTableRow>
                  <TkTableHead>{t('queue.position')}</TkTableHead>
                  <TkTableHead>{t('runs.name')}</TkTableHead>
                  <TkTableHead>{t('runs.status')}</TkTableHead>
                  <TkTableHead>{t('priority.title')}</TkTableHead>
                  <TkTableHead>{t('queue.when')}</TkTableHead>
                  <TkTableHead>{t('queue.progress')}</TkTableHead>
                  <TkTableHead>
                    <span className="sr-only">{t('queue.actions')}</span>
                  </TkTableHead>
                </TkTableRow>
              </TkTableHeader>
              <TkTableBody>
                {node.runs.map((run) => {
                  const at = waiting.findIndex((r) => r.id === run.id);
                  return (
                    <TkTableRow key={run.id}>
                      <TkTableCell className="tabular-nums">{run.position ?? '–'}</TkTableCell>
                      <TkTableCell>
                        <Link className="font-medium hover:underline" to={`/runs/${run.id}`}>
                          {run.name}
                        </Link>
                        {run.waiting && <div className="text-xs text-muted-foreground">{run.waiting}</div>}
                        {run.error && <div className="text-xs text-destructive">{run.error}</div>}
                      </TkTableCell>
                      <TkTableCell>
                        <RunStateBadge state={run.status} preemptedBy={run.preempted_by} />
                      </TkTableCell>
                      <TkTableCell>
                        {['preprocessing', 'planning', 'planned', 'queued', 'paused'].includes(run.status) ? (
                          <PrioritySelect
                            value={run.priority}
                            label={t('priority.of', { name: run.name })}
                            onChange={(p) => act(() => forge.setPriority(run.id, p))}
                          />
                        ) : (
                          <span className="text-sm">{t(`priority.value.${run.priority}`)}</span>
                        )}
                      </TkTableCell>
                      <TkTableCell className="text-sm">
                        {t(`queue.whenValue.${run.when}`)}
                        {run.across_slots && <div className="text-xs text-muted-foreground">{t('queue.acrossSlots')}</div>}
                      </TkTableCell>
                      <TkTableCell>
                        <TrainingProgress run={run} compact />
                      </TkTableCell>
                      <TkTableCell>
                        <div className="flex justify-end gap-1">
                          {at >= 0 && (
                            <>
                              <TkButton intent="ghost" size="icon" aria-label={t('queue.up', { name: run.name })} disabled={at === 0} onClick={() => move(run, -1)}>
                                <ArrowUp className="h-4 w-4" />
                              </TkButton>
                              <TkButton
                                intent="ghost"
                                size="icon"
                                aria-label={t('queue.down', { name: run.name })}
                                disabled={at === waiting.length - 1}
                                onClick={() => move(run, 1)}
                              >
                                <ArrowDown className="h-4 w-4" />
                              </TkButton>
                            </>
                          )}
                          {(run.status === 'queued' || run.status === 'running') && (
                            <TkButton intent="ghost" size="icon" aria-label={t('queue.pause', { name: run.name })} onClick={() => act(() => forge.pauseRun(run.id))}>
                              <Pause className="h-4 w-4" />
                            </TkButton>
                          )}
                          {run.status === 'paused' && (
                            <TkButton intent="ghost" size="icon" aria-label={t('queue.resume', { name: run.name })} onClick={() => act(() => forge.resumeRun(run.id))}>
                              <Play className="h-4 w-4" />
                            </TkButton>
                          )}
                          <TkButton intent="ghost" size="icon" aria-label={t('queue.cancel', { name: run.name })} onClick={() => act(() => forge.cancelRun(run.id))}>
                            <X className="h-4 w-4" />
                          </TkButton>
                        </div>
                      </TkTableCell>
                    </TkTableRow>
                  );
                })}
              </TkTableBody>
            </TkTable>
          </div>
        )}
      </TkCardContent>
    </TkCard>
  );
}

export default function RunsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [nodes, setNodes] = useState<QueueNode[]>([]);
  const [worker, setWorker] = useState<WorkerStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const toggle = (id: string, on: boolean) =>
    setChosen((current) => (on ? [...current, id].slice(-4) : current.filter((x) => x !== id)));

  const load = useCallback(() => {
    Promise.all([forge.runs(), forge.queue()])
      .then(([r, q]) => {
        setRuns(r);
        setNodes(q.nodes);
        setWorker(q.worker);
        setError(null);
      })
      .catch((e) => setError(failure(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // While the queue has work, the page follows it
  const open = runs?.some((r) => RUN_STATES_OPEN.includes(r.status));
  useEffect(() => {
    if (!open) return;
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [open, load]);

  return (
    <TkPageWrapper>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">{t('runs.title')}</h1>
          <p className="text-muted-foreground mt-1">{t('runs.subtitle')}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <TkButton intent="secondary" onClick={() => navigate('/slots')}>
            <CalendarClock className="h-4 w-4" />
            {t('nav.slots')}
          </TkButton>
          <TkButton intent="secondary" onClick={() => navigate('/priorities')}>
            <Gauge className="h-4 w-4" />
            {t('nav.priorities')}
          </TkButton>
          <TkButton intent="secondary" disabled={chosen.length < 2} onClick={() => navigate(`/compare?runs=${chosen.join(',')}`)}>
            <GitCompare className="h-4 w-4" />
            {t('runs.compare', { count: chosen.length })}
          </TkButton>
          <TkButton onClick={() => navigate('/runs/new')}>
            <Plus className="h-4 w-4" />
            {t('runs.new')}
          </TkButton>
        </div>
      </div>

      {error && <TkErrorAlert title={t('runs.loadFailed')}>{error}</TkErrorAlert>}
      {worker && !worker.connected && (
        <TkWarningAlert title={t('queue.notConnected')}>
          {t('queue.notConnectedHelp')}{' '}
          <Link className="underline" to="/slots">
            {t('nav.slots')}
          </Link>
        </TkWarningAlert>
      )}
      {worker?.connected && worker.error && <TkWarningAlert title={t('queue.workerProblem')}>{worker.error}</TkWarningAlert>}

      {nodes.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">{t('queue.title')}</h2>
          <div className="grid gap-4 xl:grid-cols-2">
            {nodes.map((n) => (
              <NodeQueue key={n.node} node={n} onChange={load} />
            ))}
          </div>
        </section>
      )}

      {runs === null && !error ? (
        <div className="flex justify-center py-12">
          <TkLoader size="lg" />
        </div>
      ) : runs && runs.length === 0 ? (
        <TkInfoAlert>{t('runs.none')}</TkInfoAlert>
      ) : runs ? (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">{t('runs.all')}</h2>
          <div className="overflow-x-auto">
            <TkTable>
              <TkTableHeader>
                <TkTableRow>
                  <TkTableHead>
                    <span className="sr-only">{t('runs.choose')}</span>
                  </TkTableHead>
                  <TkTableHead>{t('runs.name')}</TkTableHead>
                  <TkTableHead>{t('runs.baseModel')}</TkTableHead>
                  <TkTableHead>{t('runs.method')}</TkTableHead>
                  <TkTableHead>{t('runs.node')}</TkTableHead>
                  <TkTableHead>{t('runs.status')}</TkTableHead>
                  <TkTableHead>{t('queue.progress')}</TkTableHead>
                  <TkTableHead>{t('runs.started')}</TkTableHead>
                </TkTableRow>
              </TkTableHeader>
              <TkTableBody>
                {runs.map((run) => (
                  <TkTableRow key={run.id}>
                    <TkTableCell>
                      <TkCheckbox
                        aria-label={t('runs.chooseRun', { name: run.name })}
                        checked={chosen.includes(run.id)}
                        onCheckedChange={(on) => toggle(run.id, on === true)}
                      />
                    </TkTableCell>
                    <TkTableCell>
                      <Link className="font-medium hover:underline" to={`/runs/${run.id}`}>
                        {run.name}
                      </Link>
                      {run.model_id && <div className="text-xs text-muted-foreground">{run.model_id}</div>}
                    </TkTableCell>
                    <TkTableCell className="text-sm">{run.base_model}</TkTableCell>
                    <TkTableCell className="text-sm">{t(`method.${run.method}`)}</TkTableCell>
                    <TkTableCell className="text-sm">{run.node}</TkTableCell>
                    <TkTableCell>
                      <RunStateBadge state={run.status} preemptedBy={run.preempted_by} />
                    </TkTableCell>
                    <TkTableCell>
                      <TrainingProgress run={run} compact />
                    </TkTableCell>
                    <TkTableCell className="text-sm">{new Date(run.created_at).toLocaleString()}</TkTableCell>
                  </TkTableRow>
                ))}
              </TkTableBody>
            </TkTable>
          </div>
        </section>
      ) : null}
    </TkPageWrapper>
  );
}

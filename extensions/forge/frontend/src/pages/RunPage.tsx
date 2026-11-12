/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { ExternalLink, Pause, Play } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { TkBadge, TkButton, TkLoadingButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkCodeBlock, TkErrorAlert, TkInfoAlert, TkLoader } from 'thinkube-style/components/feedback';
import { TkSelect, TkSelectContent, TkSelectItem, TkSelectTrigger, TkSelectValue } from 'thinkube-style/components/forms-inputs';
import { TkControlledConfirmDialog } from 'thinkube-style/components/modals-overlays';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { ChatCompare } from '@/components/ChatCompare';
import { ExportCard } from '@/components/ExportCard';
import { Nvfp4Card } from '@/components/Nvfp4Card';
import { LossChart } from '@/components/LossChart';
import { MilestonesCard } from '@/components/MilestonesCard';
import { PlanCard } from '@/components/PlanCard';
import { RunStateBadge } from '@/components/RunStateBadge';
import { TrainingProgress } from '@/components/TrainingProgress';
import {
  cellText,
  failure,
  forge,
  OPEN_STATES,
  RUN_STATES_OPEN,
  type GpuNode,
  type ModelStatus,
  type Progress,
  type Run,
} from '@/lib/forge';

const RUN_POLL_MS = 5000;
const PROGRESS_POLL_MS = 10000;
const MODEL_MOVING = ['loading', 'unloading'];
const LOG_LINES = 60;

function last<T>(items: T[] | undefined): T | undefined {
  return items && items.length ? items[items.length - 1] : undefined;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}

export default function RunPage() {
  const { t } = useTranslation();
  const { runId = '' } = useParams();
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [progressError, setProgressError] = useState<string | null>(null);
  const [model, setModel] = useState<ModelStatus | null>(null);
  const [modelError, setModelError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<GpuNode[]>([]);
  const [loadNode, setLoadNode] = useState('');
  const [changing, setChanging] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const loadRun = useCallback(() => {
    forge
      .run(runId)
      .then((r) => {
        setRun(r);
        setError(null);
      })
      .catch((e) => setError(failure(e)));
  }, [runId]);

  const loadProgress = useCallback(() => {
    forge
      .progress(runId)
      .then((p) => {
        setProgress(p);
        setProgressError(null);
      })
      .catch((e) => setProgressError(failure(e)));
  }, [runId]);

  const loadModel = useCallback(() => {
    forge
      .model(runId)
      .then((m) => {
        setModel(m);
        setModelError(null);
      })
      .catch((e) => setModelError(failure(e)));
  }, [runId]);

  useEffect(() => {
    loadRun();
    loadProgress();
  }, [loadRun, loadProgress]);

  const open = run ? RUN_STATES_OPEN.includes(run.status) : false;
  const exporting = [run?.export, run?.nvfp4].some((e) => e && OPEN_STATES.includes(e.status));
  useEffect(() => {
    if (!exporting) return;
    const timer = setInterval(loadRun, RUN_POLL_MS);
    return () => clearInterval(timer);
  }, [exporting, loadRun]);
  useEffect(() => {
    if (!open) return;
    const runTimer = setInterval(loadRun, RUN_POLL_MS);
    const progressTimer = setInterval(loadProgress, PROGRESS_POLL_MS);
    return () => {
      clearInterval(runTimer);
      clearInterval(progressTimer);
    };
  }, [open, loadRun, loadProgress]);

  // The model exists once a run that registers has completed
  const hasModel = run?.register && run.status === 'completed';
  useEffect(() => {
    if (!hasModel) return;
    loadModel();
    forge.nodes().then(setNodes).catch((e) => setModelError(failure(e)));
  }, [hasModel, loadModel]);

  const moving = model ? MODEL_MOVING.includes(model.model.state) : false;
  useEffect(() => {
    if (!moving) return;
    const timer = setInterval(loadModel, RUN_POLL_MS);
    return () => clearInterval(timer);
  }, [moving, loadModel]);

  const cancel = async () => {
    try {
      setRun(await forge.cancelRun(runId));
    } catch (e) {
      toast.error(failure(e));
    } finally {
      setConfirmCancel(false);
    }
  };

  const act = async (action: (id: string) => Promise<Run>, message?: string) => {
    try {
      setRun(await action(runId));
      if (message) toast.info(message);
    } catch (e) {
      toast.error(failure(e));
    }
  };

  const changeModel = async (load: boolean) => {
    setChanging(true);
    try {
      const answer = load ? await forge.loadModel(runId, loadNode || null) : await forge.unloadModel(runId);
      toast.info(answer.message);
      loadModel();
    } catch (e) {
      toast.error(failure(e));
    } finally {
      setChanging(false);
    }
  };

  if (error && !run) {
    return (
      <TkPageWrapper>
        <TkErrorAlert title={t('run.loadFailed')}>{error}</TkErrorAlert>
      </TkPageWrapper>
    );
  }
  if (!run) {
    return (
      <TkPageWrapper>
        <div className="flex justify-center py-12">
          <TkLoader size="lg" />
        </div>
      </TkPageWrapper>
    );
  }

  const results = run.job?.results ?? [];
  const loss = progress?.metrics.loss ?? [];
  const logCell = last(results);
  // The plan the training runs with, once it has started; the plan job's before
  const plan = run.progress?.plan ?? run.plan;
  const log = logCell ? cellText(logCell).split('\n').slice(-LOG_LINES).join('\n') : '';

  return (
    <TkPageWrapper>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold">{run.name}</h1>
            <RunStateBadge state={run.status} preemptedBy={run.preempted_by} />
          </div>
          <p className="text-muted-foreground mt-1">
            {t('run.summary', { model: run.base_model, method: t(`method.${run.method}`), node: run.node })}
          </p>
          {run.job_spec && (
            <p className="text-muted-foreground text-sm">
              {t(`queue.whenValue.${run.when}`)}
              {run.across_slots ? ` · ${t('queue.acrossSlots')}` : ''}
              {run.position != null && (run.status === 'queued' || run.status === 'paused')
                ? ` · ${t('run.position', { position: run.position })}`
                : ''}
              {` · ${t('priority.title')} ${t(`priority.value.${run.priority}`)}`}
              {run.paused_by ? ` · ${t(`run.pausedBy.${run.paused_by}`, { run: run.preempted_by })}` : ''}
            </p>
          )}
          {run.dataset && (
            <p className="text-muted-foreground text-sm">
              {t('run.datasetLine', {
                name: run.dataset.name,
                version: run.dataset_version,
                split: run.dataset.train.split, count: run.dataset.train.rows })}
              {run.dataset.eval
                ? ` · ${t('run.evalOnSplit', { split: run.dataset.eval.split })}`
                : run.evaluation?.holdout
                  ? ` · ${t('run.evalOnHoldout', { pct: Math.round(run.evaluation.holdout * 100) })}`
                  : ''}
            </p>
          )}
        </div>
        <div className="flex gap-2">
          {progress && (
            <a href={progress.url} target="_blank" rel="noreferrer">
              <TkButton intent="secondary">
                <ExternalLink className="h-4 w-4" />
                {t('run.mlflow')}
              </TkButton>
            </a>
          )}
          {(run.status === 'queued' || run.status === 'running') && (
            <TkButton intent="secondary" onClick={() => act(forge.pauseRun, run.status === 'running' ? t('run.stopping') : undefined)}>
              <Pause className="h-4 w-4" />
              {t('run.pause')}
            </TkButton>
          )}
          {(run.status === 'paused' || (run.status === 'failed' && run.plan)) && (
            <TkButton intent="secondary" onClick={() => act(forge.resumeRun, t('run.resumed'))}>
              <Play className="h-4 w-4" />
              {t('run.resume')}
            </TkButton>
          )}
          {run.status === 'planned' && (
            <TkButton onClick={() => act((id) => forge.queueRun(id))}>{t('run.queue')}</TkButton>
          )}
          {open && (
            <TkButton intent="danger" onClick={() => setConfirmCancel(true)}>
              {t('run.cancel')}
            </TkButton>
          )}
        </div>
      </div>

      {run.waiting && <TkInfoAlert>{run.waiting}</TkInfoAlert>}
      {run.error && <TkErrorAlert title={t('run.failed')}>{run.error}</TkErrorAlert>}

      <div className="grid gap-4 lg:grid-cols-3">
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>{t('run.progressTitle')}</TkCardTitle>
            <TkCardDescription>{t('run.progressHelp')}</TkCardDescription>
          </TkCardHeader>
          <TkCardContent className="space-y-3">
            {run.progress || run.eta_seconds != null ? (
              <TrainingProgress run={run} />
            ) : (
              <p className="text-sm text-muted-foreground">
                {t(`run.waiting.${run.status}`, { node: run.status === 'preprocessing' ? run.preprocess?.node ?? '…' : run.node })}
              </p>
            )}
            {run.progress?.state && (
              <p className="text-xs text-muted-foreground">
                {t('run.meepState', { state: run.progress.state })}
                {run.progress.checkpoint ? ` · ${t('run.checkpoint', { path: run.progress.checkpoint })}` : ''}
              </p>
            )}
            {run.progress?.error && <TkErrorAlert>{run.progress.error}</TkErrorAlert>}
          </TkCardContent>
        </TkCard>

        <TkCard className="lg:col-span-2">
          <TkCardHeader>
            <TkCardTitle>{t('run.lossTitle')}</TkCardTitle>
            <TkCardDescription>{t('run.lossHelp')}</TkCardDescription>
          </TkCardHeader>
          <TkCardContent className="space-y-4">
            {progressError && <TkErrorAlert title={t('run.progressFailed')}>{progressError}</TkErrorAlert>}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label={t('run.loss')} value={last(loss)?.value.toPrecision(3) ?? '–'} />
              <Stat label={t('run.step')} value={last(loss)?.step.toString() ?? '–'} />
              <Stat label={t('run.epoch')} value={last(progress?.metrics.epoch)?.value.toFixed(2) ?? '–'} />
              <Stat label={t('run.learningRate')} value={last(progress?.metrics.learning_rate)?.value.toExponential(1) ?? '–'} />
            </div>
            <LossChart points={loss} evalPoints={progress?.metrics.eval_loss ?? []} />
          </TkCardContent>
        </TkCard>
      </div>

      {run.preprocess?.report && (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>{t('run.preprocessTitle')}</TkCardTitle>
            <TkCardDescription>{t('run.preprocessHelp', { node: run.preprocess.node })}</TkCardDescription>
          </TkCardHeader>
          <TkCardContent className="space-y-2">
            <p className="text-sm">{run.preprocess.report.decision.text}</p>
            <p className="text-xs text-muted-foreground">{run.preprocess.report.decision.reason}</p>
            <p className="text-xs text-muted-foreground">
              {run.preprocess.found ? t('run.preprocessFound') : t('run.preprocessBuilt')}
              {run.preprocess.key ? ` · ${t('run.preprocessKey', { key: run.preprocess.key })}` : ''}
            </p>
          </TkCardContent>
        </TkCard>
      )}

      {run.milestones && (
        <MilestonesCard settings={run.milestones} made={run.progress?.milestones ?? []} next={run.progress?.next_milestone} />
      )}

      {plan && <PlanCard plan={plan} run={run} title={run.progress?.plan ? t('run.trainingPlan') : t('plan.title')} />}

      {log && (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>{t('run.output')}</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            <TkCodeBlock maxHeight="max-h-80">{log}</TkCodeBlock>
          </TkCardContent>
        </TkCard>
      )}

      {run.register && (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>{t('run.model')}</TkCardTitle>
            <TkCardDescription>{run.model_id}</TkCardDescription>
          </TkCardHeader>
          <TkCardContent className="space-y-4">
            {!hasModel ? (
              <TkInfoAlert>{t('run.modelAfterRun')}</TkInfoAlert>
            ) : modelError ? (
              <TkErrorAlert title={t('run.modelFailed')}>{modelError}</TkErrorAlert>
            ) : !model ? (
              <TkLoader />
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="text-muted-foreground">{t('run.gatewayState')}</span>
                  <TkBadge status={model.model.state === 'available' ? 'healthy' : moving ? 'pending' : 'active'}>
                    {t(`modelState.${model.model.state}`, { defaultValue: model.model.state })}
                  </TkBadge>
                  {model.model.backend_id && <span className="text-muted-foreground">{model.model.backend_id}</span>}
                </div>
                {model.model.last_error && <TkErrorAlert>{model.model.last_error}</TkErrorAlert>}
                {model.model.state === 'available' ? (
                  <div className="space-y-2">
                    <TkInfoAlert>{t('run.modelServed', { id: run.model_id })}</TkInfoAlert>
                    <TkLoadingButton intent="secondary" loading={changing} onClick={() => changeModel(false)}>
                      {t('run.unload')}
                    </TkLoadingButton>
                  </div>
                ) : !moving ? (
                  <div className="flex flex-wrap items-end gap-2">
                    <div className="min-w-48">
                      <TkSelect value={loadNode} onValueChange={setLoadNode}>
                        <TkSelectTrigger>
                          <TkSelectValue placeholder={t('run.anyNode')} />
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
                    <TkLoadingButton loading={changing} onClick={() => changeModel(true)}>
                      {t('run.load')}
                    </TkLoadingButton>
                  </div>
                ) : null}
              </>
            )}
          </TkCardContent>
        </TkCard>
      )}

      {hasModel && <ExportCard run={run} nodes={nodes} onChange={setRun} />}
      {hasModel && <Nvfp4Card run={run} nodes={nodes} onChange={setRun} />}

      {hasModel && run.model_id && (
        <ChatCompare
          runId={run.id}
          tunedServed={model?.model.state === 'available'}
          ids={{
            tuned: run.model_id,
            base: run.base_model,
            gguf: run.export?.status === 'completed' ? run.export.model_id : undefined,
            nvfp4: run.nvfp4?.status === 'completed' ? run.nvfp4.model_id : undefined,
          }}
        />
      )}

      <TkControlledConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title={t('run.cancelTitle')}
        description={t('run.cancelConfirm')}
        confirmText={t('run.cancel')}
        cancelText={t('common.close')}
        variant="destructive"
        onConfirm={cancel}
      />
    </TkPageWrapper>
  );
}

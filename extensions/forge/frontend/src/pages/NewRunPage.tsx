/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ListOrdered, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { TkButton, TkLoadingButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkCodeBlock, TkErrorAlert, TkInfoAlert, TkLoader, TkWarningAlert } from 'thinkube-style/components/feedback';
import {
  TkInput,
  TkLabel,
  TkRadioGroup,
  TkRadioGroupItem,
  TkSelect,
  TkSelectContent,
  TkSelectItem,
  TkSelectTrigger,
  TkSelectValue,
  TkSwitch,
} from 'thinkube-style/components/forms-inputs';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { PlanCard } from '@/components/PlanCard';
import { PrioritySelect } from '@/components/PrioritySelect';
import { RunStateBadge } from '@/components/RunStateBadge';
import {
  failure,
  forge,
  type BaseModel,
  type Dataset,
  type DatasetSplit,
  type DatasetVersion,
  type DecisionValue,
  type GpuNode,
  type Method,
  type Preview,
  type Run,
  type When,
} from '@/lib/forge';

const PLAN_POLL_MS = 5000;
const NO_EVAL = '__none__';
const HOLDOUT = '__holdout__';

/** A dataset's own evaluation split, when it has one; else a share held out of the training split. */
function evalSplitOf(splits: DatasetSplit[], train: string): string {
  const other = splits.find((s) => s.name !== train && ['test', 'validation', 'eval'].includes(s.name));
  return other ? other.name : HOLDOUT;
}

/** Datasets lives in Thinkube Control, at control.<domain>. */
const DATASETS_URL = `${window.location.protocol}//control.${window.location.hostname.split('.').slice(1).join('.')}/x/datasets/`;

/** The id the model of a run gets, as the backend names it. */
function modelId(name: string): string {
  return `forge/${name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')}`;
}

/** The Plan step: the run waits for its plan, shows it, may be planned again, then is queued. */
function PlanStep({ run, onRun }: { run: Run; onRun: (run: Run) => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [queueing, setQueueing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [memory, setMemory] = useState(32);

  const setOwnMemory = async () => {
    try {
      onRun(await forge.setResources(run.id, memory, run.cpu_cores));
    } catch (e) {
      setError(failure(e));
    }
  };

  const replan = async (decisions: Record<string, DecisionValue>) => {
    try {
      onRun(await forge.replan(run.id, decisions));
    } catch (e) {
      setError(failure(e));
    }
  };

  const queue = async () => {
    setQueueing(true);
    try {
      const queued = await forge.queueRun(run.id);
      navigate(`/runs/${queued.id}`);
    } catch (e) {
      setError(failure(e));
    } finally {
      setQueueing(false);
    }
  };

  const chosen: Record<string, DecisionValue> = {
    ...((run.job_spec?.recipe as Record<string, DecisionValue>) ?? {}),
    ...((run.job_spec?.overrides as Record<string, DecisionValue>) ?? {}),
    ...(run.job_spec?.eval_every != null ? { eval_every: run.job_spec.eval_every as number } : {}),
  };

  return (
    <>
      <TkCard>
        <TkCardHeader>
          <div className="flex flex-wrap items-center gap-3">
            <TkCardTitle>{t('newRun.planStep')}</TkCardTitle>
            <RunStateBadge state={run.status} />
          </div>
          <TkCardDescription>{t('newRun.planHelp', { node: run.node })}</TkCardDescription>
        </TkCardHeader>
        <TkCardContent className="space-y-3">
          {run.status === 'preprocessing' && (
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <TkLoader />
              {run.preprocess?.node ? t('newRun.preprocessing', { node: run.preprocess.node }) : t('newRun.preprocessWaits')}
            </div>
          )}
          {run.status === 'planning' && (
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <TkLoader />
              {run.plan_job_id ? t('newRun.planning') : t('newRun.planWaits', { node: run.node })}
            </div>
          )}
          {run.waiting && <p className="text-sm text-muted-foreground">{run.waiting}</p>}
          {run.status === 'failed' && <TkErrorAlert title={t('newRun.planFailed')}>{run.error}</TkErrorAlert>}
          <p className="text-sm">
            <Link className="underline" to={`/runs/${run.id}`}>
              {t('newRun.openRun')}
            </Link>
          </p>
        </TkCardContent>
      </TkCard>
      {run.plan && <PlanCard key={JSON.stringify(run.job_spec)} plan={run.plan} run={run} chosen={chosen} onReplan={replan} />}
      {error && <TkErrorAlert>{error}</TkErrorAlert>}
      {run.status === 'planned' && run.training_memory_gb == null && (
        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <TkLabel htmlFor="own-memory">{t('newRun.memory')}</TkLabel>
            <TkInput id="own-memory" className="w-32" type="number" step="4" value={memory} onChange={(e) => setMemory(Number(e.target.value))} />
          </div>
          <TkButton intent="secondary" onClick={setOwnMemory}>
            {t('newRun.setMemory')}
          </TkButton>
        </div>
      )}
      {run.status === 'planned' && (
        <div>
          <TkLoadingButton loading={queueing} disabled={run.training_memory_gb == null} onClick={queue}>
            <ListOrdered className="h-4 w-4" />
            {t('newRun.queue', { when: t(`queue.whenValue.${run.when}`) })}
          </TkLoadingButton>
        </div>
      )}
    </>
  );
}

export default function NewRunPage() {
  const { t } = useTranslation();
  const [models, setModels] = useState<BaseModel[] | null>(null);
  const [datasets, setDatasets] = useState<Dataset[] | null>(null);
  const [nodes, setNodes] = useState<GpuNode[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState('');
  const [baseModel, setBaseModel] = useState('');
  const [datasetId, setDatasetId] = useState('');
  // The version the run trains on: the dataset's latest ready one unless another is picked
  const [datasetVersion, setDatasetVersion] = useState<number | null>(null);
  const [versions, setVersions] = useState<DatasetVersion[] | null>(null);
  const [trainSplit, setTrainSplit] = useState('train');
  const [evalChoice, setEvalChoice] = useState(HOLDOUT);
  const [holdoutPct, setHoldoutPct] = useState(5);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [method, setMethod] = useState<Method>('lora');
  const [node, setNode] = useState('');
  const [register, setRegister] = useState(true);
  const [length, setLength] = useState<'epochs' | 'steps'>('epochs');
  const [epochs, setEpochs] = useState(1);
  const [steps, setSteps] = useState(1000);
  const [when, setWhen] = useState<When>('now');
  const [acrossSlots, setAcrossSlots] = useState(false);
  const [priority, setPriority] = useState<number | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [memoryGb, setMemoryGb] = useState(32);
  const [cpuCores, setCpuCores] = useState(8);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  // Meep²'s milestones: how often, and the evaluation set they score with the scoring service
  const [milestonesOn, setMilestonesOn] = useState(false);
  const [milestonePct, setMilestonePct] = useState(2);
  const [evalSetId, setEvalSetId] = useState(NO_EVAL);
  const [evalSetVersions, setEvalSetVersions] = useState<DatasetVersion[] | null>(null);
  const [evalSetSplit, setEvalSetSplit] = useState('train');
  const [groupColumn, setGroupColumn] = useState(NO_EVAL);
  const [sourceColumn, setSourceColumn] = useState(NO_EVAL);
  const [metrics, setMetrics] = useState('');

  useEffect(() => {
    Promise.all([forge.models(), forge.datasets(), forge.nodes(), forge.priorities()])
      .then(([m, d, n, p]) => {
        setModels(m);
        setDatasets(d);
        setNodes(n);
        setPriority(p.find((k) => k.kind === 'forge_run')?.priority ?? null);
        if (n.length === 1) setNode(n[0].name);
      })
      .catch((e) => setLoadError(failure(e)));
  }, []);

  const chosenNode = useMemo(() => nodes?.find((n) => n.name === node), [nodes, node]);
  const chosenDataset = useMemo(() => datasets?.find((d) => d.id === datasetId), [datasets, datasetId]);

  // The splits of the version picked; the dataset's own until its versions are read
  const splits = useMemo(
    () => versions?.find((v) => v.version === datasetVersion)?.splits ?? chosenDataset?.splits ?? [],
    [versions, datasetVersion, chosenDataset],
  );

  const pickSplits = (from: DatasetSplit[]) => {
    const train = from.some((s) => s.name === 'train') ? 'train' : from[0].name;
    setTrainSplit(train);
    setEvalChoice(evalSplitOf(from, train));
  };

  const pickDataset = (id: string) => {
    const d = datasets!.find((x) => x.id === id)!;
    setDatasetId(id);
    setDatasetVersion(d.version);
    setVersions(null);
    pickSplits(d.splits);
    forge
      .datasetVersions(id)
      .then(setVersions)
      .catch((e) => setLoadError(failure(e)));
  };

  const pickVersion = (n: number) => {
    setDatasetVersion(n);
    pickSplits(versions!.find((v) => v.version === n)!.splits);
  };

  // The first example as the base model will see it
  useEffect(() => {
    setPreview(null);
    setPreviewError(null);
    if (!datasetId || datasetVersion === null || !baseModel) return;
    forge
      .preview(datasetId, datasetVersion, baseModel, trainSplit)
      .then(setPreview)
      .catch((e) => setPreviewError(failure(e)));
  }, [datasetId, datasetVersion, baseModel, trainSplit]);

  // The run follows its plan job until the plan is there
  const followRun = useCallback(() => {
    if (!run) return;
    forge.run(run.id).then(setRun).catch((e) => setCreateError(failure(e)));
  }, [run]);
  useEffect(() => {
    if (run?.status !== 'preprocessing' && run?.status !== 'planning') return;
    const timer = setInterval(followRun, PLAN_POLL_MS);
    return () => clearInterval(timer);
  }, [run?.status, followRun]);

  // The evaluation set's latest ready version: its splits and columns
  const evalSet = useMemo(() => datasets?.find((d) => d.id === evalSetId), [datasets, evalSetId]);
  const evalSetVersion = useMemo(
    () => evalSetVersions?.find((v) => v.version === evalSet?.version) ?? null,
    [evalSetVersions, evalSet],
  );
  const evalColumns = (evalSetVersion?.columns ?? []).map((c) => c.name).filter((c) => !c.startsWith('_'));
  const metricList = metrics.split(',').map((m) => m.trim()).filter(Boolean);

  const pickEvalSet = (id: string) => {
    setEvalSetId(id);
    setEvalSetVersions(null);
    setGroupColumn(NO_EVAL);
    setSourceColumn(NO_EVAL);
    if (id === NO_EVAL) return;
    const d = datasets!.find((x) => x.id === id)!;
    setEvalSetSplit(d.splits.some((sp) => sp.name === 'train') ? 'train' : d.splits[0].name);
    forge
      .datasetVersions(id)
      .then(setEvalSetVersions)
      .catch((e) => setLoadError(failure(e)));
  };

  const milestonesReady = !milestonesOn || (milestonePct > 0 && milestonePct <= 100
    && (evalSetId === NO_EVAL || metricList.length > 0));
  const ready = name.trim() && baseModel && datasetId && datasetVersion !== null && node && priority !== null
    && milestonesReady;

  const plan = async () => {
    setCreating(true);
    setCreateError(null);
    try {
      setRun(
        await forge.createRun({
          name: name.trim(),
          dataset_id: datasetId,
          dataset_version: datasetVersion!,
          train_split: trainSplit,
          eval_split: evalChoice === NO_EVAL || evalChoice === HOLDOUT ? null : evalChoice,
          eval_holdout: evalChoice === HOLDOUT ? holdoutPct / 100 : 0,
          base_model: baseModel,
          method,
          node,
          register,
          steps: length === 'steps' ? steps : null,
          epochs: length === 'epochs' ? epochs : null,
          decisions: {},
          when,
          across_slots: when === 'slots' && acrossSlots,
          priority: priority as number,
          queue: false,
          memory_gb: advanced ? memoryGb : null,
          cpu_cores: advanced ? cpuCores : null,
          milestones: milestonesOn
            ? {
              every: milestonePct / 100,
              eval_dataset_id: evalSetId === NO_EVAL ? null : evalSetId,
              eval_dataset_version: evalSetId === NO_EVAL ? null : evalSet!.version,
              eval_split: evalSetSplit,
              group_column: groupColumn === NO_EVAL ? null : groupColumn,
              source_column: sourceColumn === NO_EVAL ? null : sourceColumn,
              metrics: evalSetId === NO_EVAL ? [] : metricList,
            }
            : null,
        }),
      );
    } catch (e) {
      setCreateError(failure(e));
    } finally {
      setCreating(false);
    }
  };

  if (loadError) {
    return (
      <TkPageWrapper>
        <TkErrorAlert title={t('newRun.loadFailed')}>{loadError}</TkErrorAlert>
      </TkPageWrapper>
    );
  }

  return (
    <TkPageWrapper>
      <div>
        <h1 className="text-2xl font-bold">{t('newRun.title')}</h1>
        <p className="text-muted-foreground mt-1">{t('newRun.subtitle')}</p>
      </div>

      <fieldset disabled={run !== null} className="space-y-6">
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>{t('newRun.what')}</TkCardTitle>
          </TkCardHeader>
          <TkCardContent className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1 md:col-span-2">
              <TkLabel htmlFor="run-name">{t('newRun.name')}</TkLabel>
              <TkInput id="run-name" value={name} placeholder={t('newRun.namePlaceholder')} onChange={(e) => setName(e.target.value)} />
              {register && name.trim() && <p className="text-xs text-muted-foreground">{t('newRun.modelWillBe', { id: modelId(name) })}</p>}
            </div>

            <div className="space-y-1">
              <TkLabel>{t('newRun.baseModel')}</TkLabel>
              {models && models.length === 0 ? (
                <TkWarningAlert>{t('newRun.noModels')}</TkWarningAlert>
              ) : (
                <TkSelect value={baseModel} onValueChange={setBaseModel}>
                  <TkSelectTrigger>
                    <TkSelectValue placeholder={t('newRun.pickModel')} />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {(models ?? []).map((m) => (
                      <TkSelectItem key={m.id} value={m.id}>
                        {m.name} {m.params_b ? `· ${m.params_b}B` : ''}
                      </TkSelectItem>
                    ))}
                  </TkSelectContent>
                </TkSelect>
              )}
            </div>

            <div className="space-y-1">
              <TkLabel>{t('newRun.dataset')}</TkLabel>
              {datasets && datasets.length === 0 ? (
                <TkInfoAlert>
                  {t('newRun.noDatasets')}{' '}
                  <a className="underline" href={DATASETS_URL}>
                    {t('newRun.openDatasets')}
                  </a>
                </TkInfoAlert>
              ) : (
                <TkSelect value={datasetId} onValueChange={pickDataset}>
                  <TkSelectTrigger>
                    <TkSelectValue placeholder={t('newRun.pickDataset')} />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {(datasets ?? []).map((d) => (
                      <TkSelectItem key={d.id} value={d.id}>
                        {d.name} · v{d.version} · {d.origin}
                      </TkSelectItem>
                    ))}
                  </TkSelectContent>
                </TkSelect>
              )}
            </div>

            {chosenDataset && datasetVersion !== null && (
              <div className="space-y-1">
                <TkLabel>{t('newRun.datasetVersion')}</TkLabel>
                <TkSelect
                  value={String(datasetVersion)}
                  disabled={!versions}
                  onValueChange={(v) => pickVersion(Number(v))}
                >
                  <TkSelectTrigger aria-label={t('newRun.datasetVersion')}>
                    <TkSelectValue />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {versions ? (
                      versions.map((v) => (
                        <TkSelectItem key={v.version} value={String(v.version)}>
                          {t(v.version === chosenDataset.version ? 'newRun.versionLatest' : 'newRun.versionItem', {
                            version: v.version,
                            count: v.rows,
                          })}
                        </TkSelectItem>
                      ))
                    ) : (
                      <TkSelectItem value={String(datasetVersion)}>v{datasetVersion}</TkSelectItem>
                    )}
                  </TkSelectContent>
                </TkSelect>
              </div>
            )}

            {chosenDataset && (
              <div className="space-y-1">
                <TkLabel>{t('newRun.trainSplit')}</TkLabel>
                <TkSelect
                  value={trainSplit}
                  onValueChange={(v) => {
                    setTrainSplit(v);
                    setEvalChoice(evalSplitOf(splits, v));
                  }}
                >
                  <TkSelectTrigger>
                    <TkSelectValue />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {splits.map((sp) => (
                      <TkSelectItem key={sp.name} value={sp.name}>
                        {sp.name} · {t('newRun.rows', { count: sp.rows })}
                      </TkSelectItem>
                    ))}
                  </TkSelectContent>
                </TkSelect>
              </div>
            )}

            {chosenDataset && (
              <div className="space-y-1">
                <TkLabel>{t('newRun.evaluation')}</TkLabel>
                <div className="flex gap-2">
                  <TkSelect value={evalChoice} onValueChange={setEvalChoice}>
                    <TkSelectTrigger>
                      <TkSelectValue />
                    </TkSelectTrigger>
                    <TkSelectContent>
                      <TkSelectItem value={HOLDOUT}>{t('newRun.holdout')}</TkSelectItem>
                      {splits
                        .filter((sp) => sp.name !== trainSplit)
                        .map((sp) => (
                          <TkSelectItem key={sp.name} value={sp.name}>
                            {t('newRun.evalSplit', { split: sp.name, count: sp.rows })}
                          </TkSelectItem>
                        ))}
                      <TkSelectItem value={NO_EVAL}>{t('newRun.noEval')}</TkSelectItem>
                    </TkSelectContent>
                  </TkSelect>
                  {evalChoice === HOLDOUT && (
                    <TkInput
                      className="w-24"
                      type="number"
                      min={1}
                      max={50}
                      value={holdoutPct}
                      aria-label={t('newRun.holdoutPct')}
                      onChange={(e) => setHoldoutPct(Number(e.target.value))}
                    />
                  )}
                </div>
                <p className="text-xs text-muted-foreground">{t('newRun.evaluationHelp')}</p>
              </div>
            )}

            <div className="space-y-2">
              <TkLabel>{t('newRun.method')}</TkLabel>
              <TkRadioGroup value={method} onValueChange={(v) => setMethod(v as Method)}>
                {(['lora', 'qlora', 'fp8'] as Method[]).map((m) => (
                  <label key={m} className="flex items-start gap-2 text-sm">
                    <TkRadioGroupItem value={m} className="mt-0.5" />
                    <span>
                      <span className="font-medium">{t(`method.${m}`)}</span>
                      <span className="block text-muted-foreground">{t(`method.${m}Help`)}</span>
                    </span>
                  </label>
                ))}
              </TkRadioGroup>
            </div>

            <div className="space-y-1">
              <TkLabel>{t('newRun.node')}</TkLabel>
              <TkSelect value={node} onValueChange={setNode}>
                <TkSelectTrigger>
                  <TkSelectValue placeholder={t('newRun.pickNode')} />
                </TkSelectTrigger>
                <TkSelectContent>
                  {(nodes ?? []).map((n) => (
                    <TkSelectItem key={n.name} value={n.name}>
                      {n.name} · {t('newRun.nodeMemory', { free: n.ai_remaining_gb?.toFixed(0) ?? '?', total: n.total_memory_gb.toFixed(0) })}
                    </TkSelectItem>
                  ))}
                </TkSelectContent>
              </TkSelect>
              {chosenNode && chosenNode.loaded_models.length > 0 && (
                <p className="text-xs text-muted-foreground">{t('newRun.nodeLoaded', { models: chosenNode.loaded_models.join(', ') })}</p>
              )}
            </div>

            <label className="flex items-center gap-3 text-sm md:col-span-2">
              <TkSwitch checked={register} onCheckedChange={setRegister} />
              <span>
                <span className="font-medium">{t('newRun.register')}</span>
                <span className="block text-muted-foreground">{t('newRun.registerHelp')}</span>
              </span>
            </label>

            <label className="flex items-center gap-3 text-sm md:col-span-2">
              <TkSwitch checked={milestonesOn} onCheckedChange={setMilestonesOn} />
              <span>
                <span className="font-medium">{t('newRun.milestones')}</span>
                <span className="block text-muted-foreground">{t('newRun.milestonesHelp')}</span>
              </span>
            </label>
            {milestonesOn && (
              <>
                <div className="space-y-1">
                  <TkLabel htmlFor="milestone-pct">{t('newRun.milestoneEvery')}</TkLabel>
                  <TkInput id="milestone-pct" className="w-28" type="number" min={0.1} max={100} step={0.1}
                    value={milestonePct} onChange={(e) => setMilestonePct(Number(e.target.value))} />
                </div>
                <div className="space-y-1">
                  <TkLabel>{t('newRun.milestoneEvalSet')}</TkLabel>
                  <TkSelect value={evalSetId} onValueChange={pickEvalSet}>
                    <TkSelectTrigger>
                      <TkSelectValue />
                    </TkSelectTrigger>
                    <TkSelectContent>
                      <TkSelectItem value={NO_EVAL}>{t('newRun.milestoneAdapterOnly')}</TkSelectItem>
                      {(datasets ?? []).filter((d) => d.mapping.kind !== 'text').map((d) => (
                        <TkSelectItem key={d.id} value={d.id}>{d.name} · v{d.version}</TkSelectItem>
                      ))}
                    </TkSelectContent>
                  </TkSelect>
                </div>
                {evalSet && (
                  <>
                    <div className="space-y-1">
                      <TkLabel>{t('newRun.milestoneSplit')}</TkLabel>
                      <TkSelect value={evalSetSplit} onValueChange={setEvalSetSplit}>
                        <TkSelectTrigger>
                          <TkSelectValue />
                        </TkSelectTrigger>
                        <TkSelectContent>
                          {evalSet.splits.map((sp) => (
                            <TkSelectItem key={sp.name} value={sp.name}>
                              {sp.name} · {t('newRun.rows', { count: sp.rows })}
                            </TkSelectItem>
                          ))}
                        </TkSelectContent>
                      </TkSelect>
                    </div>
                    {([['milestoneGroup', groupColumn, setGroupColumn], ['milestoneSource', sourceColumn, setSourceColumn]] as const).map(
                      ([label, value, set]) => (
                        <div key={label} className="space-y-1">
                          <TkLabel>{t(`newRun.${label}`)}</TkLabel>
                          <TkSelect value={value} onValueChange={set}>
                            <TkSelectTrigger>
                              <TkSelectValue />
                            </TkSelectTrigger>
                            <TkSelectContent>
                              <TkSelectItem value={NO_EVAL}>{t('newRun.milestoneNoColumn')}</TkSelectItem>
                              {evalColumns.map((c) => (
                                <TkSelectItem key={c} value={c}>{c}</TkSelectItem>
                              ))}
                            </TkSelectContent>
                          </TkSelect>
                          <p className="text-xs text-muted-foreground">{t(`newRun.${label}Help`)}</p>
                        </div>
                      ),
                    )}
                    <div className="space-y-1">
                      <TkLabel htmlFor="milestone-metrics">{t('newRun.milestoneMetrics')}</TkLabel>
                      <TkInput id="milestone-metrics" value={metrics} placeholder="chrf, bleu"
                        onChange={(e) => setMetrics(e.target.value)} />
                      <p className="text-xs text-muted-foreground">{t('newRun.milestoneMetricsHelp')}</p>
                    </div>
                  </>
                )}
              </>
            )}
          </TkCardContent>
        </TkCard>

        {(preview || previewError) && (
          <TkCard>
            <TkCardHeader>
              <TkCardTitle>{t('newRun.previewTitle')}</TkCardTitle>
              <TkCardDescription>{t(preview?.templated === false ? 'newRun.previewText' : 'newRun.previewHelp')}</TkCardDescription>
            </TkCardHeader>
            <TkCardContent>
              {previewError ? (
                <TkErrorAlert title={t('newRun.previewFailed')}>{previewError}</TkErrorAlert>
              ) : (
                <TkCodeBlock maxHeight="max-h-72">{preview?.text ?? ''}</TkCodeBlock>
              )}
            </TkCardContent>
          </TkCard>
        )}

        <TkCard>
          <TkCardHeader>
            <TkCardTitle>{t('newRun.settings')}</TkCardTitle>
            <TkCardDescription>{t('newRun.settingsHelp')}</TkCardDescription>
          </TkCardHeader>
          <TkCardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-2">
              <TkLabel>{t('newRun.length')}</TkLabel>
              <TkRadioGroup value={length} onValueChange={(v) => setLength(v as 'epochs' | 'steps')}>
                {(['epochs', 'steps'] as const).map((l) => (
                  <label key={l} className="flex items-center gap-2 text-sm">
                    <TkRadioGroupItem value={l} />
                    {t(`newRun.${l}`)}
                  </label>
                ))}
              </TkRadioGroup>
            </div>
            <div className="space-y-1">
              <TkLabel htmlFor="length">{t(`newRun.${length}`)}</TkLabel>
              <TkInput
                id="length"
                type="number"
                step={length === 'epochs' ? '0.5' : '100'}
                value={length === 'epochs' ? epochs : steps}
                onChange={(e) => (length === 'epochs' ? setEpochs(Number(e.target.value)) : setSteps(Number(e.target.value)))}
              />
            </div>
          </TkCardContent>
        </TkCard>

        <TkCard>
          <TkCardHeader>
            <TkCardTitle>{t('newRun.advanced')}</TkCardTitle>
            <TkCardDescription>{t('newRun.advancedHelp')}</TkCardDescription>
          </TkCardHeader>
          <TkCardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <label className="flex items-center gap-3 text-sm sm:col-span-2 lg:col-span-3">
              <TkSwitch checked={advanced} onCheckedChange={setAdvanced} />
              <span className="font-medium">{t('newRun.setHardware')}</span>
            </label>
            {advanced && (
              <>
                <div className="space-y-1">
                  <TkLabel htmlFor="memory">{t('newRun.memory')}</TkLabel>
                  <TkInput id="memory" type="number" step="4" value={memoryGb} onChange={(e) => setMemoryGb(Number(e.target.value))} />
                </div>
                <div className="space-y-1">
                  <TkLabel htmlFor="cpu">{t('newRun.cpu')}</TkLabel>
                  <TkInput id="cpu" type="number" step="1" value={cpuCores} onChange={(e) => setCpuCores(Number(e.target.value))} />
                </div>
              </>
            )}
          </TkCardContent>
        </TkCard>

        <TkCard>
          <TkCardHeader>
            <TkCardTitle>{t('newRun.whenTitle')}</TkCardTitle>
            <TkCardDescription>{t('newRun.whenHelp')}</TkCardDescription>
          </TkCardHeader>
          <TkCardContent className="space-y-3">
            <TkRadioGroup value={when} onValueChange={(v) => setWhen(v as When)}>
              {(['now', 'slots'] as When[]).map((w) => (
                <label key={w} className="flex items-start gap-2 text-sm">
                  <TkRadioGroupItem value={w} className="mt-0.5" />
                  <span>
                    <span className="font-medium">{t(`queue.whenValue.${w}`)}</span>
                    <span className="block text-muted-foreground">{t(`newRun.when.${w}`)}</span>
                  </span>
                </label>
              ))}
            </TkRadioGroup>
            <div className="space-y-1">
              <TkLabel>{t('priority.title')}</TkLabel>
              {priority !== null && <PrioritySelect value={priority} label={t('priority.title')} onChange={setPriority} />}
              <p className="text-xs text-muted-foreground">{t('priority.runHelp')}</p>
            </div>
            {when === 'slots' && (
              <label className="flex items-center gap-3 text-sm">
                <TkSwitch checked={acrossSlots} onCheckedChange={setAcrossSlots} />
                <span>
                  <span className="font-medium">{t('queue.acrossSlots')}</span>
                  <span className="block text-muted-foreground">{t('newRun.acrossSlotsHelp')}</span>
                </span>
              </label>
            )}
          </TkCardContent>
        </TkCard>
      </fieldset>

      {createError && <TkErrorAlert title={t('newRun.startFailed')}>{createError}</TkErrorAlert>}
      {run ? (
        <PlanStep run={run} onRun={setRun} />
      ) : (
        <div>
          <TkLoadingButton loading={creating} disabled={!ready} onClick={plan}>
            <Sparkles className="h-4 w-4" />
            {t('newRun.plan')}
          </TkLoadingButton>
        </div>
      )}
    </TkPageWrapper>
  );
}

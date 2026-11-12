/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, ChevronLeft, ChevronRight, Save } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import { TkBadge, TkButton, TkLoadingButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkCodeBlock, TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback';
import { TkLabel, TkSelect, TkSelectContent, TkSelectItem, TkSelectTrigger, TkSelectValue } from 'thinkube-style/components/forms-inputs';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import {
  PARTS,
  cleanMapping,
  complete,
  datasets,
  failure,
  type Dataset,
  type MappingKind,
  type Rows,
  type Version,
} from '@/lib/datasets';

const NONE = '__none__';
const STATUS = { ready: 'healthy', importing: 'pending', identifying: 'pending', failed: 'unhealthy' } as const;
const KINDS = Object.keys(PARTS) as MappingKind[];

export default function DatasetPage() {
  const { t } = useTranslation();
  const { id = '' } = useParams();
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  // The version shown; until one is chosen, the dataset's latest ready version
  const [chosen, setChosen] = useState<number | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<MappingKind>('prompt');
  const [columns, setColumns] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [split, setSplit] = useState('');
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<Rows | null>(null);
  const [rowsError, setRowsError] = useState<string | null>(null);

  const show = useCallback((d: Dataset) => {
    setDataset(d);
    if (d.mapping) {
      setKind(d.mapping.kind);
      setColumns(d.mapping.columns);
    }
    setSplit((current) => (d.splits.some((s) => s.name === current) ? current : d.splits[0]?.name || ''));
  }, []);

  useEffect(() => {
    datasets
      .get(id, chosen)
      .then(show)
      .catch((e) => setError(failure(e)));
  }, [id, chosen, show]);

  useEffect(() => {
    datasets
      .versions(id)
      .then(setVersions)
      .catch((e) => setError(failure(e)));
  }, [id]);

  const version = dataset?.version;
  const readRows = useCallback(() => {
    if (!split || version === undefined) return;
    datasets
      .rows(id, version, split, offset)
      .then((r) => {
        setRows(r);
        setRowsError(null);
      })
      .catch((e) => setRowsError(failure(e)));
  }, [id, version, split, offset]);

  useEffect(() => {
    readRows();
  }, [readRows]);

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      show(await datasets.setMapping(id, dataset!.version, cleanMapping(kind, columns)));
      toast.success(t('dataset.mappingSaved'));
      readRows();
    } catch (e) {
      setSaveError(failure(e));
    } finally {
      setSaving(false);
    }
  };

  if (error) return <TkPageWrapper><TkErrorAlert title={t('dataset.loadFailed')}>{error}</TkErrorAlert></TkPageWrapper>;
  if (!dataset) return null;

  const mapping = cleanMapping(kind, columns);
  const lineage = (v: Version) =>
    v.made_by === 'recipe'
      ? t('dataset.madeByRecipe', { recipe: v.recipe!.name, from: v.made_from })
      : t(`dataset.madeBy.${v.made_by}`);
  const total = dataset.splits.find((s) => s.name === split)?.rows ?? 0;

  return (
    <TkPageWrapper>
      <div>
        <Link to="/" className="text-sm text-muted-foreground inline-flex items-center gap-1">
          <ArrowLeft className="h-4 w-4" />
          {t('dataset.back')}
        </Link>
        <h1 className="text-2xl font-bold mt-2 break-all">{dataset.name}</h1>
        <p className="text-muted-foreground mt-1 break-all">
          {dataset.origin} · {t('dataset.versionShown', { number: dataset.version })}
        </p>
      </div>

      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{t('dataset.versions')}</TkCardTitle>
          <TkCardDescription>{t('dataset.versionsHelp')}</TkCardDescription>
        </TkCardHeader>
        <TkCardContent className="overflow-x-auto">
          <TkTable>
            <TkTableHeader>
              <TkTableRow>
                <TkTableHead>{t('dataset.version')}</TkTableHead>
                <TkTableHead>{t('dataset.lineage')}</TkTableHead>
                <TkTableHead className="text-right">{t('dataset.rowCount')}</TkTableHead>
                <TkTableHead>{t('dataset.splitRows')}</TkTableHead>
                <TkTableHead>{t('dataset.created')}</TkTableHead>
                <TkTableHead>{t('dataset.status')}</TkTableHead>
                <TkTableHead />
              </TkTableRow>
            </TkTableHeader>
            <TkTableBody>
              {versions.map((v) => (
                <TkTableRow key={v.version} data-state={v.version === dataset.version ? 'selected' : undefined}>
                  <TkTableCell className="font-medium">v{v.version}</TkTableCell>
                  <TkTableCell>{lineage(v)}</TkTableCell>
                  <TkTableCell className="text-right tabular-nums">{v.rows.toLocaleString()}</TkTableCell>
                  <TkTableCell className="text-muted-foreground">
                    {v.splits.map((s) => `${s.name} ${s.rows.toLocaleString()}`).join(' · ')}
                  </TkTableCell>
                  <TkTableCell className="text-muted-foreground whitespace-nowrap">
                    {new Date(v.created_at).toLocaleString()}
                  </TkTableCell>
                  <TkTableCell>
                    <TkBadge status={STATUS[v.status]}>{t(`status.${v.status}`)}</TkBadge>
                  </TkTableCell>
                  <TkTableCell className="text-right">
                    {v.version === dataset.version ? (
                      <span className="text-sm text-muted-foreground">{t('dataset.shown')}</span>
                    ) : (
                      <TkButton
                        intent="secondary"
                        size="sm"
                        disabled={v.status !== 'ready'}
                        onClick={() => {
                          setOffset(0);
                          setChosen(v.version);
                        }}
                      >
                        {t('dataset.show')}
                      </TkButton>
                    )}
                  </TkTableCell>
                </TkTableRow>
              ))}
            </TkTableBody>
          </TkTable>
        </TkCardContent>
      </TkCard>

      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{t('dataset.columns')}</TkCardTitle>
        </TkCardHeader>
        <TkCardContent className="flex flex-wrap gap-2">
          {dataset.columns.map((c) => (
            <TkBadge key={c.name} appearance="outlined">
              {c.name} <span className="text-muted-foreground ml-1">{c.type}</span>
            </TkBadge>
          ))}
        </TkCardContent>
      </TkCard>

      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{t('dataset.mapping')}</TkCardTitle>
          <TkCardDescription>{t('dataset.mappingHelp')}</TkCardDescription>
        </TkCardHeader>
        <TkCardContent className="space-y-4">
          <div className="space-y-1 max-w-sm">
            <TkLabel>{t('dataset.kind')}</TkLabel>
            <TkSelect value={kind} onValueChange={(v) => setKind(v as MappingKind)}>
              <TkSelectTrigger>
                <TkSelectValue />
              </TkSelectTrigger>
              <TkSelectContent>
                {KINDS.map((k) => (
                  <TkSelectItem key={k} value={k}>
                    {t(`kind.${k}`)}
                  </TkSelectItem>
                ))}
              </TkSelectContent>
            </TkSelect>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[...PARTS[kind].required, ...PARTS[kind].optional].map((part) => (
              <div key={part} className="space-y-1">
                <TkLabel>
                  {t(`part.${part}`)}
                  {PARTS[kind].optional.includes(part) && <span className="text-muted-foreground"> · {t('dataset.optional')}</span>}
                </TkLabel>
                <TkSelect
                  value={columns[part] || NONE}
                  onValueChange={(v) => setColumns({ ...columns, [part]: v === NONE ? '' : v })}
                >
                  <TkSelectTrigger>
                    <TkSelectValue />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    <TkSelectItem value={NONE}>{t('dataset.noColumn')}</TkSelectItem>
                    {dataset.columns.map((c) => (
                      <TkSelectItem key={c.name} value={c.name}>
                        {c.name}
                      </TkSelectItem>
                    ))}
                  </TkSelectContent>
                </TkSelect>
              </div>
            ))}
          </div>
          {saveError && <TkErrorAlert title={t('dataset.mappingFailed')}>{saveError}</TkErrorAlert>}
          <TkLoadingButton loading={saving} disabled={!complete(mapping)} onClick={save}>
            <Save className="h-4 w-4" />
            {t('dataset.saveMapping')}
          </TkLoadingButton>
        </TkCardContent>
      </TkCard>

      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{t('dataset.rows')}</TkCardTitle>
          <TkCardDescription>{t('dataset.rowsHelp')}</TkCardDescription>
        </TkCardHeader>
        <TkCardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            {dataset.splits.length > 1 && (
              <TkSelect
                value={split}
                onValueChange={(v) => {
                  setSplit(v);
                  setOffset(0);
                }}
              >
                <TkSelectTrigger className="w-48">
                  <TkSelectValue />
                </TkSelectTrigger>
                <TkSelectContent>
                  {dataset.splits.map((s) => (
                    <TkSelectItem key={s.name} value={s.name}>
                      {s.name}
                    </TkSelectItem>
                  ))}
                </TkSelectContent>
              </TkSelect>
            )}
            <span className="text-sm text-muted-foreground">
              {t('dataset.showing', { from: Math.min(offset + 1, total), to: Math.min(offset + 10, total), total })}
            </span>
            <div className="ml-auto flex gap-2">
              <TkButton intent="secondary" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 10))}>
                <ChevronLeft className="h-4 w-4" />
              </TkButton>
              <TkButton intent="secondary" size="sm" disabled={offset + 10 >= total} onClick={() => setOffset(offset + 10)}>
                <ChevronRight className="h-4 w-4" />
              </TkButton>
            </div>
          </div>
          {rowsError && <TkErrorAlert title={t('dataset.rowsFailed')}>{rowsError}</TkErrorAlert>}
          {!dataset.mapping && <TkInfoAlert>{t('dataset.mapFirst')}</TkInfoAlert>}
          {rows?.rows.map((row, i) => (
            <div key={offset + i} className="grid gap-2 lg:grid-cols-2">
              <TkCodeBlock maxHeight="max-h-56">{JSON.stringify(row, null, 1)}</TkCodeBlock>
              {rows.examples[i] &&
                (rows.examples[i].error ? (
                  <TkErrorAlert title={t('dataset.rowDoesNotFit')}>{rows.examples[i].error}</TkErrorAlert>
                ) : (
                  <TkCodeBlock maxHeight="max-h-56">{JSON.stringify(rows.examples[i].example, null, 1)}</TkCodeBlock>
                ))}
            </div>
          ))}
        </TkCardContent>
      </TkCard>
    </TkPageWrapper>
  );
}

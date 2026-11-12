/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { useCallback, useEffect, useState } from 'react';
import { Download, Trash2, Upload } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { TkBadge, TkButton, TkLoadingButton } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkCardDescription, TkCardHeader, TkCardTitle } from 'thinkube-style/components/cards-data';
import { TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback';
import { TkDropZone, TkInput, TkLabel } from 'thinkube-style/components/forms-inputs';
import { TkControlledConfirmDialog } from 'thinkube-style/components/modals-overlays';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { datasets, failure, size, type Dataset } from '@/lib/datasets';

const STATUS = { ready: 'healthy', importing: 'pending', identifying: 'pending', failed: 'unhealthy' } as const;

export default function DatasetsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [list, setList] = useState<Dataset[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [name, setName] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [repo, setRepo] = useState('');
  const [config, setConfig] = useState('');
  const [splits, setSplits] = useState('');
  const [hubName, setHubName] = useState('');
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<Dataset | null>(null);

  const load = useCallback(() => {
    datasets
      .list()
      .then((d) => {
        setList(d);
        setError(null);
      })
      .catch((e) => setError(failure(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // An import runs on the server; the list is read again until it has ended
  const busy = list.some((d) => d.status === 'importing');
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, [busy, load]);

  const upload = async () => {
    if (!files[0]) return;
    setUploading(true);
    setUploadError(null);
    try {
      const dataset = await datasets.upload(files[0], name.trim());
      toast.success(t('datasets.uploaded', { name: dataset.name }));
      setFiles([]);
      setName('');
      navigate(`/d/${dataset.id}`);
    } catch (e) {
      setUploadError(failure(e));
    } finally {
      setUploading(false);
    }
  };

  const importHub = async () => {
    setImporting(true);
    setImportError(null);
    try {
      const wanted = splits
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      const dataset = await datasets.importHub(repo.trim(), config.trim(), wanted, hubName.trim());
      toast.success(t('datasets.importStarted', { name: dataset.name }));
      setRepo('');
      setConfig('');
      setSplits('');
      setHubName('');
      load();
    } catch (e) {
      setImportError(failure(e));
    } finally {
      setImporting(false);
    }
  };

  const remove = async () => {
    if (!toDelete) return;
    try {
      await datasets.remove(toDelete.id);
      load();
    } catch (e) {
      toast.error(failure(e));
    } finally {
      setToDelete(null);
    }
  };

  return (
    <TkPageWrapper>
      <div>
        <h1 className="text-2xl font-bold">{t('datasets.title')}</h1>
        <p className="text-muted-foreground mt-1">{t('datasets.subtitle')}</p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>{t('datasets.uploadTitle')}</TkCardTitle>
            <TkCardDescription>{t('datasets.uploadHelp')}</TkCardDescription>
          </TkCardHeader>
          <TkCardContent className="space-y-4">
            <TkDropZone accept=".jsonl,.json,.csv,.parquet" files={files} onFilesChange={setFiles} label={t('datasets.file')} />
            <div className="space-y-1">
              <TkLabel htmlFor="upload-name">{t('datasets.name')}</TkLabel>
              <TkInput id="upload-name" value={name} placeholder={files[0]?.name ?? ''} onChange={(e) => setName(e.target.value)} />
            </div>
            {uploadError && <TkErrorAlert title={t('datasets.uploadFailed')}>{uploadError}</TkErrorAlert>}
            <TkLoadingButton loading={uploading} disabled={!files[0]} onClick={upload}>
              <Upload className="h-4 w-4" />
              {t('datasets.upload')}
            </TkLoadingButton>
          </TkCardContent>
        </TkCard>

        <TkCard>
          <TkCardHeader>
            <TkCardTitle>{t('datasets.hubTitle')}</TkCardTitle>
            <TkCardDescription>{t('datasets.hubHelp')}</TkCardDescription>
          </TkCardHeader>
          <TkCardContent className="space-y-4">
            <div className="space-y-1">
              <TkLabel htmlFor="hub-repo">{t('datasets.repo')}</TkLabel>
              <TkInput id="hub-repo" value={repo} placeholder="tatsu-lab/alpaca" onChange={(e) => setRepo(e.target.value)} />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1">
                <TkLabel htmlFor="hub-config">{t('datasets.config')}</TkLabel>
                <TkInput id="hub-config" value={config} placeholder={t('datasets.configHint')} onChange={(e) => setConfig(e.target.value)} />
              </div>
              <div className="space-y-1">
                <TkLabel htmlFor="hub-splits">{t('datasets.splits')}</TkLabel>
                <TkInput id="hub-splits" value={splits} placeholder={t('datasets.splitsHint')} onChange={(e) => setSplits(e.target.value)} />
              </div>
            </div>
            <div className="space-y-1">
              <TkLabel htmlFor="hub-name">{t('datasets.name')}</TkLabel>
              <TkInput id="hub-name" value={hubName} placeholder={repo.split('/')[1] ?? ''} onChange={(e) => setHubName(e.target.value)} />
            </div>
            {importError && <TkErrorAlert title={t('datasets.importFailed')}>{importError}</TkErrorAlert>}
            <TkLoadingButton loading={importing} disabled={!repo.includes('/')} onClick={importHub}>
              <Download className="h-4 w-4" />
              {t('datasets.import')}
            </TkLoadingButton>
          </TkCardContent>
        </TkCard>
      </div>

      {error && <TkErrorAlert title={t('datasets.loadFailed')}>{error}</TkErrorAlert>}
      {list.length === 0 && !error ? (
        <TkInfoAlert>{t('datasets.none')}</TkInfoAlert>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {list.map((d) => (
            <TkCard key={d.id}>
              <TkCardHeader>
                <TkCardTitle className="text-base break-all">{d.name}</TkCardTitle>
                <TkCardDescription className="break-all">
                  {d.origin} · {new Date(d.created_at).toLocaleDateString()}
                </TkCardDescription>
              </TkCardHeader>
              <TkCardContent className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <TkBadge status={STATUS[d.status]}>{t(`status.${d.status}`)}</TkBadge>
                  <TkBadge appearance="outlined">
                    {t('datasets.version', { number: d.version, count: d.versions })}
                  </TkBadge>
                  {d.splits.map((s) => (
                    <TkBadge key={s.name} appearance="outlined">
                      {s.name} · {t('datasets.rows', { count: s.rows })} · {size(s.bytes)}
                    </TkBadge>
                  ))}
                  {d.status === 'ready' && !d.mapping && <TkBadge status="warning">{t('datasets.noMapping')}</TkBadge>}
                </div>
                {d.error && <p className="text-sm text-destructive">{d.error}</p>}
                <div className="flex gap-2">
                  <TkButton intent="secondary" size="sm" disabled={d.status !== 'ready'} onClick={() => navigate(`/d/${d.id}`)}>
                    {t('datasets.open')}
                  </TkButton>
                  <TkButton
                    intent="ghost"
                    size="sm"
                    className="ml-auto"
                    aria-label={t('common.delete')}
                    onClick={() => setToDelete(d)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </TkButton>
                </div>
              </TkCardContent>
            </TkCard>
          ))}
        </div>
      )}

      <TkControlledConfirmDialog
        open={!!toDelete}
        onOpenChange={(open) => !open && setToDelete(null)}
        title={t('datasets.deleteTitle')}
        description={t('datasets.deleteConfirm', { name: toDelete?.name ?? '' })}
        confirmText={t('common.delete')}
        cancelText={t('common.cancel')}
        variant="destructive"
        onConfirm={remove}
      />
    </TkPageWrapper>
  );
}

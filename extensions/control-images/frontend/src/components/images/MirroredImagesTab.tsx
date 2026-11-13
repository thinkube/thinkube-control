/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent, TkStatCard } from 'thinkube-style/components/cards-data';
import {
  TkInput,
  TkSelect,
  TkSelectTrigger,
  TkSelectContent,
  TkSelectItem,
  TkSelectValue,
} from 'thinkube-style/components/forms-inputs';
import {
  TkTable,
  TkTableHeader,
  TkTableBody,
  TkTableRow,
  TkTableHead,
  TkTableCell,
} from 'thinkube-style/components/tables';
import { TkLoader, TkErrorAlert } from 'thinkube-style/components/feedback';
import { RefreshCw, Plus, Eye, Trash2, Lock, Edit2, Box, AlertCircle } from 'lucide-react';
import api from '@/lib/axios';
import { AddImageDialog } from './AddImageDialog';
import { ConfirmDialog } from './ConfirmDialog';
import { ImageDetailsDialog } from './ImageDetailsDialog';
import { ProgressDialog, type Followed } from './ProgressDialog';
import {
  errorText,
  formatDate,
  openInNewTab,
  type ImageStats,
  type MirrorJob,
  type MirrorStarted,
  type MirroredImage,
} from './types';

const PAGE_SIZE = 50;

// How long the search text rests before the list is asked for again
const SEARCH_DELAY_MS = 300;

/** The images mirrored into Harbor: the list with its filters, and what can be done with each image. */
export function MirroredImagesTab() {
  const { t } = useTranslation();
  const [images, setImages] = useState<MirroredImage[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<ImageStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [category, setCategory] = useState('all');
  const [protection, setProtection] = useState('all');
  const [search, setSearch] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');
  const [skip, setSkip] = useState(0);

  const [adding, setAdding] = useState(false);
  const [shown, setShown] = useState<MirroredImage | null>(null);
  const [confirm, setConfirm] = useState<{ action: 'remirror' | 'delete'; image: MirroredImage } | null>(null);
  const [followed, setFollowed] = useState<Followed | null>(null);
  const [working, setWorking] = useState(false);

  // Numbers the list requests, so that an answer that arrives late does not replace a newer one
  const lastRequest = useRef(0);

  const loadImages = useCallback(async () => {
    const request = ++lastRequest.current;
    setLoading(true);
    try {
      const params: Record<string, string | number | boolean> = { skip, limit: PAGE_SIZE };
      if (category !== 'all') params.category = category;
      if (protection !== 'all') params.protected = protection === 'true';
      if (appliedSearch) params.search = appliedSearch;
      const { data } = await api.get('/images', { params });
      if (request !== lastRequest.current) return;
      setImages(data.images);
      setTotal(data.total);
      setError(null);
    } catch (err) {
      if (request !== lastRequest.current) return;
      setError(errorText(err));
    } finally {
      if (request === lastRequest.current) setLoading(false);
    }
  }, [skip, category, protection, appliedSearch]);

  const loadStats = useCallback(async () => {
    try {
      const { data } = await api.get('/images/stats');
      setStats(data);
    } catch (err) {
      setError(errorText(err));
    }
  }, []);

  useEffect(() => {
    loadImages();
  }, [loadImages]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setAppliedSearch(search.trim());
      setSkip(0);
    }, SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [search]);

  const refresh = useCallback(() => {
    loadImages();
    loadStats();
  }, [loadImages, loadStats]);

  const hasFilters = category !== 'all' || protection !== 'all' || search !== '';

  const clearFilters = () => {
    setCategory('all');
    setProtection('all');
    setSearch('');
    setAppliedSearch('');
    setSkip(0);
  };

  /** Runs one action on the server; a failure is shown with the reason the server gave. */
  const run = async (action: () => Promise<void>) => {
    setWorking(true);
    try {
      await action();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setWorking(false);
    }
  };

  const followJob = (job: MirrorJob, name: string) => {
    setFollowed({
      title: t('images.progress.mirroring', { name }),
      successMessage: t('images.progress.mirrored', { name }),
      read: async () => {
        const { data } = await api.get(`/jobs/${job.id}`);
        return { status: data.status, log: data.logs || '', error: data.error_message };
      },
    });
  };

  const onMirrorStarted = (started: MirrorStarted) => {
    setAdding(false);
    followJob(started.job, `${started.image.name}:${started.image.tag}`);
    refresh();
  };

  const syncImages = () =>
    run(async () => {
      await api.post('/images/sync', {});
      toast.success(t('images.mirrored.synced'));
      refresh();
    });

  const toggleBase = (image: MirroredImage) =>
    run(async () => {
      const { data } = await api.patch(`/images/${image.id}/toggle-base`);
      toast.success(
        t(data.image.is_base ? 'images.mirrored.markedBase' : 'images.mirrored.unmarkedBase', { name: image.name }),
      );
      loadImages();
    });

  const editTemplate = (image: MirroredImage) =>
    run(async () => {
      const { data } = await api.get(`/images/${image.id}/edit-template`);
      openInNewTab(data.editor_url);
    });

  const confirmed = () => {
    if (!confirm) return;
    const { action, image } = confirm;
    const name = `${image.name}:${image.tag}`;
    void run(async () => {
      try {
        if (action === 'remirror') {
          const { data } = await api.post(`/images/${image.id}/remirror`);
          followJob(data.job, name);
        } else {
          await api.delete(`/images/${image.id}`);
          toast.success(t('images.mirrored.deleted', { name, url: image.destination_url }));
          refresh();
        }
      } finally {
        setConfirm(null);
      }
    });
  };

  const page = Math.floor(skip / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <TkButton intent="secondary" onClick={syncImages} disabled={working} className="gap-2">
          <RefreshCw className="h-4 w-4" />
          {t('images.mirrored.sync')}
        </TkButton>
        <TkButton onClick={() => setAdding(true)} className="gap-2">
          <Plus className="h-4 w-4" />
          {t('images.mirrored.add')}
        </TkButton>
      </div>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(12rem,1fr))] gap-4">
        <TkStatCard title={t('images.mirrored.statTotal')} value={String(stats?.total ?? '-')} variant="primary" />
        <TkStatCard
          title={t('images.mirrored.statSystem')}
          value={String(stats?.by_category.system ?? '-')}
          description={t('images.mirrored.statSystemHelp')}
          variant="default"
        />
        <TkStatCard
          title={t('images.mirrored.statUser')}
          value={String(stats?.by_category.user ?? '-')}
          description={t('images.mirrored.statUserHelp')}
          variant="warning"
        />
        <TkStatCard
          title={t('images.mirrored.statProtected')}
          value={String(stats?.protected ?? '-')}
          description={t('images.mirrored.statProtectedHelp')}
          variant="success"
        />
      </div>

      <TkCard>
        <TkCardContent>
          <div className="flex flex-wrap gap-2">
            <TkSelect
              value={category}
              onValueChange={(value: string) => {
                setCategory(value);
                setSkip(0);
              }}
            >
              <TkSelectTrigger className="w-[200px]">
                <TkSelectValue />
              </TkSelectTrigger>
              <TkSelectContent>
                <TkSelectItem value="all">{t('images.mirrored.allCategories')}</TkSelectItem>
                <TkSelectItem value="system">{t('images.mirrored.system')}</TkSelectItem>
                <TkSelectItem value="user">{t('images.mirrored.user')}</TkSelectItem>
              </TkSelectContent>
            </TkSelect>

            <TkSelect
              value={protection}
              onValueChange={(value: string) => {
                setProtection(value);
                setSkip(0);
              }}
            >
              <TkSelectTrigger className="w-[200px]">
                <TkSelectValue />
              </TkSelectTrigger>
              <TkSelectContent>
                <TkSelectItem value="all">{t('images.mirrored.allImages')}</TkSelectItem>
                <TkSelectItem value="true">{t('images.mirrored.protectedOnly')}</TkSelectItem>
                <TkSelectItem value="false">{t('images.mirrored.unprotectedOnly')}</TkSelectItem>
              </TkSelectContent>
            </TkSelect>

            <TkInput
              value={search}
              onChange={(e: ChangeEvent<HTMLInputElement>) => setSearch(e.target.value)}
              placeholder={t('images.mirrored.searchPlaceholder')}
              aria-label={t('images.mirrored.searchPlaceholder')}
              className="w-[300px]"
            />

            {hasFilters && (
              <TkButton intent="ghost" onClick={clearFilters}>
                {t('images.mirrored.clearFilters')}
              </TkButton>
            )}
          </div>
        </TkCardContent>
      </TkCard>

      {error && <TkErrorAlert title={t('images.loadFailed')}>{error}</TkErrorAlert>}

      {loading && images.length === 0 && (
        <TkCard>
          <TkCardContent>
            <div className="flex justify-center py-8">
              <TkLoader size="lg" />
            </div>
          </TkCardContent>
        </TkCard>
      )}

      {images.length > 0 && (
        <TkCard>
          <TkCardContent>
            <div className="overflow-x-auto">
              <TkTable>
                <TkTableHeader>
                  <TkTableRow>
                    <TkTableHead>{t('images.mirrored.colName')}</TkTableHead>
                    <TkTableHead>{t('images.mirrored.colTag')}</TkTableHead>
                    <TkTableHead>{t('images.mirrored.colCategory')}</TkTableHead>
                    <TkTableHead className="hidden xl:table-cell">{t('images.mirrored.colDescription')}</TkTableHead>
                    <TkTableHead className="hidden xl:table-cell">{t('images.mirrored.colMirrorDate')}</TkTableHead>
                    <TkTableHead>{t('images.mirrored.colScan')}</TkTableHead>
                    <TkTableHead>{t('images.colActions')}</TkTableHead>
                  </TkTableRow>
                </TkTableHeader>
                <TkTableBody>
                  {images.map((image) => {
                    const critical = image.vulnerabilities?.critical ?? 0;
                    const high = image.vulnerabilities?.high ?? 0;
                    const scanned = image.vulnerabilities && Object.keys(image.vulnerabilities).length > 0;
                    return (
                      <TkTableRow key={image.id}>
                        <TkTableCell>
                          <div className="font-medium">{image.repository || image.name}</div>
                        </TkTableCell>
                        <TkTableCell>
                          <div className="text-sm font-mono">{image.tag}</div>
                          {image.digest && (
                            <div className="text-xs text-muted-foreground" title={image.digest}>
                              {image.digest.substring(0, 19)}...
                            </div>
                          )}
                        </TkTableCell>
                        <TkTableCell>
                          <div className="flex flex-wrap gap-1">
                            <TkBadge category={image.category === 'user' ? 'user' : 'core'}>
                              {t(`images.mirrored.${image.category}`, { defaultValue: image.category })}
                              {image.protected && <Lock className="h-3 w-3 ml-1" />}
                            </TkBadge>
                            {image.is_base && <TkBadge>{t('images.base')}</TkBadge>}
                          </div>
                        </TkTableCell>
                        <TkTableCell className="hidden xl:table-cell">
                          <div className="max-w-xs truncate" title={image.description ?? undefined}>
                            {image.description || '-'}
                          </div>
                        </TkTableCell>
                        <TkTableCell className="hidden xl:table-cell">
                          <div className="text-sm">{formatDate(image.mirror_date)}</div>
                        </TkTableCell>
                        <TkTableCell>
                          {!scanned ? (
                            <div className="text-sm text-muted-foreground">{t('images.mirrored.notScanned')}</div>
                          ) : (
                            <div className="flex flex-wrap gap-1">
                              {critical > 0 && (
                                <TkBadge status="unhealthy">
                                  {critical} {t('images.severity.critical')}
                                </TkBadge>
                              )}
                              {high > 0 && (
                                <TkBadge status="warning">
                                  {high} {t('images.severity.high')}
                                </TkBadge>
                              )}
                              {critical === 0 && high === 0 && (
                                <div className="text-sm text-muted-foreground">{t('images.mirrored.noneSerious')}</div>
                              )}
                            </div>
                          )}
                        </TkTableCell>
                        <TkTableCell>
                          <div className="flex gap-1">
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => setShown(image)}
                              title={t('images.mirrored.view')}
                              aria-label={t('images.mirrored.view')}
                            >
                              <Eye className="h-4 w-4" />
                            </TkButton>
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => toggleBase(image)}
                              disabled={working}
                              title={t(image.is_base ? 'images.unmarkBase' : 'images.markBase')}
                              aria-label={t(image.is_base ? 'images.unmarkBase' : 'images.markBase')}
                              className={image.is_base ? 'text-primary' : ''}
                            >
                              <Box className="h-4 w-4" />
                            </TkButton>
                            {image.is_base && (
                              <TkButton
                                intent="ghost"
                                size="sm"
                                onClick={() => editTemplate(image)}
                                disabled={working}
                                title={t('images.mirrored.editTemplate')}
                                aria-label={t('images.mirrored.editTemplate')}
                              >
                                <Edit2 className="h-4 w-4" />
                              </TkButton>
                            )}
                            {image.category === 'user' && image.tag === 'latest' && (
                              <TkButton
                                intent="ghost"
                                size="sm"
                                onClick={() => setConfirm({ action: 'remirror', image })}
                                title={t('images.mirrored.remirror')}
                                aria-label={t('images.mirrored.remirror')}
                              >
                                <RefreshCw className="h-4 w-4" />
                              </TkButton>
                            )}
                            {!image.protected && image.category === 'user' && (
                              <TkButton
                                intent="ghost"
                                size="sm"
                                onClick={() => setConfirm({ action: 'delete', image })}
                                title={t('images.mirrored.delete')}
                                aria-label={t('images.mirrored.delete')}
                                className="text-destructive"
                              >
                                <Trash2 className="h-4 w-4" />
                              </TkButton>
                            )}
                          </div>
                        </TkTableCell>
                      </TkTableRow>
                    );
                  })}
                </TkTableBody>
              </TkTable>
            </div>

            {total > PAGE_SIZE && (
              <div className="flex items-center justify-center mt-4 gap-2">
                <TkButton
                  intent="secondary"
                  disabled={page <= 1}
                  onClick={() => setSkip(Math.max(0, skip - PAGE_SIZE))}
                  aria-label={t('images.mirrored.previousPage')}
                >
                  «
                </TkButton>
                <span className="text-sm">{t('images.mirrored.page', { page, pages })}</span>
                <TkButton
                  intent="secondary"
                  disabled={page >= pages}
                  onClick={() => setSkip(skip + PAGE_SIZE)}
                  aria-label={t('images.mirrored.nextPage')}
                >
                  »
                </TkButton>
              </div>
            )}
          </TkCardContent>
        </TkCard>
      )}

      {!loading && !error && images.length === 0 && (
        <TkCard>
          <TkCardContent>
            <div className="text-center py-8">
              <AlertCircle className="h-16 w-16 mx-auto text-muted-foreground mb-4" />
              <p className="text-lg mb-2">{t('images.mirrored.empty')}</p>
              <p className="text-muted-foreground mb-4">
                {t(hasFilters ? 'images.mirrored.emptyFiltered' : 'images.mirrored.emptyHint')}
              </p>
            </div>
          </TkCardContent>
        </TkCard>
      )}

      {adding && <AddImageDialog onStarted={onMirrorStarted} onClose={() => setAdding(false)} />}

      {shown && <ImageDetailsDialog image={shown} onClose={() => setShown(null)} />}

      {confirm && (
        <ConfirmDialog
          title={t(confirm.action === 'remirror' ? 'images.mirrored.remirrorTitle' : 'images.mirrored.deleteTitle')}
          message={t(
            confirm.action === 'remirror' ? 'images.mirrored.remirrorConfirm' : 'images.mirrored.deleteConfirm',
            { name: `${confirm.image.name}:${confirm.image.tag}` },
          )}
          confirmLabel={t(confirm.action === 'remirror' ? 'images.mirrored.remirror' : 'common.delete')}
          danger={confirm.action === 'delete'}
          working={working}
          onConfirm={confirmed}
          onCancel={() => setConfirm(null)}
        />
      )}

      {followed && (
        <ProgressDialog
          followed={followed}
          onFinished={refresh}
          onClose={() => {
            setFollowed(null);
            refresh();
          }}
        />
      )}
    </div>
  );
}

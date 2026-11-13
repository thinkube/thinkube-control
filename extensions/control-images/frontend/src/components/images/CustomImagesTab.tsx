/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkCard, TkCardContent } from 'thinkube-style/components/cards-data';
import {
  TkSelect,
  TkSelectTrigger,
  TkSelectContent,
  TkSelectItem,
  TkSelectValue,
  TkCheckbox,
  TkLabel,
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
import { Plus, Trash2, Edit2, Settings, FileText, Box, Star, RefreshCw } from 'lucide-react';
import api from '@/lib/axios';
import { ConfirmDialog } from './ConfirmDialog';
import { CreateCustomImageDialog } from './CreateCustomImageDialog';
import { ProgressDialog, type Followed } from './ProgressDialog';
import { errorText, formatDate, openInNewTab, statusBadge, type CustomImage } from './types';

// The statuses of an image that was given to a build at least once, so that it has a build log
const HAS_BUILD_LOG = ['building', 'success', 'failed'];

/** The custom images: the list, and creating, building, editing and deleting them. */
export function CustomImagesTab() {
  const { t } = useTranslation();
  const [images, setImages] = useState<CustomImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [scope, setScope] = useState('all');
  const [baseOnly, setBaseOnly] = useState(false);
  const [treeView, setTreeView] = useState(false);

  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<CustomImage | null>(null);
  const [followed, setFollowed] = useState<Followed | null>(null);
  const [working, setWorking] = useState(false);

  const loadImages = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get('/custom-images', { params: { limit: 500 } });
      setImages(data.builds);
      setError(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadImages();
  }, [loadImages]);

  const scopes = useMemo(() => Array.from(new Set(images.map((image) => image.scope))).sort(), [images]);

  const filtered = useMemo(
    () => images.filter((image) => (scope === 'all' || image.scope === scope) && (!baseOnly || image.is_base)),
    [images, scope, baseOnly],
  );

  // The rows of the table: in tree view each image is followed by the images made from it, one level deeper
  const rows = useMemo(() => {
    if (!treeView) return filtered.map((image) => ({ image, depth: 0 }));
    const listed = new Set(filtered.map((image) => image.id));
    const ordered: { image: CustomImage; depth: number }[] = [];
    const add = (image: CustomImage, depth: number) => {
      ordered.push({ image, depth });
      filtered.filter((child) => child.parent_image_id === image.id).forEach((child) => add(child, depth + 1));
    };
    filtered
      .filter((image) => !image.parent_image_id || !listed.has(image.parent_image_id))
      .forEach((image) => add(image, 0));
    return ordered;
  }, [filtered, treeView]);

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

  const followBuild = (image: CustomImage, title: string) => {
    setFollowed({
      title,
      successMessage: t('images.progress.built', { name: image.name }),
      read: async () => {
        const { data } = await api.get(`/custom-images/${image.id}/log`);
        return { status: data.status, log: data.log || '' };
      },
    });
  };

  const buildImage = (image: CustomImage) =>
    run(async () => {
      await api.post(`/custom-images/${image.id}/build`, {});
      followBuild(image, t('images.progress.building', { name: image.name }));
      loadImages();
    });

  const openInIde = (image: CustomImage) =>
    run(async () => {
      const { data } = await api.get(`/custom-images/${image.id}/editor-url`);
      openInNewTab(data.editor_url);
    });

  const toggleBase = (image: CustomImage) =>
    run(async () => {
      const { data } = await api.patch(`/custom-images/${image.id}/toggle-base`);
      toast.success(t(data.is_base ? 'images.custom.markedBase' : 'images.custom.unmarkedBase', { name: image.name }));
      loadImages();
    });

  const deleteConfirmed = () => {
    if (!deleting) return;
    const image = deleting;
    void run(async () => {
      try {
        await api.delete(`/custom-images/${image.id}`);
        toast.success(t('images.custom.deleted', { name: image.name }));
        loadImages();
      } finally {
        setDeleting(null);
      }
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <TkButton onClick={() => setCreating(true)} className="gap-2">
          <Plus className="h-4 w-4" />
          {t('images.custom.create')}
        </TkButton>
        <TkButton intent="ghost" onClick={loadImages} className="gap-2">
          <RefreshCw className="h-4 w-4" />
          {t('images.custom.refresh')}
        </TkButton>
      </div>

      <TkCard>
        <TkCardContent>
          <div className="flex flex-wrap items-center gap-4">
            <TkSelect value={scope} onValueChange={(value: string) => setScope(value)}>
              <TkSelectTrigger className="w-[200px]">
                <TkSelectValue />
              </TkSelectTrigger>
              <TkSelectContent>
                <TkSelectItem value="all">{t('images.custom.allScopes')}</TkSelectItem>
                {scopes.map((name) => (
                  <TkSelectItem key={name} value={name}>
                    {name}
                  </TkSelectItem>
                ))}
              </TkSelectContent>
            </TkSelect>

            <div className="flex items-center gap-2">
              <TkCheckbox
                id="custom-base-only"
                checked={baseOnly}
                onCheckedChange={(checked: boolean | 'indeterminate') => setBaseOnly(checked === true)}
              />
              <TkLabel htmlFor="custom-base-only">{t('images.custom.baseOnly')}</TkLabel>
            </div>

            <div className="flex items-center gap-2">
              <TkCheckbox
                id="custom-tree-view"
                checked={treeView}
                onCheckedChange={(checked: boolean | 'indeterminate') => setTreeView(checked === true)}
              />
              <TkLabel htmlFor="custom-tree-view">{t('images.custom.treeView')}</TkLabel>
            </div>
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

      {rows.length > 0 && (
        <TkCard>
          <TkCardContent>
            <div className="overflow-x-auto">
              <TkTable>
                <TkTableHeader>
                  <TkTableRow>
                    <TkTableHead>{t('images.custom.colName')}</TkTableHead>
                    <TkTableHead>{t('images.custom.colScope')}</TkTableHead>
                    <TkTableHead>{t('images.custom.colType')}</TkTableHead>
                    <TkTableHead>{t('images.custom.colStatus')}</TkTableHead>
                    <TkTableHead className="hidden xl:table-cell">{t('images.custom.colRegistry')}</TkTableHead>
                    <TkTableHead className="hidden xl:table-cell">{t('images.custom.colCreated')}</TkTableHead>
                    <TkTableHead>{t('images.colActions')}</TkTableHead>
                  </TkTableRow>
                </TkTableHeader>
                <TkTableBody>
                  {rows.map(({ image, depth }) => (
                    <TkTableRow key={image.id}>
                      <TkTableCell>
                        <div style={{ paddingLeft: `${depth * 1.5}rem` }}>
                          <div className="font-medium">{image.name}</div>
                          {image.build_config?.description && (
                            <div className="text-xs text-muted-foreground">{image.build_config.description}</div>
                          )}
                          {image.parent_image_id && (
                            <div className="text-xs text-muted-foreground">{t('images.custom.extended')}</div>
                          )}
                        </div>
                      </TkTableCell>
                      <TkTableCell>
                        <TkBadge appearance="outlined">{image.scope}</TkBadge>
                      </TkTableCell>
                      <TkTableCell>
                        {image.is_base ? (
                          <TkBadge>{t('images.base')}</TkBadge>
                        ) : image.parent_image_id ? (
                          <TkBadge appearance="muted">{t('images.custom.typeExtended')}</TkBadge>
                        ) : (
                          <TkBadge appearance="outlined">{t('images.custom.typeStandard')}</TkBadge>
                        )}
                      </TkTableCell>
                      <TkTableCell>
                        <TkBadge status={statusBadge(image.status)}>
                          {t(`images.status.${image.status}`, { defaultValue: image.status })}
                        </TkBadge>
                      </TkTableCell>
                      <TkTableCell className="hidden xl:table-cell">
                        {image.registry_url ? (
                          <div className="text-sm font-mono truncate max-w-xs" title={image.registry_url}>
                            {image.registry_url}
                          </div>
                        ) : (
                          <div className="text-muted-foreground">-</div>
                        )}
                      </TkTableCell>
                      <TkTableCell className="hidden xl:table-cell">
                        <div className="text-sm">{formatDate(image.created_at)}</div>
                      </TkTableCell>
                      <TkTableCell>
                        <div className="flex gap-1">
                          <TkButton
                            intent="ghost"
                            size="sm"
                            onClick={() => openInIde(image)}
                            disabled={working}
                            title={t('images.custom.openIde')}
                            aria-label={t('images.custom.openIde')}
                          >
                            <Edit2 className="h-4 w-4" />
                          </TkButton>
                          {image.status !== 'building' && (
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => buildImage(image)}
                              disabled={working}
                              title={t('images.custom.build')}
                              aria-label={t('images.custom.build')}
                            >
                              <Settings className="h-4 w-4" />
                            </TkButton>
                          )}
                          {HAS_BUILD_LOG.includes(image.status) && (
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => followBuild(image, t('images.progress.buildLog', { name: image.name }))}
                              title={t('images.custom.log')}
                              aria-label={t('images.custom.log')}
                            >
                              <FileText className="h-4 w-4" />
                            </TkButton>
                          )}
                          {image.status === 'success' && (
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => toggleBase(image)}
                              disabled={working}
                              title={t(image.is_base ? 'images.unmarkBase' : 'images.markBase')}
                              aria-label={t(image.is_base ? 'images.unmarkBase' : 'images.markBase')}
                            >
                              <Star className={image.is_base ? 'h-4 w-4 fill-current' : 'h-4 w-4'} />
                            </TkButton>
                          )}
                          {image.status !== 'building' && (
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => setDeleting(image)}
                              title={t('images.custom.delete')}
                              aria-label={t('images.custom.delete')}
                              className="text-destructive"
                            >
                              <Trash2 className="h-4 w-4" />
                            </TkButton>
                          )}
                        </div>
                      </TkTableCell>
                    </TkTableRow>
                  ))}
                </TkTableBody>
              </TkTable>
            </div>
          </TkCardContent>
        </TkCard>
      )}

      {!loading && !error && rows.length === 0 && (
        <TkCard>
          <TkCardContent>
            <div className="text-center py-8">
              <Box className="h-16 w-16 mx-auto text-muted-foreground mb-4" />
              <p className="text-lg mb-2">
                {t(images.length === 0 ? 'images.custom.empty' : 'images.custom.emptyFiltered')}
              </p>
              {images.length === 0 && <p className="text-muted-foreground mb-4">{t('images.custom.emptyHint')}</p>}
            </div>
          </TkCardContent>
        </TkCard>
      )}

      {creating && (
        <CreateCustomImageDialog
          onCreated={(image) => {
            setCreating(false);
            toast.success(t('images.custom.created', { name: image.name }));
            loadImages();
          }}
          onClose={() => setCreating(false)}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title={t('images.custom.deleteTitle')}
          message={t('images.custom.deleteConfirm', { name: deleting.name })}
          confirmLabel={t('common.delete')}
          danger
          working={working}
          onConfirm={deleteConfirmed}
          onCancel={() => setDeleting(null)}
        />
      )}

      {followed && (
        <ProgressDialog
          followed={followed}
          onFinished={loadImages}
          onClose={() => {
            setFollowed(null);
            loadImages();
          }}
        />
      )}
    </div>
  );
}

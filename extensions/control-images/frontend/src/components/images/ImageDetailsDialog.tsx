/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkCodeBlock } from 'thinkube-style/components/feedback';
import { Lock } from 'lucide-react';
import { formatDate, type MirroredImage } from './types';

/** A size in bytes, which the API gives as text, in the largest unit that keeps it above 1. */
function formatSize(size: string): string {
  const bytes = Number(size);
  if (!Number.isFinite(bytes) || bytes < 0) return size;
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-sm font-semibold text-foreground">{label}</div>
      <div className="mt-1">{children}</div>
    </div>
  );
}

const SEVERITIES: { key: string; status: 'unhealthy' | 'warning' | 'pending' }[] = [
  { key: 'critical', status: 'unhealthy' },
  { key: 'high', status: 'warning' },
  { key: 'medium', status: 'warning' },
  { key: 'low', status: 'pending' },
];

interface ImageDetailsDialogProps {
  image: MirroredImage;
  onClose: () => void;
}

/** Everything the list knows of one image. Mount it only while an image is shown. */
export function ImageDetailsDialog({ image, onClose }: ImageDetailsDialogProps) {
  const { t } = useTranslation();
  const found = SEVERITIES.filter(({ key }) => (image.vulnerabilities?.[key] ?? 0) > 0);

  return (
    <TkDialogRoot open onOpenChange={(open: boolean) => !open && onClose()}>
      <TkDialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
        <TkDialogHeader>
          <TkDialogTitle>{t('images.details.title')}</TkDialogTitle>
        </TkDialogHeader>

        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4">
            <Field label={t('images.details.name')}>
              <span className="text-lg">{image.name}</span>
            </Field>
            <Field label={t('images.details.tag')}>
              <TkBadge appearance="outlined">{image.tag}</TkBadge>
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label={t('images.details.category')}>
              <TkBadge category={image.category === 'user' ? 'user' : 'core'}>
                {t(`images.mirrored.${image.category}`, { defaultValue: image.category })}
              </TkBadge>
            </Field>
            <Field label={t('images.details.protection')}>
              {image.protected ? (
                <div className="flex items-center gap-2">
                  <Lock className="h-5 w-5 text-success" />
                  <span>{t('images.details.protected')}</span>
                </div>
              ) : (
                <span className="text-muted-foreground">{t('images.details.notProtected')}</span>
              )}
            </Field>
          </div>

          <Field label={t('images.details.registryUrl')}>
            <TkCodeBlock>{image.destination_url}</TkCodeBlock>
          </Field>

          {image.source_url && (
            <Field label={t('images.details.sourceUrl')}>
              <TkCodeBlock>{image.source_url}</TkCodeBlock>
            </Field>
          )}

          {image.description && (
            <Field label={t('images.details.description')}>
              <p className="text-base">{image.description}</p>
            </Field>
          )}

          <div className="grid grid-cols-2 gap-4">
            <Field label={t('images.details.mirrorDate')}>
              {formatDate(image.mirror_date, t('images.details.never'))}
            </Field>
            <Field label={t('images.details.lastSynced')}>
              {formatDate(image.last_synced, t('images.details.never'))}
            </Field>
          </div>

          {image.digest && (
            <Field label={t('images.details.digest')}>
              <span className="font-mono text-xs break-all">{image.digest}</span>
            </Field>
          )}

          {image.size_bytes && (
            <Field label={t('images.details.size')}>{formatSize(image.size_bytes)}</Field>
          )}

          {found.length > 0 && (
            <Field label={t('images.details.vulnerabilities')}>
              <div className="flex flex-wrap gap-2">
                {found.map(({ key, status }) => (
                  <TkBadge key={key} status={status}>
                    {image.vulnerabilities[key]} {t(`images.severity.${key}`)}
                  </TkBadge>
                ))}
              </div>
            </Field>
          )}

          {image.metadata && Object.keys(image.metadata).length > 0 && (
            <Field label={t('images.details.metadata')}>
              <TkCodeBlock>{JSON.stringify(image.metadata, null, 2)}</TkCodeBlock>
            </Field>
          )}
        </div>

        <TkDialogFooter>
          <TkButton onClick={onClose}>{t('common.close')}</TkButton>
        </TkDialogFooter>
      </TkDialogContent>
    </TkDialogRoot>
  );
}

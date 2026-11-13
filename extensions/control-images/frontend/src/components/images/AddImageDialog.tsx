/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, type ChangeEvent, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkInput, TkTextarea, TkLabel } from 'thinkube-style/components/forms-inputs';
import { TkErrorAlert } from 'thinkube-style/components/feedback';
import { Loader2 } from 'lucide-react';
import api from '@/lib/axios';
import { errorText, type MirrorStarted } from './types';

// An image address with its tag: registry/path/name:tag, the registry with or without a port
const IMAGE_WITH_TAG = /^[a-zA-Z0-9][a-zA-Z0-9./_:-]*:[a-zA-Z0-9._-]+$/;

interface AddImageDialogProps {
  /** Called with the image and its job once the server has started the mirror. */
  onStarted: (started: MirrorStarted) => void;
  onClose: () => void;
}

/** Asks for a public image and starts mirroring it into Harbor. Mount it only while it is open. */
export function AddImageDialog({ onStarted, onClose }: AddImageDialogProps) {
  const { t } = useTranslation();
  const [sourceUrl, setSourceUrl] = useState('');
  const [description, setDescription] = useState('');
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const source = sourceUrl.trim();
  const invalid = source !== '' && !IMAGE_WITH_TAG.test(source);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!source || invalid) return;
    setWorking(true);
    setError(null);
    try {
      const { data } = await api.post('/images', { source_url: source, description });
      onStarted(data);
    } catch (err) {
      setError(errorText(err));
      setWorking(false);
    }
  };

  return (
    <TkDialogRoot open onOpenChange={(open: boolean) => !open && onClose()}>
      <TkDialogContent className="max-w-2xl">
        <TkDialogHeader>
          <TkDialogTitle>{t('images.add.title')}</TkDialogTitle>
        </TkDialogHeader>

        <form onSubmit={submit}>
          <div className="space-y-4">
            <div className="space-y-2">
              <TkLabel htmlFor="image-source">{t('images.add.sourceLabel')}</TkLabel>
              <TkInput
                id="image-source"
                type="text"
                placeholder="docker.io/library/nginx:1.27"
                value={sourceUrl}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setSourceUrl(e.target.value)}
                className={invalid ? 'border-destructive' : ''}
                required
              />
              {invalid && <p className="text-sm text-destructive">{t('images.add.invalidSource')}</p>}
              <p className="text-sm text-muted-foreground">{t('images.add.sourceHelp')}</p>
            </div>

            <div className="space-y-2">
              <TkLabel htmlFor="image-description">{t('images.add.descriptionLabel')}</TkLabel>
              <TkTextarea
                id="image-description"
                placeholder={t('images.add.descriptionPlaceholder')}
                value={description}
                onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setDescription(e.target.value)}
                className="h-24"
              />
            </div>

            <p className="text-sm text-muted-foreground">{t('images.add.note')}</p>

            {error && <TkErrorAlert title={t('images.add.failed')}>{error}</TkErrorAlert>}
          </div>

          <TkDialogFooter className="mt-6">
            <TkButton type="button" intent="secondary" onClick={onClose} disabled={working}>
              {t('common.cancel')}
            </TkButton>
            <TkButton type="submit" disabled={working || !source || invalid} className="gap-2">
              {working && <Loader2 className="h-4 w-4 animate-spin" />}
              {t('images.add.submit')}
            </TkButton>
          </TkDialogFooter>
        </form>
      </TkDialogContent>
    </TkDialogRoot>
  );
}

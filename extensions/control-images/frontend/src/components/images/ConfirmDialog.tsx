/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useTranslation } from 'react-i18next';
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { Loader2 } from 'lucide-react';

interface ConfirmDialogProps {
  title: string;
  message: string;
  confirmLabel: string;
  /** The action cannot be undone: the confirm button is drawn as dangerous. */
  danger?: boolean;
  working: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Asks before an action runs. Mount it only while there is something to confirm. */
export function ConfirmDialog({ title, message, confirmLabel, danger, working, onConfirm, onCancel }: ConfirmDialogProps) {
  const { t } = useTranslation();
  return (
    <TkDialogRoot open onOpenChange={(open: boolean) => !open && onCancel()}>
      <TkDialogContent>
        <TkDialogHeader>
          <TkDialogTitle>{title}</TkDialogTitle>
        </TkDialogHeader>
        <p className="text-sm">{message}</p>
        <TkDialogFooter>
          <TkButton intent="ghost" onClick={onCancel} disabled={working}>
            {t('common.cancel')}
          </TkButton>
          <TkButton intent={danger ? 'danger' : undefined} onClick={onConfirm} disabled={working} className="gap-2">
            {working && <Loader2 className="h-4 w-4 animate-spin" />}
            {confirmLabel}
          </TkButton>
        </TkDialogFooter>
      </TkDialogContent>
    </TkDialogRoot>
  );
}

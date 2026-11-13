/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkErrorAlert, TkSuccessAlert } from 'thinkube-style/components/feedback';
import { Loader2 } from 'lucide-react';
import { POLL_INTERVAL_MS, errorText, statusBadge } from './types';

/** One reading of a mirror or a build that runs on the server. */
export interface Progress {
  status: string;
  log: string;
  error?: string | null;
}

/** A mirror or a build to follow: how to read it, and what to say about it. */
export interface Followed {
  title: string;
  successMessage: string;
  read: () => Promise<Progress>;
}

// Readings that may fail in a row before the dialog stops asking
const MAX_FAILED_READS = 5;

const hasEnded = (status: string) => status === 'success' || status === 'failed';

interface ProgressDialogProps {
  followed: Followed;
  /** Called once, when the followed work ends in success or failed. */
  onFinished: () => void;
  onClose: () => void;
}

/**
 * Follows a mirror or a build: reads it when it opens and then every
 * POLL_INTERVAL_MS, shows its status and its log, and stops when it ends or
 * when the dialog goes away. Mount it only while there is something to follow.
 */
export function ProgressDialog({ followed, onFinished, onClose }: ProgressDialogProps) {
  const { t } = useTranslation();
  const [progress, setProgress] = useState<Progress | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [following, setFollowing] = useState(true);
  const logBox = useRef<HTMLPreElement>(null);
  const finished = useRef(onFinished);

  useEffect(() => {
    finished.current = onFinished;
  });

  useEffect(() => {
    let stopped = false;
    let reading = false;
    let failedReads = 0;

    const read = async () => {
      if (stopped || reading) return;
      reading = true;
      try {
        const next = await followed.read();
        if (stopped) return;
        failedReads = 0;
        setReadError(null);
        setProgress(next);
        if (hasEnded(next.status)) {
          stop();
          setFollowing(false);
          finished.current();
        }
      } catch (err) {
        if (stopped) return;
        failedReads += 1;
        setReadError(errorText(err));
        if (failedReads >= MAX_FAILED_READS) {
          stop();
          setFollowing(false);
        }
      } finally {
        reading = false;
      }
    };

    const timer = setInterval(read, POLL_INTERVAL_MS);
    const stop = () => {
      stopped = true;
      clearInterval(timer);
    };
    void read();
    return stop;
  }, [followed]);

  // Keeps the newest lines of the log in view
  useEffect(() => {
    if (logBox.current) {
      logBox.current.scrollTop = logBox.current.scrollHeight;
    }
  }, [progress?.log]);

  const status = progress?.status;

  return (
    <TkDialogRoot open onOpenChange={(open: boolean) => !open && onClose()}>
      <TkDialogContent className="max-w-4xl">
        <TkDialogHeader>
          <TkDialogTitle>{followed.title}</TkDialogTitle>
        </TkDialogHeader>

        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm">
            {following && <Loader2 className="h-4 w-4 animate-spin" />}
            <span>{t('images.progress.status')}</span>
            <TkBadge status={statusBadge(status ?? 'pending')}>
              {status ? t(`images.status.${status}`, { defaultValue: status }) : t('images.progress.reading')}
            </TkBadge>
          </div>

          {status === 'success' && (
            <TkSuccessAlert title={t('images.progress.succeeded')}>{followed.successMessage}</TkSuccessAlert>
          )}
          {status === 'failed' && (
            <TkErrorAlert title={t('images.progress.failed')}>
              {progress?.error || t('images.progress.seeLog')}
            </TkErrorAlert>
          )}
          {readError && (
            <TkErrorAlert title={t('images.progress.readFailed')}>{readError}</TkErrorAlert>
          )}

          <pre
            ref={logBox}
            className="h-80 overflow-auto rounded-md border bg-muted p-3 font-mono text-xs whitespace-pre-wrap break-words"
          >
            {progress?.log || t('images.progress.noLog')}
          </pre>
        </div>

        <TkDialogFooter>
          <TkButton onClick={onClose}>{t('common.close')}</TkButton>
        </TkDialogFooter>
      </TkDialogContent>
    </TkDialogRoot>
  );
}

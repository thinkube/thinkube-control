/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import {
  TkCard,
  TkCardContent,
  TkCardHeader,
  TkCardTitle,
} from 'thinkube-style/components/cards-data';

export default function NotFoundPage() {
  const { t } = useTranslation();

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-8">
      <TkCard className="w-full max-w-md">
        <TkCardHeader className="items-center text-center">
          <div className="mb-6">
            <span className="text-9xl font-bold text-primary">{t('errors.notFound.title')}</span>
          </div>
          <TkCardTitle className="text-2xl">{t('errors.notFound.heading')}</TkCardTitle>
        </TkCardHeader>
        <TkCardContent className="space-y-6 text-center">
          <p className="text-muted-foreground">{t('errors.notFound.message')}</p>
          <Link to="/">
            <TkButton className="w-full">{t('errors.notFound.backButton')}</TkButton>
          </Link>
        </TkCardContent>
      </TkCard>
    </div>
  );
}

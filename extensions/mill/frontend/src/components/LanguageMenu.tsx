/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { Languages } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { TkDropdownMenu } from 'thinkube-style/components/navigation';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { setLocale, SUPPORTED_LOCALES, Locale } from '@/i18n';

const LOCALE_NAMES: Record<Locale, string> = {
  en: 'English',
  es: 'Espanol',
  ca: 'Catala',
};

export function LanguageMenu() {
  const { t, i18n } = useTranslation();
  const current = (i18n.resolvedLanguage || 'en') as Locale;

  const groups = [
    {
      label: t('language.label'),
      items: SUPPORTED_LOCALES.map((locale) => ({
        label: LOCALE_NAMES[locale],
        onClick: () => setLocale(locale),
      })),
    },
  ];

  return (
    <TkDropdownMenu
      width="w-40"
      trigger={
        <TkButton intent="ghost" size="sm" aria-label={t('language.label')}>
          <Languages className="h-4 w-4" />
          <span>{current.toUpperCase()}</span>
        </TkButton>
      }
      groups={groups}
    />
  );
}

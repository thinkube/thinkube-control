/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from '@/locales/en.json';
import es from '@/locales/es.json';
import ca from '@/locales/ca.json';

export const SUPPORTED_LOCALES = ['en', 'es', 'ca'] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];

const LOCALE_KEY = 'locale';

const isSupported = (value: string | null): value is Locale =>
  !!value && (SUPPORTED_LOCALES as readonly string[]).includes(value);

/** Saved preference first, then the browser's language, then English. */
function initialLocale(): Locale {
  const saved = localStorage.getItem(LOCALE_KEY);
  if (isSupported(saved)) return saved;

  const browser = navigator.language?.split('-')[0] ?? '';
  return isSupported(browser) ? browser : 'en';
}

export function setLocale(locale: Locale) {
  localStorage.setItem(LOCALE_KEY, locale);
  i18n.changeLanguage(locale);
}

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    es: { translation: es },
    ca: { translation: ca },
  },
  lng: initialLocale(),
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
});

export default i18n;

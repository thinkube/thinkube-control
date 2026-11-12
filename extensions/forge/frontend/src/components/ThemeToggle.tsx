/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { Moon, Sun } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { useTheme } from './ThemeProvider';

export function ThemeToggle() {
  const { actualTheme, setTheme } = useTheme();
  const { t } = useTranslation();

  return (
    <TkButton
      intent="ghost"
      size="icon"
      aria-label={t('theme.toggle')}
      onClick={() => setTheme(actualTheme === 'dark' ? 'light' : 'dark')}
    >
      {actualTheme === 'dark' ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
      <span className="sr-only">{t('theme.toggle')}</span>
    </TkButton>
  );
}

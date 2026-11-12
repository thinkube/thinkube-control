/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useTranslation } from 'react-i18next';
import { TkSelect, TkSelectContent, TkSelectItem, TkSelectTrigger, TkSelectValue } from 'thinkube-style/components/forms-inputs';
import { PRIORITIES } from '@/lib/forge';

/** A priority from 1 (highest) to 5 (lowest). */
export function PrioritySelect({ value, onChange, label }: { value: number; onChange: (p: number) => void; label: string }) {
  const { t } = useTranslation();
  return (
    <TkSelect value={String(value)} onValueChange={(v) => onChange(Number(v))}>
      <TkSelectTrigger aria-label={label} className="w-36">
        <TkSelectValue />
      </TkSelectTrigger>
      <TkSelectContent>
        {PRIORITIES.map((p) => (
          <TkSelectItem key={p} value={String(p)}>
            {t(`priority.value.${p}`)}
          </TkSelectItem>
        ))}
      </TkSelectContent>
    </TkSelect>
  );
}

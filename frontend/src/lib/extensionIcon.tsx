/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { forwardRef } from 'react';
import type { LucideIcon, LucideProps } from 'lucide-react';
import { DynamicIcon, type IconName } from 'lucide-react/dynamic';

const cache = new Map<string, LucideIcon>();

/** A navigation icon an extension names; lucide loads only the icons used. */
export function extensionIcon(name: string): LucideIcon {
  let icon = cache.get(name);
  if (!icon) {
    icon = forwardRef<SVGSVGElement, LucideProps>((props, ref) => (
      <DynamicIcon ref={ref} name={name as IconName} {...props} />
    )) as unknown as LucideIcon;
    cache.set(name, icon);
  }
  return icon;
}

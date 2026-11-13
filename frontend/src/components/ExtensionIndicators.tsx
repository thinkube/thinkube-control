/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { extensionBase, extensionRequest, type Extension } from '@/lib/extensions';
import { extensionIcon } from '@/lib/extensionIcon';

const POLL_MS = 10_000;

/** One extension's count in the top bar, read from its indicator api every
 * 10 seconds and shown while it is above zero. A failed read shows nothing
 * until a read succeeds: the extension's own page reports its errors. */
function ExtensionIndicator({ extension }: { extension: Extension & { indicator: NonNullable<Extension['indicator']> } }) {
  const navigate = useNavigate();
  const [count, setCount] = useState(0);
  const { name, indicator } = extension;

  useEffect(() => {
    const request = extensionRequest(name);
    let stopped = false;
    const read = () =>
      request({ method: 'GET', url: indicator.api })
        .then((response) => {
          if (!stopped) setCount(Number(response.data?.count) || 0);
        })
        .catch(() => {
          if (!stopped) setCount(0);
        });
    read();
    const timer = setInterval(read, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [name, indicator.api]);

  if (count <= 0) return null;
  const Icon = extensionIcon(indicator.icon);
  return (
    <button
      onClick={() => navigate(`${extensionBase(name)}${indicator.path === '/' ? '' : indicator.path}`)}
      className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-200 dark:hover:bg-blue-900/50 transition-colors"
      title={indicator.title}
    >
      <Icon className="w-4 h-4" />
      <span className="text-sm font-medium">{count}</span>
    </button>
  );
}

/** The top-bar counts of the extensions that declare an indicator and can be loaded. */
export function ExtensionIndicators({ extensions }: { extensions: Extension[] }) {
  return (
    <>
      {extensions
        .filter((e): e is Extension & { indicator: NonNullable<Extension['indicator']> } => !!e.indicator && e.problems.length === 0)
        .map((e) => (
          <ExtensionIndicator key={e.name} extension={e} />
        ))}
    </>
  );
}

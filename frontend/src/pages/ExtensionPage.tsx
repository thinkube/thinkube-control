/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { TkErrorAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import {
  extensionBase,
  extensionRequest,
  uiUrl,
  type Extension,
  type ExtensionHost,
  type ExtensionModule,
} from '@/lib/extensions';

/** Draws an extension's pages inside Control's layout: loads its module and
 * stylesheet through Control, mounts it, and keeps it told where it is. */
export default function ExtensionPage({ extensions }: { extensions: Extension[] }) {
  const { name = '' } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const element = useRef<HTMLDivElement>(null);
  const listeners = useRef(new Set<(path: string) => void>());
  const [error, setError] = useState<string | null>(null);
  const extension = extensions.find((e) => e.name === name);
  const base = extensionBase(name);

  // navigate may change with each move; the host keeps one, so the extension is mounted once
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;

  const relative = location.pathname.slice(base.length) || '/';
  const current = useRef(relative + location.search);
  current.current = relative + location.search;

  // Each move of the address, by Control's navigation or the extension's own, reaches the extension
  useEffect(() => {
    listeners.current.forEach((listener) => listener(current.current));
  }, [relative, location.search]);

  const host = useMemo<ExtensionHost>(
    () => ({
      basename: base,
      path: () => current.current,
      listen: (listener) => {
        listeners.current.add(listener);
        return () => listeners.current.delete(listener);
      },
      navigate: (to, options) => navigateRef.current(`${base}${to === '/' ? '' : to}`, { replace: options?.replace }),
      request: extensionRequest(name),
    }),
    [base, name],
  );

  useEffect(() => {
    if (!extension || !element.current) return;
    setError(null);
    let unmount: (() => void) | undefined;
    let cancelled = false;
    let stylesheet: HTMLLinkElement | undefined;
    if (extension.ui.css) {
      stylesheet = document.createElement('link');
      stylesheet.rel = 'stylesheet';
      stylesheet.href = uiUrl(name, extension.ui.css);
      document.head.appendChild(stylesheet);
    }
    import(/* @vite-ignore */ uiUrl(name, extension.ui.module))
      .then((module: ExtensionModule) => {
        if (cancelled || !element.current) return;
        if (typeof module.mount !== 'function') {
          throw new Error(`${extension.ui.module} exports no mount function`);
        }
        unmount = module.mount(element.current, host);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
      unmount?.();
      stylesheet?.remove();
    };
    // Mounted again only when the module or stylesheet changes, not when the list is read again
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extension?.ui.module, extension?.ui.css, name, host]);

  if (!extension) {
    return (
      <TkPageWrapper>
        <TkErrorAlert title="Extension not found">No enabled extension is named {name}.</TkErrorAlert>
      </TkPageWrapper>
    );
  }
  return (
    <>
      {error && (
        <TkPageWrapper>
          <TkErrorAlert title={`${extension.title} could not be loaded`}>{error}</TkErrorAlert>
        </TkPageWrapper>
      )}
      <div ref={element} />
    </>
  );
}

/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * The app as an extension of Thinkube Control.
 *
 * Control loads this module (built by vite.extension.config.ts into
 * extension/index.js) and calls mount() with the element to draw into and the
 * host: Control owns the address and the session, so the app's router
 * follows Control's and its API calls go through Control.
 */

import { StrictMode, useEffect, useState, type ComponentProps, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { Router, createPath, type To } from 'react-router-dom';
import type { AxiosRequestConfig, AxiosResponse } from 'axios';
import { Toaster } from 'sonner';
// Control has loaded the fonts, the base styles and thinkube-style's theme; the app adds its own utility classes
import './extension.css';
import '@/i18n';
import { AppRoutes } from '@/App';
import { hostRequests } from '@/lib/axios';

/** What Control hands an extension (thinkube-control frontend/src/lib/extensions.ts). */
interface ExtensionHost {
  basename: string;
  path: () => string;
  listen: (listener: (path: string) => void) => () => void;
  navigate: (to: string, options?: { replace?: boolean }) => void;
  request: (config: AxiosRequestConfig) => Promise<AxiosResponse>;
}

/** A router whose address is Control's: it shows Control's current path and asks Control to move. */
function HostRouter({ host, children }: { host: ExtensionHost; children: ReactNode }) {
  const [path, setPath] = useState(host.path());
  useEffect(() => host.listen(setPath), [host]);
  const toPath = (to: To) => (typeof to === 'string' ? to : createPath(to));
  const navigator: ComponentProps<typeof Router>['navigator'] = {
    createHref: (to) => `${host.basename}${toPath(to)}`,
    push: (to) => host.navigate(toPath(to)),
    replace: (to) => host.navigate(toPath(to), { replace: true }),
    go: (delta) => window.history.go(delta),
  };
  return (
    <Router location={path} navigator={navigator}>
      {children}
    </Router>
  );
}

export function mount(element: HTMLElement, host: ExtensionHost): () => void {
  hostRequests(host.request);
  const root = createRoot(element);
  root.render(
    <StrictMode>
      <HostRouter host={host}>
        <Toaster richColors position="top-right" />
        <AppRoutes />
      </HostRouter>
    </StrictMode>,
  );
  return () => root.unmount();
}

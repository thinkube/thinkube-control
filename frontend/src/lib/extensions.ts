/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import type { AxiosRequestConfig, AxiosResponse } from 'axios';
import api from './axios';

/** An extension as Control's registry lists it (backend app/services/extensions.py). */
export interface Extension {
  name: string;
  title: string;
  description: string;
  icon: string | null;
  platform: string;
  depends: string[];
  ui: { module: string; css: string | null; folder: string };
  nav: { label: string; path: string; icon: string | null }[];
  /** Why its pages cannot be loaded: another platform version, or depends not installed. */
  problems: string[];
}

/** What Control hands an extension's module when it mounts it.
 *
 * Control owns the address: `path()` is the extension's current path below
 * `basename`, `listen` reports each change, and `navigate` moves to a path of
 * the extension. `request` calls the extension's own API (its /api/v1) with
 * the user's session. */
export interface ExtensionHost {
  basename: string;
  path: () => string;
  listen: (listener: (path: string) => void) => () => void;
  navigate: (to: string, options?: { replace?: boolean }) => void;
  request: (config: AxiosRequestConfig) => Promise<AxiosResponse>;
}

/** The module an extension serves: mount draws its pages into `element` and returns the function that removes them. */
export interface ExtensionModule {
  mount: (element: HTMLElement, host: ExtensionHost) => () => void;
}

export const extensionBase = (name: string) => `/x/${name}`;

export async function listExtensions(): Promise<Extension[]> {
  return (await api.get<Extension[]>('/extensions')).data;
}

/** A call to the extension's API through Control: /api/v1/x/<name>/api/<path>. */
export function extensionRequest(name: string) {
  return (config: AxiosRequestConfig) =>
    api.request({
      ...config,
      url: `/x/${name}/api${config.url ?? ''}`,
      // An upload stays multipart: under Control's JSON default axios would turn the form into JSON
      ...(config.data instanceof FormData ? { headers: { ...config.headers, 'Content-Type': 'multipart/form-data' } } : {}),
    });
}

export const uiUrl = (name: string, file: string) => `/api/v1/x/${name}/ui${file}`;

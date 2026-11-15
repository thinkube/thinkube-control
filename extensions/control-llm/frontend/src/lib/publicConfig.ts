/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

// Variables the deployment chose to show the browser.
//
// The platform sets them in the container from this container's publicEnv in
// thinkube.yaml, and public-config.sh writes them into config.js, which
// index.html loads before the bundle. Nothing is published unless named there,
// so a value is absent whenever the app runs outside the platform.

declare global {
  interface Window {
    __PUBLIC_CONFIG__?: Record<string, string>;
  }
}

/** The deployment's value for `name`, or undefined when it published none. */
export function publicValue(name: string): string | undefined {
  return window.__PUBLIC_CONFIG__?.[name] || undefined;
}

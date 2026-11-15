/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

/** The reason a failed call gives: the API's detail, else the client's own message. */
export function errorText(err: unknown): string {
  const failure = err as { response?: { data?: { detail?: unknown } }; message?: string } | null;
  const detail = failure?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail) return JSON.stringify(detail);
  return failure?.message || String(err);
}

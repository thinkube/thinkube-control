/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect } from 'react';
import { Bell } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useFixesStore } from '../stores/useFixesStore';

/** The number of fixes to apply and news not yet read; hidden when there are none. */
export function FixesIndicator() {
  const navigate = useNavigate();
  const { feed, fetchFeed, startPolling, stopPolling } = useFixesStore();

  useEffect(() => {
    fetchFeed();
    startPolling();
    return () => stopPolling();
  }, []);

  const count = (feed?.pending_fixes ?? 0) + (feed?.unread_news ?? 0);
  if (count === 0) {
    return null;
  }

  return (
    <button
      onClick={() => navigate('/news')}
      className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-amber-100 dark:bg-amber-900/30 text-amber-800 dark:text-amber-300 hover:bg-amber-200 dark:hover:bg-amber-900/50 transition-colors"
      title="News and fixes"
    >
      <Bell className="w-4 h-4" />
      <span className="text-sm font-medium">{count}</span>
    </button>
  );
}

/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { User, LogOut, Key } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { TkDropdownMenu } from 'thinkube-style/components/navigation';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { useAuthStore } from '@/stores/useAuthStore';

export function UserMenu() {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();
  const { t } = useTranslation();

  if (!user) return null;

  const displayName = user.preferred_username || user.email || 'User';

  const groups = [
    {
      label: t('nav.account'),
      items: [
        {
          label: displayName,
          icon: User,
        },
      ],
    },
    {
      items: [
        {
          label: t('nav.apiTokens'),
          icon: Key,
          onClick: () => navigate('/tokens'),
        },
        {
          label: t('nav.logout'),
          icon: LogOut,
          variant: 'destructive' as const,
          onClick: () => logout(),
        },
      ],
    },
  ];

  return (
    <TkDropdownMenu
      trigger={
        <TkButton intent="ghost">
          <User className="w-4 h-4" />
          {displayName}
        </TkButton>
      }
      groups={groups}
    />
  );
}

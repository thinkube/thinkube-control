import { TkDropdownMenu } from 'thinkube-style';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { User, LogOut } from 'lucide-react';
import { useAuthStore } from '@/stores/useAuthStore';
import { useNavigate } from 'react-router-dom';

export function UserMenu() {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  if (!user) return null;

  const menuGroups = [
    {
      label: 'Account',
      items: [
        {
          label: user.username || user.email || 'User',
          icon: User,
          onClick: () => {}, // No action, just shows username
        },
      ],
    },
    {
      items: [
        {
          label: 'Logout',
          icon: LogOut,
          onClick: handleLogout,
        },
      ],
    },
  ];

  return (
    <TkDropdownMenu
      trigger={
        <TkButton variant="ghost">
          <User className="w-4 h-4" />
          {user.username || user.email}
        </TkButton>
      }
      groups={menuGroups}
    />
  );
}

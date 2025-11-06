import { Moon, Sun } from 'lucide-react';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { useTheme } from './ThemeProvider';

export function ThemeToggle() {
  const { actualTheme, setTheme } = useTheme();

  return (
    <TkButton
      variant="ghost"
      size="icon"
      onClick={() => setTheme(actualTheme === 'dark' ? 'light' : 'dark')}
    >
      {actualTheme === 'dark' ? (
        <Sun className="h-5 w-5" />
      ) : (
        <Moon className="h-5 w-5" />
      )}
      <span className="sr-only">Toggle theme</span>
    </TkButton>
  );
}

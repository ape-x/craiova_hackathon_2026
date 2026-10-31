import { Moon, Sun } from 'lucide-react';
import { toggleTheme, useTheme } from './theme';

export default function ThemeToggle() {
  const dark = useTheme() === 'dark';
  const label = dark ? 'Switch to light mode' : 'Switch to dark mode';
  return (
    <button
      type="button"
      className="icon-button theme-toggle"
      onClick={toggleTheme}
      aria-label={label}
      title={label}
    >
      {dark ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}

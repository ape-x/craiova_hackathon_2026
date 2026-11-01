import { useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { api } from './api';
import { Brand, Tag } from './ui';
import ThemeToggle from './ThemeToggle';

export default function Login({
  onSuccess,
  method = 'password',
}: {
  onSuccess: () => void;
  method?: 'password' | 'token' | null;
}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const tokenLogin = method === 'token';
  return (
    <main className="login-page">
      <div className="page-theme-toggle">
        <ThemeToggle />
      </div>
      <Brand />
      <form
        aria-label="Operator sign in"
        aria-busy={busy}
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy) return;
          setBusy(true);
          setError('');
          try {
            if (tokenLogin) await api.loginWithToken(password);
            else await api.login(username, password);
            setPassword('');
            onSuccess();
          } catch (cause) {
            setError(cause instanceof Error ? cause.message : 'Sign in failed. Try again.');
          } finally {
            setBusy(false);
          }
        }}
      >
        <Tag tone="cyan">OPERATOR ACCESS</Tag>
        <h1>
          Your operations.
          <br />
          One clear picture.
        </h1>
        <p>
          {tokenLogin
            ? 'Enter your operator access token.'
            : 'Sign in with your operator username and password.'}
        </p>
        {!tokenLogin && (
          <label className="field">
            Username
            <input
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              maxLength={128}
              disabled={busy}
              required
            />
          </label>
        )}
        <label className="field">
          {tokenLogin ? 'Operator access token' : 'Password'}
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            maxLength={tokenLogin ? 512 : 1024}
            disabled={busy}
            required
          />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button className="primary-button" type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
          <ArrowRight size={16} />
        </button>
      </form>
      <small>ARIA · Operations workspace</small>
    </main>
  );
}

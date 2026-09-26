import { useState, type FormEvent } from 'react';
import { useAuth } from '../auth';
import { errMsg } from '../ui';

export default function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(errMsg(err) === 'invalid credentials' ? 'Wrong username or password' : errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="brand brand-lg">
          <span className="logo">P</span>
          <span>Parking Control</span>
        </div>
        <p className="muted">Supervisor &amp; admin dashboard — edge server</p>
        <label className="field">
          <span>Username</span>
          <input name="username" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && (
          <div className="error-box" role="alert">
            {error}
          </div>
        )}
        <button className="btn btn-primary btn-block" type="submit" disabled={busy || !username || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}

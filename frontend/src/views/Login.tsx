import { useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { LogIn } from 'lucide-react';
import { api, type User } from '../lib/api';
import { Button, Field, Input } from '../components/ui';

export default function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const reduce = useReducedMotion();

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      const user = await api<User>('/auth/login', { body: { username, password } });
      onLogin(user);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center px-4">
      <motion.section
        className="glass noise w-full max-w-sm p-7"
        initial={reduce ? undefined : { opacity: 0, y: 18 }}
        animate={reduce ? undefined : { opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      >
        <div className="mb-6 flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-accent shadow-[0_0_18px_rgba(106,166,255,.9)]" />
          <span className="text-sm font-semibold tracking-wide">netcfg-evidence</span>
        </div>
        <h1 className="text-lg font-semibold text-slate-100">登录</h1>
        <p className="mt-1 text-xs leading-relaxed text-muted">本地账号。编辑者修改配置，审批者做决定。</p>
        <form onSubmit={submit} className="mt-6 space-y-3">
          <Field label="用户名">
            <Input value={username} autoComplete="username" onChange={(event) => setUsername(event.target.value)} data-testid="login-username" />
          </Field>
          <Field label="密码">
            <Input type="password" value={password} autoComplete="current-password" onChange={(event) => setPassword(event.target.value)} data-testid="login-password" />
          </Field>
          <Button type="submit" variant="primary" className="w-full" disabled={busy || !username || !password} data-testid="login-submit">
            <LogIn size={14} /> 登录
          </Button>
          {error && <p className="text-xs text-danger" data-testid="login-error">{error}</p>}
        </form>
        <p className="mt-6 text-[11px] leading-relaxed text-muted">
          首次使用：<code className="mono rounded bg-white/5 px-1.5 py-0.5">make user USER=你的名字 ROLE=admin</code>
        </p>
      </motion.section>
    </main>
  );
}

import { Suspense, lazy, useEffect, useState } from 'react';
import { useReducedMotion } from 'motion/react';
import { api, type User } from './lib/api';
import { ToastProvider } from './components/ui';
import Login from './views/Login';
import Dashboard from './views/Dashboard';

const Scene = lazy(() => import('./components/Scene'));

function Splash() {
  const reduce = useReducedMotion();
  return (
    <div className="grid min-h-screen place-items-center">
      <div className="flex items-center gap-3 text-sm text-muted">
        <span className={`h-2 w-2 rounded-full bg-accent ${reduce ? '' : 'animate-pulse'}`} />
        正在检查登录状态…
      </div>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [withScene, setWithScene] = useState(false);

  useEffect(() => {
    const wide = window.matchMedia('(min-width: 900px)');
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setWithScene(wide.matches && !calm.matches);
    update();
    wide.addEventListener('change', update);
    calm.addEventListener('change', update);
    return () => {
      wide.removeEventListener('change', update);
      calm.removeEventListener('change', update);
    };
  }, []);

  useEffect(() => {
    api<User>('/auth/me').then(setUser).catch(() => setUser(null)).finally(() => setChecking(false));
    const onAuthRequired = () => setUser(null);
    window.addEventListener('auth-required', onAuthRequired);
    return () => window.removeEventListener('auth-required', onAuthRequired);
  }, []);

  return (
    <ToastProvider>
      <div className="mesh" aria-hidden />
      {withScene && (
        <Suspense fallback={null}>
          <Scene />
        </Suspense>
      )}
      {checking ? <Splash /> : user
        ? <Dashboard user={user} onLogout={() => setUser(null)} />
        : <Login onLogin={setUser} />}
    </ToastProvider>
  );
}

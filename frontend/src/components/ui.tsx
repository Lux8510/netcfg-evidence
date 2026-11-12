import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { ChevronDown, Inbox, Moon, Sun, X } from 'lucide-react';
import { cn } from '../lib/utils';

/* ---------- Button ---------- */
type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'ghost' | 'outline' | 'danger';
  size?: 'sm' | 'md';
};
export function Button({ className, variant = 'outline', size = 'md', ...props }: ButtonProps) {
  return (
    <button
      {...props}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-all duration-200',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:opacity-40 disabled:pointer-events-none',
        size === 'sm' ? 'h-8 px-3 text-xs' : 'h-9 px-4 text-sm',
        variant === 'primary' && 'bg-accent text-ink hover:bg-[#8ab9ff] shadow-[0_6px_20px_-14px_rgba(106,166,255,.9)]',
        variant === 'outline' && 'border border-line bg-panel text-fg hover:border-accent/40 hover:bg-panel',
        variant === 'ghost' && 'text-fg/90 hover:bg-panel hover:text-white',
        variant === 'danger' && 'border border-danger/40 text-danger hover:bg-danger/10',
        className,
      )}
    />
  );
}

/* ---------- Theme toggle ---------- */
export function ThemeToggle() {
  const [theme, setTheme] = useState<string>(
    () => document.documentElement.dataset.theme ?? 'dark');
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('netcfg-theme', theme);
  }, [theme]);
  const next = theme === 'dark' ? 'light' : 'dark';
  return (
    <Button variant="ghost" size="sm" data-testid="theme-toggle"
            aria-label={`切换到${next === 'light' ? '浅色' : '深色'}主题`}
            title={`切换到${next === 'light' ? '浅色' : '深色'}主题`}
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
      {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
    </Button>
  );
}

/* ---------- Card ---------- */
export function Card({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div {...props} className={cn('glass noise glass-hover p-5', className)}>
      {children}
    </div>
  );
}

export function CardHeader({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="mb-4 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold tracking-wide text-fg">{title}</h2>
        {hint && <p className="mt-1 text-xs leading-relaxed text-muted">{hint}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

/* ---------- Inputs ---------- */
const fieldClass =
  'w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm text-fg placeholder:text-muted ' +
  'focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20 transition-colors';

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn(fieldClass, props.className)} />;
}
export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cn(fieldClass, 'font-mono text-xs leading-relaxed', props.className)} />;
}
export function Select({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={cn('relative block', className)}>
      <select {...props} className={cn(fieldClass, 'w-full appearance-none pr-9')} />
      <ChevronDown size={14} aria-hidden
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-muted" />
    </span>
  );
}
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

/* ---------- Badge ---------- */
const badgeTone: Record<string, string> = {
  neutral: 'border-line text-fg/90',
  ok: 'border-ok/40 text-ok bg-ok/10',
  warn: 'border-warn/40 text-warn bg-warn/10',
  danger: 'border-danger/40 text-danger bg-danger/10',
  accent: 'border-accent/40 text-accent bg-accent/10',
};
export function Badge({ tone = 'neutral', children, className }: { tone?: keyof typeof badgeTone; children: ReactNode; className?: string }) {
  return (
    <span className={cn('inline-flex items-center rounded-full border px-2 py-0.5 text-[11px]', badgeTone[tone], className)}>
      {children}
    </span>
  );
}

/* ---------- Empty state ---------- */
export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-line px-4 py-8 text-center">
      <Inbox size={18} aria-hidden className="text-muted/70" />
      <p className="text-sm text-fg/90">{title}</p>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

/* ---------- Dialog ---------- */
export function Dialog({ open, onClose, title, children, wide }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean;
}) {
  const reduce = useReducedMotion();
  const panelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const focusables = () => Array.from(panel?.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])') ?? [])
      .filter((node) => !node.hasAttribute('disabled'));
    focusables()[0]?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { onClose(); return; }
      if (event.key !== 'Tab') return;
      const items = focusables();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('keydown', onKey); previous?.focus?.(); };
  }, [open, onClose]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm"
          initial={reduce ? undefined : { opacity: 0 }} animate={reduce ? undefined : { opacity: 1 }}
          exit={reduce ? undefined : { opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            ref={panelRef}
            role="dialog" aria-modal="true" aria-label={title}
            className={cn('glass noise max-h-[86vh] w-full overflow-auto p-5', wide ? 'max-w-5xl' : 'max-w-xl')}
            initial={reduce ? undefined : { opacity: 0, y: 14, scale: 0.99 }}
            animate={reduce ? undefined : { opacity: 1, y: 0, scale: 1 }}
            exit={reduce ? undefined : { opacity: 0, y: 6, scale: 0.995 }}
            transition={{ type: 'spring', stiffness: 260, damping: 26 }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-fg">{title}</h3>
              <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-white/5 hover:text-white" aria-label="关闭">
                <X size={16} />
              </button>
            </div>
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/* ---------- Toast ---------- */
type Toast = { id: number; text: string; tone: 'ok' | 'error' | 'info' };
const ToastContext = createContext<{ push: (text: string, tone?: Toast['tone']) => void }>({ push: () => {} });
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const reduce = useReducedMotion();
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Toast['tone'] = 'info') => {
    const id = Date.now() + Math.random();
    setItems((current) => [...current, { id, text, tone }]);
    setTimeout(() => setItems((current) => current.filter((item) => item.id !== id)), 4200);
  }, []);
  const value = useMemo(() => ({ push }), [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div role="status" aria-live="polite"
        className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[min(380px,90vw)] flex-col gap-2">
        <AnimatePresence>
          {items.map((item) => (
            <motion.div
              key={item.id}
              initial={reduce ? undefined : { opacity: 0, y: 8 }}
              animate={reduce ? undefined : { opacity: 1, y: 0 }}
              exit={reduce ? undefined : { opacity: 0, y: 4 }}
              className={cn('glass pointer-events-auto px-3.5 py-2.5 text-xs',
                item.tone === 'ok' && 'border-ok/40 text-ok',
                item.tone === 'error' && 'border-danger/40 text-danger')}
              data-testid="toast"
            >
              {item.text}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

/* ---------- Line spine ---------- */
export type SpinePoint = { line: number; versionId?: string; tone?: 'old' | 'new' };
export type HoverPoint = { line: number; versionId: string; path?: string | null } | null;

export function LineSpine({ lines, active, onHover, onSelect, label }: {
  lines: SpinePoint[];
  active?: HoverPoint;
  onHover?: (point: SpinePoint | null) => void;
  onSelect?: (point: SpinePoint) => void;
  label?: string;
}) {
  const unique = useMemo(() => {
    const seen = new Set<string>();
    return lines.filter((point) => {
      const key = `${point.versionId ?? ''}:${point.line}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [lines]);
  if (unique.length === 0) return null;
  const shown = unique.slice(0, 3);
  const rest = unique.length - shown.length;
  return (
    <div className="flex w-11 shrink-0 flex-col items-end gap-1 border-r border-line pr-2"
         data-testid="line-spine" aria-label={label ?? '行号'}>
      {shown.map((point) => {
        const isActive = !!active && active.line === point.line && active.versionId === (point.versionId ?? '');
        return (
          <button key={`${point.versionId ?? ''}-${point.line}`} type="button"
                  data-testid="spine-line" data-line={point.line} data-active={isActive || undefined}
                  aria-label={`第 ${point.line} 行`} title={`第 ${point.line} 行`}
                  onMouseEnter={() => onHover?.(point)}
                  onFocus={() => onHover?.(point)}
                  onClick={() => onSelect?.(point)}
                  className={cn('mono relative w-full rounded px-1 py-0.5 text-right text-[10px] tabular-nums transition-colors',
                    'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60',
                    isActive ? 'bg-accent/20 text-accent ring-1 ring-accent/30'
                      : point.tone === 'old' ? 'text-muted/80 hover:text-fg' : 'text-muted hover:text-fg')}>
            {point.line}
            <span aria-hidden
                  className={cn('absolute -right-2 top-1/2 h-px w-2 -translate-y-1/2', isActive ? 'bg-accent' : 'bg-line')} />
          </button>
        );
      })}
      {rest > 0 && <span className="mono text-[10px] text-muted/70">+{rest}</span>}
    </div>
  );
}

/* ---------- Stagger ---------- */
const staggerParent = { hidden: {}, show: { transition: { staggerChildren: 0.055, delayChildren: 0.02 } } };
const staggerChild = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: [0.22, 1, 0.36, 1] as const } },
};

export function Stagger({ children, className }: { children: ReactNode; className?: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.div className={className} variants={reduce ? undefined : staggerParent}
      initial={reduce ? undefined : 'hidden'} whileInView={reduce ? undefined : 'show'}
      viewport={{ once: true, margin: '-60px' }}>
      {children}
    </motion.div>
  );
}

export function StaggerItem({ children, className }: { children: ReactNode; className?: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.div className={className} variants={reduce ? undefined : staggerChild}>
      {children}
    </motion.div>
  );
}

/* ---------- Reveal ---------- */
export function Reveal({ children, delay = 0, className }: { children: ReactNode; delay?: number; className?: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={reduce ? undefined : { opacity: 0, y: 16 }}
      whileInView={reduce ? undefined : { opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-60px' }}
      transition={{ duration: 0.5, delay, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}

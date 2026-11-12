import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const shortId = (value?: string | null) => (value ? value.slice(0, 8) : '—');

export const when = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '—';

export const cash = (value?: number | string | null) =>
  value === null || value === undefined ? '—' : String(value);

/** Two evidence paths describe the same scope when they touch the same interface. */
export function sameScope(a?: string | null, b?: string | null): boolean {
  if (!a || !b) return false;
  if (a === b) return true;
  const left = a.split('.');
  const right = b.split('.');
  return left[0] === 'interface' && right[0] === 'interface' && left[1] === right[1];
}

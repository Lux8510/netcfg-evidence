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

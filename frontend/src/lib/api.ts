export type User = { id: string; username: string; role: 'editor' | 'approver' | 'admin' };
export type Device = { id: string; name: string; created_at: string };
export type Version = {
  id: string; device_id: string; source_name: string; raw_sha256: string; canonical_sha256: string;
  parser_version?: string; line_count: number; parsed: Record<string, unknown>; text?: string;
  unparsed: { line: number; text: string }[]; created_at: string;
};
export type DriftItem = {
  path: string; category: string; kind: 'added' | 'removed' | 'changed';
  old: unknown; new: unknown; old_lines: number[]; new_lines: number[];
  exemption?: Exemption | null;
};
export type DriftReport = {
  id: string; baseline_id: string; baseline_version_id: string; current_version_id: string;
  identical: boolean; items: DriftItem[];
  summary: { added: number; removed: number; changed: number; total: number;
    unparsed_baseline?: number; unparsed_current?: number; exempted?: number; actionable?: number };
  created_at: string;
};
export type Evidence = { kind: string; line: number | null; text: string; path?: string | null; version_id?: string | null };
export type Finding = {
  id: string; run_id: string; device_id: string; config_version_id: string; rule_id: string;
  status: 'violated' | 'undetermined'; message: string; evidence: Evidence[]; created_at: string;
};
export type RuleRun = { run_id: string; device_id: string; config_version_id: string;
  counts: Record<string, number>; created_at: string };
export type RuleCheck = {
  run_id: string; device_id: string; config_version_id: string;
  digest: Record<string, unknown>; counts: Record<string, number>; findings: Finding[];
};
export type Exemption = {
  id: string; device_id: string; path: string; reason: string; created_by: string;
  expires_at: string; revoked_at: string | null; active: boolean; expired: boolean;
};
export type ChangeRequest = {
  id: string; device_id: string; title: string; rationale: string;
  status: 'draft' | 'pending_approval' | 'approved' | 'rejected' | 'applied' | 'cancelled';
  finding_ids: string[]; findings_snapshot: Finding[]; proposed_commands: string[];
  created_by: string; approved_by: string | null; rejection_reason: string | null;
  created_at: string; executed_on_device: boolean;
};
export type AuditRow = {
  id: string; entity_type: string; entity_id: string; action: string; actor: string;
  note: string; created_at: string;
};
export type Explanation = {
  id: string; rule_id: string; status: 'accepted' | 'rejected'; reason: string; text: string;
  model: string; citations: { rule_id: string; lines: number[] }[];
  usage: { prompt_tokens?: number; completion_tokens?: number } | null; created_at: string;
};
export type Link = {
  id: string;
  device_a: { id: string; name: string; interface: string };
  device_b: { id: string; name: string; interface: string };
};

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(message: string, code: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

type Options = { method?: string; body?: unknown };

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const init: RequestInit = { method: options.method ?? (options.body !== undefined ? 'POST' : 'GET') };
  if (options.body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(`/api${path}`, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new ApiError(
      payload?.error?.message ?? `请求失败 ${response.status}`,
      payload?.error?.code ?? 'ERROR',
      response.status,
    );
    if (response.status === 401) window.dispatchEvent(new Event('auth-required'));
    throw error;
  }
  return payload.data as T;
}

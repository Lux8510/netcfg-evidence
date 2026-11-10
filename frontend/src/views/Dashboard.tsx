import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import {
  Activity, ArrowRightLeft, CheckCircle2, FileDiff, GitBranch, Link2, ListChecks,
  LogOut, Network, Plus, RefreshCw, ShieldCheck, Sparkles, Upload, Wallet,
} from 'lucide-react';
import {
  api, type AuditRow, type ChangeRequest, type ChangeRun, type Device, type DeviceTarget, type DriftItem, type DriftReport,
  type Exemption, type Explanation, type Finding, type Link, type RuleCheck, type User, type Version,
} from '../lib/api';
import { diffRows } from '../lib/diff';
import { cn, shortId, when } from '../lib/utils';
import {
  Badge, Button, Card, CardHeader, Dialog, EmptyState, Field, Input, Reveal, Select, Textarea, ThemeToggle, useToast,
} from '../components/ui';

const statusTone: Record<string, 'ok' | 'warn' | 'danger' | 'neutral'> = {
  draft: 'neutral', pending_approval: 'warn', approved: 'ok', applied: 'ok',
  rejected: 'danger', cancelled: 'neutral',
};

export default function Dashboard({ user, onLogout }: { user: User; onLogout: () => void }) {
  const toast = useToast();
  const loadSeq = useRef(0);
  const deviceRef = useRef('');
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceId, setDeviceId] = useState<string>('');
  const [versions, setVersions] = useState<Version[]>([]);
  const [baseline, setBaseline] = useState<{ config_version_id: string } | null>(null);
  const [drift, setDrift] = useState<DriftReport | null>(null);
  const [ruleCheck, setRuleCheck] = useState<RuleCheck | null>(null);
  const [changes, setChanges] = useState<ChangeRequest[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [explanations, setExplanations] = useState<Explanation[]>([]);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [target, setTarget] = useState<DeviceTarget | null>(null);
  const [changeRuns, setChangeRuns] = useState<ChangeRun[]>([]);
  const [changeDialog, setChangeDialog] = useState<{ mode: 'start' | 'complete'; runId?: string } | null>(null);
  const [busy, setBusy] = useState('');
  const [includeCross, setIncludeCross] = useState(true);

  const [diffTarget, setDiffTarget] = useState<{ base: string; current: string } | null>(null);
  const [evidence, setEvidence] = useState<{ versionId: string; line: number; title: string } | null>(null);
  const [changeFor, setChangeFor] = useState<Finding | null>(null);
  const [noteFor, setNoteFor] = useState<{ requestId: string; action: string } | null>(null);
  const [exemptFor, setExemptFor] = useState<DriftItem | null>(null);

  const loadDevices = useCallback(async () => {
    const rows = await api<Device[]>('/devices');
    setDevices(rows);
    setDeviceId((current) => (rows.some((row) => row.id === current) ? current : rows[0]?.id ?? ''));
  }, []);

  const loadDeviceData = useCallback(async (id: string) => {
    const seq = ++loadSeq.current;
    deviceRef.current = id;
    // Clear first: the previous device's data must never be shown under the new selection.
    setVersions([]); setBaseline(null); setDrift(null); setRuleCheck(null);
    setChanges([]); setLinks([]); setExplanations([]); setAudit([]);
    setTarget(null); setChangeRuns([]);
    if (!id) return;
    const stale = () => seq !== loadSeq.current || deviceRef.current !== id;
    const [nextVersions, nextBaseline, nextDrift, nextChanges, nextLinks, nextExplain, nextAudit,
      nextTarget, nextChangeRuns] = await Promise.all([
      api<Version[]>(`/devices/${id}/configs`),
      api<{ config_version_id: string } | null>(`/devices/${id}/baseline`),
      api<DriftReport[]>(`/devices/${id}/drift`),
      api<ChangeRequest[]>(`/devices/${id}/change-requests`),
      api<Link[]>('/links'),
      api<Explanation[]>(`/devices/${id}/explanations`),
      api<AuditRow[]>(`/devices/${id}/audit`),
      api<DeviceTarget | null>(`/devices/${id}/target`),
      api<ChangeRun[]>(`/devices/${id}/change-runs`),
    ]);
    if (stale()) return;
    setVersions(nextVersions);
    setBaseline(nextBaseline);
    setDrift(nextDrift[0] ?? null);
    setChanges(nextChanges);
    setLinks(nextLinks);
    setExplanations(nextExplain);
    setAudit(nextAudit);
    setTarget(nextTarget);
    setChangeRuns(nextChangeRuns);

    const runs = await api<{ run_id: string; counts: Record<string, number>; config_version_id: string }[]>(
      `/devices/${id}/rules/runs`);
    if (stale()) return;
    if (runs.length) {
      const latest = runs[0];
      const [findings, scopedExplanations] = await Promise.all([
        api<Finding[]>(`/devices/${id}/findings?run_id=${encodeURIComponent(latest.run_id)}`),
        api<Explanation[]>(`/devices/${id}/explanations?run_id=${encodeURIComponent(latest.run_id)}`),
      ]);
      if (stale()) return;
      setRuleCheck({ run_id: latest.run_id, device_id: id, config_version_id: latest.config_version_id,
        counts: latest.counts, digest: {}, findings });
      setExplanations(scopedExplanations);
    } else {
      setRuleCheck(null);
    }
  }, []);

  useEffect(() => { loadDevices().catch((error) => toast.push((error as Error).message, 'error')); }, [loadDevices, toast]);
  useEffect(() => { loadDeviceData(deviceId).catch((error) => toast.push((error as Error).message, 'error')); }, [deviceId, loadDeviceData, toast]);

  useEffect(() => {
    // Any dialog belongs to the device that opened it.
    setDiffTarget(null); setEvidence(null); setChangeFor(null); setNoteFor(null); setExemptFor(null);
  }, [deviceId]);

  const currentDevice = devices.find((device) => device.id === deviceId) ?? null;
  const deviceLinks = useMemo(
    () => links.filter((link) => link.device_a.id === deviceId || link.device_b.id === deviceId),
    [links, deviceId]);

  async function run(label: string, task: () => Promise<void>) {
    setBusy(label);
    try { await task(); } catch (error) { toast.push((error as Error).message, 'error'); } finally { setBusy(''); }
  }

  const createDevice = (name: string) => run('正在创建…', async () => {
    const device = await api<Device>('/devices', { body: { name } });
    await loadDevices();
    setDeviceId(device.id);
    toast.push(`设备 ${device.name} 已创建`, 'ok');
  });

  const importConfig = (text: string, source: string) => run('正在导入…', async () => {
    const version = await api<Version & { unchanged: boolean }>(`/devices/${deviceId}/configs`,
      { body: { text, source_name: source || 'manual' } });
    await loadDeviceData(deviceId);
    toast.push(version.unchanged ? '相同配置已存在，返回原版本' : '配置已导入', 'ok');
  });

  const setAsBaseline = (versionId: string) => run('正在设置基线…', async () => {
    await api(`/devices/${deviceId}/baseline`, { body: { version_id: versionId } });
    await loadDeviceData(deviceId);
    toast.push('基线已更新', 'ok');
  });

  const computeDrift = () => run('正在比对…', async () => {
    const owner = deviceId;
    const report = await api<DriftReport>(`/devices/${owner}/drift`, { body: {} });
    if (deviceRef.current !== owner) return;
    setDrift(report);
    toast.push(report.identical ? '与基线一致' : `发现 ${report.items.length} 处漂移`, report.identical ? 'ok' : 'info');
  });

  const checkRules = () => run('正在检查…', async () => {
    const owner = deviceId;
    const result = await api<RuleCheck>(`/devices/${owner}/rules/check`, { body: { include_cross: includeCross } });
    if (deviceRef.current !== owner) return;
    setRuleCheck(result);
    toast.push(`检查完成：违规 ${result.counts.findings_violated ?? 0}，无法判定 ${result.counts.findings_undetermined ?? 0}`, 'ok');
  });

  const createChange = (finding: Finding, title: string, rationale: string, commands: string[]) =>
    run('正在创建变更单…', async () => {
      await api(`/devices/${deviceId}/change-requests`, {
        body: { title, rationale, finding_ids: [finding.id], proposed_commands: commands },
      });
      await loadDeviceData(deviceId);
      setChangeFor(null);
      toast.push('变更单已创建，等待人工审批', 'ok');
    });

  const transition = (requestId: string, action: string, note: string) => run('正在更新状态…', async () => {
    await api(`/change-requests/${requestId}/${action}`, { body: { note, idempotency_key: crypto.randomUUID() } });
    await loadDeviceData(deviceId);
    setNoteFor(null);
    toast.push('状态已更新', 'ok');
  });

  const createExemption = (item: DriftItem, days: number, reason: string) => run('正在登记豁免…', async () => {
    if (!drift) return;
    await api(`/devices/${deviceId}/drift/exemptions`, { body: {
      drift_report_id: drift.id, path: item.path, reason,
      expires_at: new Date(Date.now() + days * 86400000).toISOString(),
    } });
    await loadDeviceData(deviceId);
    setExemptFor(null);
    toast.push('豁免已登记，到期后自动恢复为待处理', 'ok');
  });

  const revokeExemption = (exemption: Exemption, note: string) => run('正在撤销…', async () => {
    await api(`/exemptions/${exemption.id}/revoke`, { body: { note } });
    await loadDeviceData(deviceId);
    toast.push('豁免已撤销', 'ok');
  });

  const generateExplanation = () => run('正在生成解释…', async () => {
    const owner = deviceId;
    const rows = await api<Explanation[]>(`/devices/${owner}/explanations`, { body: {} });
    if (deviceRef.current !== owner) return;
    setExplanations(rows);
    toast.push(`生成 ${rows.length} 条解释`, 'ok');
  });

  const saveTarget = (fields: { host: string; port: number; username: string; credential_ref: string }) =>
    run('正在保存采集目标…', async () => {
      const saved = await api<DeviceTarget>(`/devices/${deviceId}/target`, { method: 'PUT', body: { ...fields, enabled: true } });
      setTarget(saved);
      toast.push('SSH 目标已保存；密码来自服务端环境变量', 'ok');
    });

  const collectNow = () => run('正在通过 SSH 采集…', async () => {
    const result = await api<{ version: Version; event: { status: string } }>(
      `/devices/${deviceId}/collect`, { body: { label: 'manual' } });
    await loadDeviceData(deviceId);
    toast.push(`采集完成：${result.version.source_name}`, 'ok');
  });

  const startChange = (note: string) => run('正在采集变更前快照…', async () => {
    await api(`/devices/${deviceId}/change-runs`, { body: { note } });
    await loadDeviceData(deviceId);
    setChangeDialog(null);
    toast.push('变更窗口已开始，变更后点击「完成核对」', 'ok');
  });

  const completeChange = (runId: string, note: string) => run('正在采集变更后快照…', async () => {
    await api(`/change-runs/${runId}/complete`, { body: { note } });
    await loadDeviceData(deviceId);
    setChangeDialog(null);
    toast.push('变更核对完成', 'ok');
  });

  const logout = async () => {
    await fetch('/api/auth/logout', { method: 'POST' });
    onLogout();
  };

  return (
    <div className="mx-auto max-w-[1440px] px-4 pb-16 pt-5 lg:px-6">
      <Header user={user} devices={devices} deviceId={deviceId} onSelect={setDeviceId}
              onCreate={createDevice} onLogout={logout} busy={!!busy} />

      <main className="mt-5 grid grid-cols-12 gap-4">
        <Reveal className="col-span-12 xl:col-span-4">
          <VersionsCard versions={versions} baseline={baseline} busy={busy}
                        onImport={importConfig} onBaseline={setAsBaseline} onShow={setEvidence} />
        </Reveal>

        <Reveal className="col-span-12 xl:col-span-8" delay={0.04}>
          <DriftCard report={drift} busy={busy} onCompute={computeDrift}
                     onDiff={(base, current) => setDiffTarget({ base, current })}
                     onExempt={setExemptFor} onRevoke={revokeExemption} onShow={setEvidence} />
        </Reveal>

        <Reveal className="col-span-12 lg:col-span-7" delay={0.06}>
          <RulesCard check={ruleCheck} busy={busy} includeCross={includeCross}
                     onToggleCross={setIncludeCross} onRun={checkRules}
                     onEvidence={(finding, item) => setEvidence({
                       versionId: item.version_id ?? finding.config_version_id,
                       line: item.line as number,
                       title: `${finding.rule_id} 证据` })}
                     onGenerate={setChangeFor} />
        </Reveal>

        <Reveal className="col-span-12 lg:col-span-5" delay={0.08}>
          <TopologyCard links={deviceLinks} currentId={deviceId} onSelect={setDeviceId} />
        </Reveal>

        <Reveal className="col-span-12 lg:col-span-7" delay={0.1}>
          <ChangesCard changes={changes} busy={busy}
                       onAction={(requestId, action) => setNoteFor({ requestId, action })} />
        </Reveal>

        <Reveal className="col-span-12 lg:col-span-5" delay={0.12}>
          <AuditCard rows={audit} />
        </Reveal>

        <Reveal className="col-span-12" delay={0.14}>
          <ChangeVerifyCard target={target} runs={changeRuns} busy={busy}
                            onSaveTarget={saveTarget} onCollect={collectNow}
                            onStart={() => setChangeDialog({ mode: 'start' })}
                            onComplete={(runId) => setChangeDialog({ mode: 'complete', runId })} />
        </Reveal>

        <Reveal className="col-span-12" delay={0.16}>
          <ExplainCard rows={explanations} busy={busy} onGenerate={generateExplanation} />
        </Reveal>
      </main>

      <DiffDialog target={diffTarget} onClose={() => setDiffTarget(null)} />
      <EvidenceDialog target={evidence} onClose={() => setEvidence(null)} />
      <ChangeDialog finding={changeFor} deviceName={currentDevice?.name ?? ''} onClose={() => setChangeFor(null)}
                    onSubmit={createChange} />
      <NoteDialog target={noteFor} onClose={() => setNoteFor(null)} onSubmit={transition} />
      <ExemptDialog item={exemptFor} onClose={() => setExemptFor(null)} onSubmit={createExemption} />
      <ChangeNoteDialog target={changeDialog} onClose={() => setChangeDialog(null)}
                        onSubmit={(note) => {
                          if (!changeDialog) return;
                          if (changeDialog.mode === 'start') startChange(note);
                          else if (changeDialog.runId) completeChange(changeDialog.runId, note);
                        }} />
    </div>
  );
}

/* ---------------- header ---------------- */
function Header({ user, devices, deviceId, onSelect, onCreate, onLogout, busy }: {
  user: User; devices: Device[]; deviceId: string; onSelect: (id: string) => void;
  onCreate: (name: string) => void; onLogout: () => void; busy: boolean;
}) {
  const [name, setName] = useState('');
  return (
    <header className="glass noise flex flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between">
      <div className="flex items-center gap-2.5">
        <span className="h-2 w-2 rounded-full bg-accent shadow-[0_0_18px_rgba(106,166,255,.9)]" />
        <div>
          <p className="text-sm font-semibold tracking-wide">netcfg-evidence</p>
          <p className="text-[11px] text-muted">配置基线 · 漂移 · 证据链</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Select value={deviceId} onChange={(event) => onSelect(event.target.value)} data-testid="device-select"
                aria-label="选择设备" className="w-[200px]">
          {devices.length === 0 && <option value="">还没有设备</option>}
          {devices.map((device) => <option key={device.id} value={device.id}>{device.name}</option>)}
        </Select>
        <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="新设备名"
               className="w-[140px]" data-testid="device-name" />
        <Button variant="ghost" size="sm" disabled={busy || !name.trim()} data-testid="create-device"
                onClick={() => { onCreate(name.trim()); setName(''); }}>
          <Plus size={14} /> 新增
        </Button>
        <div className="mx-1 hidden h-6 w-px bg-white/10 md:block" />
        <span className="text-xs text-muted" data-testid="user-label">{user.username} · {user.role}</span>
        <ThemeToggle />
        <Button variant="ghost" size="sm" onClick={onLogout} data-testid="logout"><LogOut size={14} /> 退出</Button>
      </div>
    </header>
  );
}

/* ---------------- versions ---------------- */
function VersionsCard({ versions, baseline, busy, onImport, onBaseline, onShow }: {
  versions: Version[]; baseline: { config_version_id: string } | null; busy: string;
  onImport: (text: string, source: string) => void;
  onBaseline: (id: string) => void;
  onShow: (target: { versionId: string; line: number; title: string }) => void;
}) {
  const [source, setSource] = useState('');
  const [text, setText] = useState('');
  const reduce = useReducedMotion();
  return (
    <Card className="flex h-full flex-col">
      <CardHeader title="配置版本" hint="相同字节重复导入会返回同一版本（幂等）"
                  action={<Activity size={16} className="text-muted" />} />
      <form className="space-y-2" onSubmit={(event) => {
        event.preventDefault();
        if (!text.trim()) return;
        onImport(text, source);
        setText(''); setSource('');
      }}>
        <Input value={source} onChange={(event) => setSource(event.target.value)}
               placeholder="来源名称，如 r1-baseline" data-testid="source-name" />
        <Textarea rows={7} value={text} onChange={(event) => setText(event.target.value)}
                  placeholder={'粘贴 FRR 配置，例如：\nhostname r1\ninterface eth2\n ip address 10.12.0.1/30\n ip ospf cost 10'}
                  data-testid="config-text" />
        <Button type="submit" variant="primary" className="w-full" disabled={!!busy || !text.trim()} data-testid="import-submit">
          <Upload size={14} /> 导入配置
        </Button>
      </form>
      <div className="mt-4 space-y-2 overflow-auto" data-testid="version-list">
        {versions.length === 0 && <EmptyState title="还没有配置版本" hint="先导入一份配置" />}
        {versions.slice(0, 6).map((version, index) => {
          const isBaseline = baseline?.config_version_id === version.id;
          return (
            <motion.article key={version.id}
              initial={reduce ? undefined : { opacity: 0, y: 8 }}
              animate={reduce ? undefined : { opacity: 1, y: 0 }}
              transition={{ delay: index * 0.03 }}
              className="rounded-xl border border-line bg-panel-2 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="truncate text-xs font-medium text-fg">{version.source_name}</p>
                <div className="flex shrink-0 gap-1">
                  {isBaseline && <Badge tone="ok">基线</Badge>}
                  {version.unparsed.length > 0 && <Badge tone="warn">未解析 {version.unparsed.length}</Badge>}
                </div>
              </div>
              <p className="mt-1 mono text-[10px] text-muted">
                {when(version.created_at)} · {version.line_count} 行 · {shortId(version.canonical_sha256)}
              </p>
              <div className="mt-2 flex gap-1.5">
                <Button size="sm" variant="ghost" disabled={!!busy || isBaseline} data-testid="set-baseline"
                        onClick={() => onBaseline(version.id)}>设为基线</Button>
                <Button size="sm" variant="ghost" onClick={() => onShow({ versionId: version.id, line: 1, title: `${version.source_name} 原文` })}>
                  查看原文
                </Button>
              </div>
            </motion.article>
          );
        })}
      </div>
    </Card>
  );
}

/* ---------------- drift ---------------- */
function DriftCard({ report, busy, onCompute, onDiff, onExempt, onRevoke, onShow }: {
  report: DriftReport | null; busy: string;
  onCompute: () => void;
  onDiff: (base: string, current: string) => void;
  onExempt: (item: DriftItem) => void;
  onRevoke: (exemption: Exemption, note: string) => void;
  onShow: (target: { versionId: string; line: number; title: string }) => void;
}) {
  return (
    <Card className="h-full">
      <CardHeader title="漂移" hint="与基线逐项比对；豁免是限时的，不是修复"
                  action={<Button size="sm" variant="primary" disabled={!!busy} onClick={onCompute} data-testid="compute-drift">
                    <RefreshCw size={13} className={busy ? 'animate-spin' : ''} /> 与基线比对
                  </Button>} />
      {!report ? (
        <EmptyState title="还没有漂移报告" hint="设置基线后点击「与基线比对」" />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="drift-summary">
            {[['新增', report.summary.added], ['删除', report.summary.removed],
              ['修改', report.summary.changed], ['豁免', report.summary.exempted ?? 0]].map(([label, value]) => (
              <div key={String(label)} className="rounded-xl border border-line bg-panel-2 px-3 py-2">
                <p className="label">{label}</p>
                <p className="mt-1 text-lg font-semibold tabular-nums">{value}</p>
              </div>
            ))}
          </div>
          <div className="mt-3 max-h-[340px] space-y-2 overflow-auto pr-1">
            {report.items.length === 0 && <EmptyState title="两份配置语义一致" />}
            {report.items.map((item) => (
              <article key={item.path} data-testid="drift-item" data-path={item.path}
                       className="rounded-xl border border-line bg-panel-2 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="mono text-[11px] text-fg">{item.path}</code>
                  <Badge tone={item.kind === 'added' ? 'ok' : item.kind === 'removed' ? 'danger' : 'warn'}>{item.kind}</Badge>
                  {item.exemption && <Badge tone="accent">豁免至 {when(item.exemption.expires_at)}</Badge>}
                </div>
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  <pre className="mono overflow-auto rounded-lg bg-panel-2 p-2 text-[10px] text-muted">{JSON.stringify(item.old, null, 1)}</pre>
                  <pre className="mono overflow-auto rounded-lg bg-panel-2 p-2 text-[10px] text-fg">{JSON.stringify(item.new, null, 1)}</pre>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <Button size="sm" variant="ghost" data-testid="diff-button"
                          onClick={() => onDiff(report.baseline_version_id, report.current_version_id)}>
                    <FileDiff size={13} /> 并排对比
                  </Button>
                  {item.old_lines.length > 0 && (
                    <Button size="sm" variant="ghost"
                            onClick={() => onShow({ versionId: report.baseline_version_id, line: item.old_lines[0], title: '基线证据' })}>
                      基线第 {item.old_lines[0]} 行
                    </Button>
                  )}
                  {item.new_lines.length > 0 && (
                    <Button size="sm" variant="ghost"
                            onClick={() => onShow({ versionId: report.current_version_id, line: item.new_lines[0], title: '当前证据' })}>
                      当前第 {item.new_lines[0]} 行
                    </Button>
                  )}
                  {item.exemption ? (
                    <RevokeButton exemption={item.exemption} onRevoke={onRevoke} />
                  ) : (
                    <Button size="sm" variant="ghost" data-testid="exempt-button" onClick={() => onExempt(item)}>
                      <ShieldCheck size={13} /> 登记豁免
                    </Button>
                  )}
                </div>
              </article>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}

function RevokeButton({ exemption, onRevoke }: { exemption: Exemption; onRevoke: (e: Exemption, note: string) => void }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)} data-testid="revoke-exemption">撤销豁免</Button>
      <Dialog open={open} onClose={() => setOpen(false)} title="撤销豁免">
        <p className="mb-3 text-xs text-muted">{exemption.reason}</p>
        <Field label="撤销原因">
          <Textarea rows={3} value={note} onChange={(event) => setNote(event.target.value)} data-testid="revoke-note" />
        </Field>
        <Button variant="danger" className="mt-3 w-full" disabled={!note.trim()}
                onClick={() => { onRevoke(exemption, note); setOpen(false); setNote(''); }}>
          确认撤销
        </Button>
      </Dialog>
    </>
  );
}

/* ---------------- rules ---------------- */
function RulesCard({ check, busy, includeCross, onToggleCross, onRun, onEvidence, onGenerate }: {
  check: RuleCheck | null; busy: string; includeCross: boolean;
  onToggleCross: (value: boolean) => void; onRun: () => void;
  onEvidence: (finding: Finding, item: { line: number | null; version_id?: string | null }) => void;
  onGenerate: (finding: Finding) => void;
}) {
  return (
    <Card className="h-full">
      <CardHeader title="规则与证据" hint="无法判定不会被当成通过"
                  action={<div className="flex items-center gap-2">
                    <label className="flex items-center gap-1.5 text-[11px] text-muted">
                      <input type="checkbox" checked={includeCross} data-testid="include-cross"
                             onChange={(event) => onToggleCross(event.target.checked)} /> 含链路规则
                    </label>
                    <Button size="sm" variant="primary" disabled={!!busy} onClick={onRun} data-testid="run-rules">
                      <ListChecks size={13} /> 运行检查
                    </Button>
                  </div>} />
      {!check ? <EmptyState title="还没有检查记录" hint="点击「运行检查」" /> : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5" data-testid="rules-summary">
            {[['检查', check.counts.rules_checked], ['符合', check.counts.rules_satisfied],
              ['违规', check.counts.findings_violated], ['无法判定', check.counts.findings_undetermined],
              ['不适用', check.counts.rules_not_applicable]].map(([label, value]) => (
              <div key={String(label)} className="rounded-xl border border-line bg-panel-2 px-3 py-2">
                <p className="label">{label}</p>
                <p className="mt-1 text-lg font-semibold tabular-nums">{value ?? 0}</p>
              </div>
            ))}
          </div>
          <div className="mt-3 max-h-[300px] space-y-2 overflow-auto pr-1" data-testid="rules-result">
            {check.findings.length === 0 && <EmptyState title="没有需要处理的 Finding" />}
            {check.findings.map((finding) => (
              <article key={finding.id} data-testid="finding" data-rule={finding.rule_id}
                       className="rounded-xl border border-line bg-panel-2 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="mono text-[11px] text-fg">{finding.rule_id}</code>
                  <Badge tone={finding.status === 'violated' ? 'danger' : 'warn'}>{finding.status}</Badge>
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-fg/90">{finding.message}</p>
                <div className="mt-2 space-y-1">
                  {finding.evidence.map((item, index) => (
                    <div key={`${finding.id}-${index}`} className="flex flex-wrap items-center gap-2 text-[11px]">
                      <span className="text-muted">第 {item.line} 行</span>
                      <code className="mono rounded bg-panel-2 px-1.5 py-0.5">{item.text}</code>
                      {item.line !== null && (
                        <Button size="sm" variant="ghost" data-testid="evidence-button"
                                onClick={() => onEvidence(finding, item)}>看原文</Button>
                      )}
                    </div>
                  ))}
                </div>
                {finding.status === 'violated' && (
                  <Button size="sm" variant="ghost" className="mt-2" data-testid="generate-change"
                          onClick={() => onGenerate(finding)}>
                    <GitBranch size={13} /> 生成变更单
                  </Button>
                )}
              </article>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}

/* ---------------- topology ---------------- */
function TopologyCard({ links, currentId, onSelect }: { links: Link[]; currentId: string; onSelect: (id: string) => void }) {
  const graph = useMemo(() => {
    const nodes = new Map<string, string>();
    for (const link of links) {
      nodes.set(link.device_a.id, link.device_a.name);
      nodes.set(link.device_b.id, link.device_b.name);
    }
    const ids = [...nodes.keys()];
    const width = 640;
    const height = 320;
    const cx = width / 2;
    const cy = height / 2;
    const radius = Math.min(120, 40 + ids.length * 16);
    const position = new Map<string, [number, number]>();
    ids.forEach((id, index) => {
      const angle = (2 * Math.PI * index) / Math.max(ids.length, 1) - Math.PI / 2;
      position.set(id, [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)]);
    });
    return { nodes, ids, position, width, height };
  }, [links]);

  return (
    <Card className="h-full">
      <CardHeader title="拓扑" hint="来自已登记的链路；点击节点切换设备" action={<Network size={16} className="text-muted" />} />
      {links.length === 0 ? <EmptyState title="还没有登记链路" hint="在接口级登记两条设备之间的连接后，跨设备规则才会生效" /> : (
        <svg viewBox={`0 0 ${graph.width} ${graph.height}`} className="w-full" role="img" aria-label="设备拓扑" data-testid="topo-svg">
          {links.map((link) => {
            const [x1, y1] = graph.position.get(link.device_a.id) ?? [0, 0];
            const [x2, y2] = graph.position.get(link.device_b.id) ?? [0, 0];
            return (
              <g key={link.id}>
                <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--color-line)" strokeWidth={1.5} />
                <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 6} textAnchor="middle" fill="var(--color-muted)" fontSize={10}>
                  {link.device_a.interface} ↔ {link.device_b.interface}
                </text>
              </g>
            );
          })}
          {graph.ids.map((id) => {
            const [x, y] = graph.position.get(id) ?? [0, 0];
            const current = id === currentId;
            return (
              <g key={id} transform={`translate(${x},${y})`} className="cursor-pointer" data-testid="topo-node"
                 data-device={id} role="button" tabIndex={0} aria-label={`设备 ${graph.nodes.get(id)}`}
                 onClick={() => onSelect(id)}
                 onKeyDown={(event) => {
                   if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(id); }
                 }}>
                <circle r={26} fill={current ? 'var(--color-accent)' : 'var(--color-surface)'} stroke="var(--color-accent)" strokeOpacity={current ? 1 : 0.45} />
                <text y={4} textAnchor="middle" fontSize={11} fill={current ? 'var(--color-ink)' : 'var(--color-fg)'}>
                  {graph.nodes.get(id)}
                </text>
              </g>
            );
          })}
        </svg>
      )}
      <p className="mt-2 text-[11px] text-muted">{links.length} 条链路 · 当前设备 {currentId ? '已选中' : '未选择'}</p>
    </Card>
  );
}

/* ---------------- changes ---------------- */
function ChangesCard({ changes, busy, onAction }: {
  changes: ChangeRequest[]; busy: string; onAction: (id: string, action: string) => void;
}) {
  return (
    <Card className="h-full">
      <CardHeader title="变更单与审批" hint="审批人来自登录会话；系统从不下发配置"
                  action={<Wallet size={16} className="text-muted" />} />
      <div className="space-y-2" data-testid="changes-list">
        {changes.length === 0 && <EmptyState title="还没有变更单" hint="在规则里对一条违规生成变更单" />}
        {changes.slice(0, 6).map((request) => (
          <article key={request.id} className="rounded-xl border border-line bg-panel-2 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-xs font-medium text-fg">{request.title}</p>
              <Badge tone={statusTone[request.status] ?? 'neutral'}>{request.status}</Badge>
            </div>
            <p className="mt-1 text-[11px] text-muted">{request.rationale}</p>
            <pre className="mono mt-2 overflow-auto rounded-lg bg-panel-2 p-2 text-[10px] text-accent/90">
              {request.proposed_commands.join('\n')}
            </pre>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {request.status === 'draft' && <ActionButton id={request.id} action="submit" label="提交审批" busy={busy} onAction={onAction} testid="transition-submit" />}
              {request.status === 'pending_approval' && (
                <>
                  <ActionButton id={request.id} action="approve" label="批准" busy={busy} onAction={onAction} testid="transition-approve" />
                  <ActionButton id={request.id} action="reject" label="拒绝" busy={busy} onAction={onAction} testid="transition-reject" />
                </>
              )}
              {request.status === 'approved' && <ActionButton id={request.id} action="apply" label="标记已执行" busy={busy} onAction={onAction} testid="transition-apply" />}
              {request.status === 'draft' && <ActionButton id={request.id} action="cancel" label="取消" busy={busy} onAction={onAction} testid="transition-cancel" />}
            </div>
            <p className="mt-2 text-[10px] text-muted">
              创建 {request.created_by} · {when(request.created_at)}
              {request.approved_by ? ` · 审批 ${request.approved_by}` : ''} · 未下发设备
            </p>
          </article>
        ))}
      </div>
    </Card>
  );
}

function ActionButton({ id, action, label, busy, onAction, testid }: {
  id: string; action: string; label: string; busy: string;
  onAction: (id: string, action: string) => void; testid: string;
}) {
  return (
    <Button size="sm" variant="ghost" disabled={!!busy} data-testid={testid} onClick={() => onAction(id, action)}>
      {label}
    </Button>
  );
}

/* ---------------- audit ---------------- */
function AuditCard({ rows }: { rows: AuditRow[] }) {
  return (
    <Card className="h-full">
      <CardHeader title="审计" hint="变更单与豁免的追加式事件" action={<CheckCircle2 size={16} className="text-muted" />} />
      <div className="max-h-[320px] space-y-1 overflow-auto pr-1" data-testid="audit-list">
        {rows.length === 0 && <EmptyState title="还没有审计事件" />}
        {rows.slice(0, 12).map((row) => (
          <div key={row.id} className="border-l border-line py-1 pl-3">
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <Badge tone={row.action === 'approve' || row.action === 'created' ? 'ok' : 'warn'}>{row.action}</Badge>
              <span className="text-fg/90">{row.actor}</span>
              <span className="text-muted">{when(row.created_at)}</span>
            </div>
            <p className="mt-0.5 text-[11px] text-muted">{row.note}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}

/* ---------------- explanations ---------------- */
function ExplainCard({ rows, busy, onGenerate }: { rows: Explanation[]; busy: string; onGenerate: () => void }) {
  return (
    <Card>
      <CardHeader title="解释" hint="模型只能解释已有判定；引用不通过会被拒绝"
                  action={<Button size="sm" variant="primary" disabled={!!busy} onClick={onGenerate} data-testid="explain-run">
                    <Sparkles size={13} /> 生成解释
                  </Button>} />
      <div className="grid gap-2 md:grid-cols-2" data-testid="explain-list">
        {rows.length === 0 && <EmptyState title="还没有解释记录" />}
        {rows.slice(0, 6).map((row) => (
          <article key={row.id} className="rounded-xl border border-line bg-panel-2 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <code className="mono text-[11px]">{row.rule_id}</code>
              <Badge tone={row.status === 'accepted' ? 'ok' : 'danger'}>{row.status}</Badge>
              <span className="text-[10px] text-muted">{row.reason}</span>
              {row.usage?.prompt_tokens != null && (
                <span className="text-[10px] text-muted">tokens {row.usage.prompt_tokens}+{row.usage.completion_tokens ?? 0}</span>
              )}
            </div>
            <p className="mt-1.5 whitespace-pre-wrap text-[11px] leading-relaxed text-fg/90">{row.text}</p>
            <p className="mt-1 text-[10px] text-muted">
              引用 {row.citations.map((c) => `${c.rule_id} 第 ${c.lines.join(',')} 行`).join('；') || '无'} · {row.model}
            </p>
          </article>
        ))}
      </div>
    </Card>
  );
}

/* ---------------- dialogs ---------------- */
function DiffDialog({ target, onClose }: { target: { base: string; current: string } | null; onClose: () => void }) {
  const [state, setState] = useState<{ left: Version; right: Version } | null>(null);
  useEffect(() => {
    if (!target) { setState(null); return; }
    let alive = true;
    Promise.all([api<Version>(`/versions/${target.base}`), api<Version>(`/versions/${target.current}`)])
      .then(([left, right]) => alive && setState({ left, right })).catch(() => undefined);
    return () => { alive = false; };
  }, [target]);
  const leftText = state?.left.text ?? '';
  const rightText = state?.right.text ?? '';
  const rows = useMemo(() => state ? diffRows(leftText.split('\n'), rightText.split('\n')) : [], [state, leftText, rightText]);
  return (
    <Dialog open={!!target} onClose={onClose} title="并排对比" wide>
      {!state ? <p className="text-xs text-muted">正在读取两个版本…</p> : (
        <>
          <div className="mb-2 grid grid-cols-2 gap-3 text-[11px] text-muted">
            <span>基线：{state.left.source_name}</span>
            <span>当前：{state.right.source_name}</span>
          </div>
          <div className="max-h-[62vh] overflow-auto rounded-lg border border-line" data-testid="diff-body">
            {rows.map((row, index) => {
              const leftLine = row.left === null ? '' : leftText.split('\n')[row.left];
              const rightLine = row.right === null ? '' : rightText.split('\n')[row.right];
              return (
                <div key={index} className="mono grid grid-cols-2 text-[11px]">
                  <div className={cn('px-2 py-0.5', row.left === null ? 'bg-panel' : row.right === null ? 'bg-danger/15' : '')}>
                    <span className="mr-2 inline-block w-8 select-none text-muted">{row.left === null ? '' : row.left + 1}</span>{leftLine}
                  </div>
                  <div className={cn('px-2 py-0.5', row.right === null ? 'bg-panel' : row.left === null ? 'bg-ok/15' : '')}>
                    <span className="mr-2 inline-block w-8 select-none text-muted">{row.right === null ? '' : row.right + 1}</span>{rightLine}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </Dialog>
  );
}

function EvidenceDialog({ target, onClose }: {
  target: { versionId: string; line: number; title: string } | null; onClose: () => void;
}) {
  const [lines, setLines] = useState<{ line: number; text: string }[]>([]);
  useEffect(() => {
    if (!target) return;
    const start = Math.max(1, target.line - 4);
    api<{ lines: { line: number; text: string }[] }>(`/versions/${target.versionId}/lines?start=${start}&end=${target.line + 4}`)
      .then((data) => setLines(data.lines)).catch(() => setLines([]));
  }, [target]);
  return (
    <Dialog open={!!target} onClose={onClose} title={target?.title ?? '原文'}>
      <pre className="mono max-h-[60vh] overflow-auto rounded-lg bg-panel-2 p-3 text-[11px] leading-relaxed" data-testid="evidence-body">
        {lines.map((row) => `${String(row.line).padStart(4, ' ')} | ${row.text}`).join('\n')}
      </pre>
    </Dialog>
  );
}

function ChangeDialog({ finding, deviceName, onClose, onSubmit }: {
  finding: Finding | null; deviceName: string; onClose: () => void;
  onSubmit: (finding: Finding, title: string, rationale: string, commands: string[]) => void;
}) {
  const [title, setTitle] = useState('修复规则发现的配置偏差');
  const [rationale, setRationale] = useState('规则检查发现该配置不符合规范，需要人工核对后修复。');
  const [commands, setCommands] = useState('interface eth2\n ip ospf cost 10');
  useEffect(() => {
    if (finding) {
      setTitle(`修复 ${finding.rule_id}`);
      setRationale(finding.message);
    }
  }, [finding]);
  return (
    <Dialog open={!!finding} onClose={onClose} title={`生成变更单 · ${deviceName}`}>
      <div className="space-y-3">
        <Field label="标题"><Input value={title} onChange={(event) => setTitle(event.target.value)} data-testid="cr-title" /></Field>
        <Field label="理由"><Textarea rows={2} value={rationale} onChange={(event) => setRationale(event.target.value)} data-testid="cr-rationale" /></Field>
        <Field label="建议命令（不会自动执行）">
          <Textarea rows={4} value={commands} onChange={(event) => setCommands(event.target.value)} data-testid="cr-commands" />
        </Field>
        <Button variant="primary" className="w-full" data-testid="cr-submit"
                disabled={!finding || !title.trim() || !rationale.trim() || !commands.trim()}
                onClick={() => finding && onSubmit(finding, title.trim(), rationale.trim(),
                  commands.split('\n').map((line) => line.trim()).filter(Boolean))}>
          创建变更单
        </Button>
      </div>
    </Dialog>
  );
}

function NoteDialog({ target, onClose, onSubmit }: {
  target: { requestId: string; action: string } | null; onClose: () => void;
  onSubmit: (requestId: string, action: string, note: string) => void;
}) {
  const [note, setNote] = useState('');
  useEffect(() => { setNote(''); }, [target]);
  return (
    <Dialog open={!!target} onClose={onClose} title={`操作说明 · ${target?.action ?? ''}`}>
      <Field label="说明（写入审计事件）">
        <Textarea rows={3} value={note} onChange={(event) => setNote(event.target.value)} data-testid="transition-note" />
      </Field>
      <Button variant="primary" className="mt-3 w-full" disabled={!note.trim()} data-testid="transition-confirm"
              onClick={() => target && onSubmit(target.requestId, target.action, note.trim())}>
        确认
      </Button>
    </Dialog>
  );
}

function ExemptDialog({ item, onClose, onSubmit }: {
  item: DriftItem | null; onClose: () => void;
  onSubmit: (item: DriftItem, days: number, reason: string) => void;
}) {
  const [days, setDays] = useState('7');
  const [reason, setReason] = useState('已知偏差，等待下个维护窗口统一整改');
  useEffect(() => { setDays('7'); setReason('已知偏差，等待下个维护窗口统一整改'); }, [item]);
  const daysNumber = Number(days);
  const valid = Number.isInteger(daysNumber) && daysNumber >= 1 && daysNumber <= 365 && reason.trim().length >= 10;
  return (
    <Dialog open={!!item} onClose={onClose} title="登记豁免">
      <p className="mb-3 text-xs leading-relaxed text-muted">
        豁免绑定当前值与路径，只在此值未变时生效；到期后自动恢复为待处理。
      </p>
      <div className="space-y-3">
        <Field label="漂移路径"><Input value={item?.path ?? ''} readOnly /></Field>
        <Field label="有效期（天，1–365）"><Input value={days} onChange={(event) => setDays(event.target.value)} data-testid="exempt-days" /></Field>
        <Field label="原因（≥10 字）">
          <Textarea rows={3} value={reason} onChange={(event) => setReason(event.target.value)} data-testid="exempt-reason" />
        </Field>
        <Button variant="primary" className="w-full" disabled={!item || !valid} data-testid="exempt-submit"
                onClick={() => item && onSubmit(item, daysNumber, reason.trim())}>
          登记豁免
        </Button>
      </div>
    </Dialog>
  );
}

/* ---------------- SSH collection + change verification ---------------- */
function ChangeVerifyCard({ target, runs, busy, onSaveTarget, onCollect, onStart, onComplete }: {
  target: DeviceTarget | null; runs: ChangeRun[]; busy: string;
  onSaveTarget: (fields: { host: string; port: number; username: string; credential_ref: string }) => void;
  onCollect: () => void;
  onStart: () => void;
  onComplete: (runId: string) => void;
}) {
  const [host, setHost] = useState(target?.host ?? '');
  const [port, setPort] = useState(String(target?.port ?? 22));
  const [username, setUsername] = useState(target?.username ?? '');
  const [credentialRef, setCredentialRef] = useState(target?.credential_ref ?? '');
  useEffect(() => {
    setHost(target?.host ?? '');
    setPort(String(target?.port ?? 22));
    setUsername(target?.username ?? '');
    setCredentialRef(target?.credential_ref ?? '');
  }, [target]);
  const open = runs.filter((run) => run.status === 'open');
  const portNumber = Number(port);
  const valid = host.trim() && username.trim() && Number.isInteger(portNumber) && portNumber >= 1 && portNumber <= 65535;
  return (
    <Card>
      <CardHeader title="SSH 采集与变更核对" hint="只读命令白名单；变更前/后各采一次快照，比较差异并重跑规则"
                  action={<div className="flex gap-1.5">
                    <Button size="sm" variant="ghost" disabled={!!busy || !target} onClick={onCollect} data-testid="collect-now">
                      <RefreshCw size={13} /> 采集当前配置
                    </Button>
                    <Button size="sm" variant="primary" disabled={!!busy || !target} onClick={onStart} data-testid="start-change">
                      <GitBranch size={13} /> 开始变更窗口
                    </Button>
                  </div>} />
      <div className="grid gap-2 sm:grid-cols-5">
        <Field label="主机"><Input value={host} onChange={(event) => setHost(event.target.value)} data-testid="target-host" placeholder="10.0.0.1 或 127.0.0.1" /></Field>
        <Field label="端口"><Input value={port} onChange={(event) => setPort(event.target.value)} data-testid="target-port" /></Field>
        <Field label="用户名"><Input value={username} onChange={(event) => setUsername(event.target.value)} data-testid="target-username" /></Field>
        <Field label="密码环境变量" hint="只存变量名，不存密码">
          <Input value={credentialRef} onChange={(event) => setCredentialRef(event.target.value)} data-testid="target-credential" placeholder="NETCFG_SSH_PASSWORD" />
        </Field>
        <div className="flex items-end">
          <Button variant="outline" className="w-full" disabled={!!busy || !valid} data-testid="target-save"
                  onClick={() => onSaveTarget({ host: host.trim(), port: portNumber, username: username.trim(), credential_ref: credentialRef.trim() })}>
            保存目标
          </Button>
        </div>
      </div>
      <p className="mt-2 text-[11px] text-muted">
        服务器只执行 <code className="mono">show running-config</code> 等白名单命令；密码从后端环境变量读取，接口不回显。
      </p>
      <div className="mt-3 space-y-2" data-testid="change-runs">
        {runs.length === 0 && <EmptyState title="还没有变更核对" hint="配置目标后点「开始变更窗口」" />}
        {runs.slice(0, 5).map((run) => (
          <article key={run.id} className="rounded-xl border border-line bg-panel-2 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={run.status === 'completed' ? 'ok' : run.status === 'failed' ? 'danger' : 'warn'}>{run.status}</Badge>
              <span className="text-[11px] text-muted">{when(run.created_at)} · {run.created_by}</span>
              {run.status === 'open' && (
                <Button size="sm" variant="ghost" disabled={!!busy} data-testid="complete-change"
                        onClick={() => onComplete(run.id)}>完成核对</Button>
              )}
            </div>
            <p className="mt-1 text-xs text-fg/90">{run.note}</p>
            {run.status === 'completed' && (
              <>
                <p className="mt-1 text-[11px] text-muted">
                  差异 {run.diff_items.length} 项 · 规则：违规 {run.findings_counts.findings_violated ?? 0}、无法判定 {run.findings_counts.findings_undetermined ?? 0}
                </p>
                {run.diff_items.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {run.diff_items.slice(0, 6).map((item) => (
                      <code key={item.path} className="mono rounded bg-panel px-1.5 py-0.5 text-[10px]">{item.path}</code>
                    ))}
                  </div>
                )}
              </>
            )}
            {run.status === 'failed' && <p className="mt-1 text-[11px] text-danger">{run.error_message}</p>}
          </article>
        ))}
      </div>
    </Card>
  );
}

function ChangeNoteDialog({ target, onClose, onSubmit }: {
  target: { mode: 'start' | 'complete'; runId?: string } | null;
  onClose: () => void; onSubmit: (note: string) => void;
}) {
  const [note, setNote] = useState('');
  useEffect(() => {
    setNote(target?.mode === 'start' ? '变更窗口：调整接口 OSPF cost，变更后核对差异' : '变更后采集并核对：确认差异与规则结果');
  }, [target]);
  return (
    <Dialog open={!!target} onClose={onClose} title={target?.mode === 'start' ? '开始变更窗口' : '完成变更核对'}>
      <Field label="说明（至少 10 字，写入记录）">
        <Textarea rows={3} value={note} onChange={(event) => setNote(event.target.value)} data-testid="change-note" />
      </Field>
      <Button variant="primary" className="mt-3 w-full" disabled={note.trim().length < 10} data-testid="change-submit"
              onClick={() => onSubmit(note.trim())}>
        {target?.mode === 'start' ? '采集变更前快照' : '采集变更后快照并核对'}
      </Button>
    </Dialog>
  );
}

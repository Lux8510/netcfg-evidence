const state = {
  devices: [],
  deviceId: null,
  versions: [],
  baseline: null,
  findings: null,
  changeRequest: null,
};

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
));

async function api(path, options = {}) {
  const init = { method: options.method || (options.body ? 'POST' : 'GET') };
  if (options.body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(`/api${path}`, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.error?.message || `请求失败 ${response.status}`);
  return payload.data;
}

function status(text, kind = 'info') {
  const node = $('#status');
  node.textContent = text || '';
  node.className = `status ${text ? kind : ''}`;
  if (text) setTimeout(() => { if (node.textContent === text) node.textContent = ''; }, 6000);
}

function badge(text, kind) { return `<span class="badge ${kind}">${esc(text)}</span>`; }
const shortSha = (value) => (value ? value.slice(0, 10) : '—');
const when = (iso) => (iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '—');

// ---------- devices ----------
async function loadDevices() {
  state.devices = await api('/devices');
  const select = $('#device-select');
  select.innerHTML = '<option value="">选择设备…</option>' +
    state.devices.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('');
  if (!state.devices.some((d) => d.id === state.deviceId)) state.deviceId = state.devices[0]?.id || null;
  select.value = state.deviceId || '';
  await refreshDevice();
}

async function createDevice() {
  const name = $('#device-name').value.trim();
  if (!name) return status('请填写设备名', 'error');
  try {
    const device = await api('/devices', { body: { name } });
    $('#device-name').value = '';
    state.deviceId = device.id;
    await loadDevices();
    status(`已创建设备 ${device.name}`, 'ok');
  } catch (error) { status(error.message, 'error'); }
}

async function refreshDevice() {
  // Never let the previous device's findings or change form leak into the new one.
  state.findings = null;
  state.changeRequest = null;
  $('#change-form').classList.add('hidden');
  renderDrift(null); renderRules(null); renderExplain([]);
  if (!state.deviceId) {
    state.versions = []; state.baseline = null;
    renderVersions(); renderChanges([]);
    $('#baseline-label').textContent = '';
    return;
  }
  [state.versions, state.baseline] = await Promise.all([
    api(`/devices/${state.deviceId}/configs`),
    api(`/devices/${state.deviceId}/baseline`),
  ]);
  renderVersions(); renderBaselineLabel();
  await Promise.all([loadDrift(), loadFindings(), loadChanges(), loadExplain()]);
}

function renderBaselineLabel() {
  const label = $('#baseline-label');
  if (!state.baseline) { label.textContent = '尚未设置基线'; return; }
  const version = state.versions.find((v) => v.id === state.baseline.config_version_id);
  label.textContent = `当前基线：${version ? version.source_name : shortSha(state.baseline.config_version_id)}`;
}

function renderVersions() {
  const list = $('#version-list');
  if (!state.versions.length) {
    list.innerHTML = '<p class="muted">还没有配置版本，先导入一份配置。</p>';
    return;
  }
  list.innerHTML = state.versions.map((version) => {
    const isBaseline = state.baseline?.config_version_id === version.id;
    return `<article class="item">
      <div class="item-head">
        <strong>${esc(version.source_name)}</strong>
        ${isBaseline ? badge('基线', 'ok') : ''}
        ${version.unparsed.length ? badge(`未解析 ${version.unparsed.length}`, 'warn') : ''}
      </div>
      <div class="muted small">${when(version.created_at)} · ${version.line_count} 行 · canonical ${shortSha(version.canonical_sha256)}</div>
      <div class="row gap">
        <button class="btn ghost small" data-baseline="${esc(version.id)}">设为基线</button>
        <button class="btn ghost small" data-lines="${esc(version.id)}">查看原文</button>
      </div>
    </article>`;
  }).join('');
  list.querySelectorAll('[data-baseline]').forEach((button) => {
    button.onclick = () => setBaseline(button.dataset.baseline);
  });
  list.querySelectorAll('[data-lines]').forEach((button) => {
    button.onclick = () => showLines(button.dataset.lines, 1, 200, '原文');
  });
}

async function setBaseline(versionId) {
  try {
    state.baseline = await api(`/devices/${state.deviceId}/baseline`, { body: { version_id: versionId } });
    renderVersions(); renderBaselineLabel();
    status('基线已更新', 'ok');
  } catch (error) { status(error.message, 'error'); }
}

// ---------- import ----------
async function importConfig(event) {
  event.preventDefault();
  const text = $('#config-text').value;
  if (!text.trim()) return status('请粘贴配置内容', 'error');
  try {
    const version = await api(`/devices/${state.deviceId}/configs`, {
      body: { text, source_name: $('#source-name').value.trim() || 'manual' },
    });
    $('#config-text').value = ''; $('#source-name').value = '';
    await refreshDevice();
    status(version.unchanged ? '相同配置已存在，返回原版本' : '配置已导入', 'ok');
  } catch (error) { status(error.message, 'error'); }
}

// ---------- drift ----------
async function loadDrift() {
  if (!state.deviceId) return renderDrift(null);
  const rows = await api(`/devices/${state.deviceId}/drift`);
  // Read-only on load. Computing a report is an explicit user action.
  if (rows.length) { renderDrift(rows[0]); return; }
  renderDrift(null, state.baseline ? '还没有漂移报告，点击「与基线比对」生成。' : null);
}

async function computeDrift() {
  try {
    const report = await api(`/devices/${state.deviceId}/drift`, { body: {} });
    renderDrift(report);
    status(report.identical ? '与基线一致' : `发现 ${report.items.length} 处漂移`, report.identical ? 'ok' : 'info');
  } catch (error) { status(error.message, 'error'); }
}

function renderDrift(report, note) {
  const host = $('#drift-result');
  if (!report) { host.innerHTML = note ? `<p class="muted">${esc(note)}</p>` : ''; return; }
  const summary = report.summary;
  host.innerHTML = `
    <div class="cards">
      <div class="card"><span>新增</span><strong>${summary.added}</strong></div>
      <div class="card"><span>删除</span><strong>${summary.removed}</strong></div>
      <div class="card"><span>修改</span><strong>${summary.changed}</strong></div>
      <div class="card"><span>未解析行</span><strong>${summary.unparsed_current ?? 0}</strong></div>
    </div>
    ${report.identical ? '<p class="muted">两份配置语义一致。</p>' : ''}
    <div class="list">${report.items.map((item) => `
      <article class="item">
        <div class="item-head"><code>${esc(item.path)}</code>${badge(item.kind, item.kind)}</div>
        <div class="diff">
          <div><span class="muted">基线</span><pre>${esc(JSON.stringify(item.old, null, 1))}</pre></div>
          <div><span class="muted">当前</span><pre>${esc(JSON.stringify(item.new, null, 1))}</pre></div>
        </div>
        <div class="row gap">
          ${item.old_lines.length ? `<button class="btn ghost small" data-v="${esc(report.baseline_version_id)}" data-from="${item.old_lines[0]}" data-to="${item.old_lines[item.old_lines.length - 1]}">基线第 ${item.old_lines.join(',')} 行</button>` : ''}
          ${item.new_lines.length ? `<button class="btn ghost small" data-v="${esc(report.current_version_id)}" data-from="${item.new_lines[0]}" data-to="${item.new_lines[item.new_lines.length - 1]}">当前第 ${item.new_lines.join(',')} 行</button>` : ''}
        </div>
      </article>`).join('')}</div>`;
  host.querySelectorAll('[data-v]').forEach((button) => {
    button.onclick = () => showLines(button.dataset.v, button.dataset.from, button.dataset.to, '证据原文');
  });
}

// ---------- rules ----------
async function loadFindings() {
  if (!state.deviceId) return renderRules(null);
  const runs = await api(`/devices/${state.deviceId}/rules/runs`);
  if (!runs.length) { renderRules(null); return; }
  const latest = runs[0];
  const rows = await api(`/devices/${state.deviceId}/findings?run_id=${encodeURIComponent(latest.run_id)}`);
  renderRules({
    saved: true,
    config_version_id: latest.config_version_id,
    counts: latest.counts,
    digest: { parser_version: 'saved', matches: true },
    findings: rows,
  });
}

async function runRules() {
  try {
    const result = await api(`/devices/${state.deviceId}/rules/check`, { body: {} });
    state.findings = result;
    renderRules(result);
    status(`规则 ${result.counts.rules_checked} 条：符合 ${result.counts.rules_satisfied}，违规 ${result.counts.findings_violated}，无法判定 ${result.counts.findings_undetermined}`, 'info');
  } catch (error) { status(error.message, 'error'); }
}

function renderRules(result) {
  const host = $('#rules-result');
  if (!result) { host.innerHTML = ''; return; }
  const c = result.counts;
  host.innerHTML = `
    <div class="cards">
      <div class="card"><span>已检查</span><strong>${c.rules_checked}</strong></div>
      <div class="card"><span>符合</span><strong>${c.rules_satisfied}</strong></div>
      <div class="card"><span>违规</span><strong>${c.findings_violated}</strong></div>
      <div class="card"><span>无法判定</span><strong>${c.findings_undetermined}</strong></div>
      <div class="card"><span>不适用</span><strong>${c.rules_not_applicable}</strong></div>
    </div>
    <p class="muted small">${result.saved ? '来自已保存的检查记录；点击「运行规则检查」可重新计算' : `解析器 ${esc(result.digest.parser_version)} · 重算摘要 ${result.digest.matches ? '一致' : '不一致'}`}</p>
    ${result.findings.length ? `<div class="list">${result.findings.map((finding) => `
      <article class="item">
        <div class="item-head"><code>${esc(finding.rule_id)}</code>${badge(finding.status, finding.status === 'violated' ? 'error' : 'warn')}</div>
        <p>${esc(finding.message)}</p>
        ${finding.evidence.map((ev) => `
          <div class="evidence">
            <span class="muted small">第 ${ev.line} 行</span>
            <code>${esc(ev.text)}</code>
            <button class="btn ghost small" data-v="${esc(finding.config_version_id || result.config_version_id)}" data-from="${ev.line}" data-to="${ev.line}">看原文</button>
          </div>`).join('')}
        ${finding.status === 'violated' && !finding.rule_id.startsWith('__')
          ? `<button class="btn ghost small" data-change="${esc(finding.id)}">生成变更单</button>` : ''}
      </article>`).join('')}</div>` : '<p class="muted">没有需要说明的 Finding。</p>'}`;
  host.querySelectorAll('[data-v]').forEach((button) => {
    button.onclick = () => showLines(button.dataset.v, button.dataset.from, button.dataset.to, '证据原文');
  });
  host.querySelectorAll('[data-change]').forEach((button) => {
    button.onclick = () => openChangeForm(button.dataset.change);
  });
}

// ---------- change requests ----------
function openChangeForm(findingId) {
  state.changeRequest = { findingId };
  const form = $('#change-form');
  form.classList.remove('hidden');
  $('#cr-title').value = '修复规则发现的配置偏差';
  $('#cr-rationale').value = '规则检查发现该接口不符合规范，需要人工核对后修复。';
  $('#cr-commands').value = 'interface eth2\n ip ospf cost 10';
  $('#cr-actor').value = localStorage.getItem('actor') || '';
  form.scrollIntoView({ behavior: 'smooth' });
}

async function submitChange() {
  const actor = $('#cr-actor').value.trim();
  localStorage.setItem('actor', actor);
  const commands = $('#cr-commands').value.split('\n').map((line) => line.trim()).filter(Boolean);
  try {
    const request = await api(`/devices/${state.deviceId}/change-requests`, {
      body: {
        title: $('#cr-title').value.trim(),
        rationale: $('#cr-rationale').value.trim(),
        finding_ids: [state.changeRequest.findingId],
        proposed_commands: commands,
        actor,
      },
    });
    $('#change-form').classList.add('hidden');
    await loadChanges();
    status(`变更单已创建：${request.title}`, 'ok');
  } catch (error) { status(error.message, 'error'); }
}

async function loadChanges() {
  if (!state.deviceId) return renderChanges([]);
  const rows = await api(`/devices/${state.deviceId}/change-requests`);
  renderChanges(rows);
}

function renderChanges(rows) {
  const host = $('#changes-result');
  if (!rows.length) { host.innerHTML = '<p class="muted">还没有变更单。先在「规则与证据」里对一条违规生成变更单。</p>'; return; }
  host.innerHTML = rows.map((row) => `
    <article class="item">
      <div class="item-head"><strong>${esc(row.title)}</strong>${badge(row.status, statusKind(row.status))}</div>
      <div class="muted small">${esc(row.rationale)}</div>
      <div class="muted small">创建 ${esc(row.created_by)} · ${when(row.created_at)}${row.approved_by ? ` · 审批 ${esc(row.approved_by)}` : ''}</div>
      <pre class="commands">${esc(row.proposed_commands.join('\n'))}</pre>
      <div class="row gap">
        ${row.status === 'draft' ? actionButton(row.id, 'submit', '提交审批') : ''}
        ${row.status === 'pending_approval' ? actionButton(row.id, 'approve', '批准') + actionButton(row.id, 'reject', '拒绝') : ''}
        ${row.status === 'approved' ? actionButton(row.id, 'apply', '标记已执行') : ''}
        ${row.status === 'draft' ? actionButton(row.id, 'cancel', '取消') : ''}
        <button class="btn ghost small" data-events="${esc(row.id)}">审计事件</button>
      </div>
      <div class="events" id="events-${esc(row.id)}"></div>
    </article>`).join('');
  host.querySelectorAll('[data-action]').forEach((button) => {
    button.onclick = () => transition(button.dataset.id, button.dataset.action);
  });
  host.querySelectorAll('[data-events]').forEach((button) => {
    button.onclick = () => loadEvents(button.dataset.events);
  });
}

function statusKind(status) {
  return { approved: 'ok', applied: 'ok', rejected: 'error', pending_approval: 'warn' }[status] || 'muted';
}

function actionButton(id, action, label) {
  return `<button class="btn ghost small" data-action="${action}" data-id="${esc(id)}">${label}</button>`;
}

async function transition(requestId, action) {
  const actor = localStorage.getItem('actor') || prompt('审批人 / 操作人（真实姓名）') || '';
  if (!actor) return;
  localStorage.setItem('actor', actor);
  const note = prompt('操作说明（会写入审计事件）') || '';
  if (!note) return status('必须填写操作说明', 'error');
  try {
    await api(`/change-requests/${requestId}/${action}`, {
      body: { actor, note, idempotency_key: crypto.randomUUID() },
    });
    await loadChanges();
    status('状态已更新', 'ok');
  } catch (error) { status(error.message, 'error'); }
}

async function loadEvents(requestId) {
  const host = document.getElementById(`events-${requestId}`);
  if (!host) return;
  const rows = await api(`/change-requests/${requestId}/events`);
  host.innerHTML = rows.map((row) => `<div class="muted small">${when(row.created_at)} · ${esc(row.action)} · ${esc(row.actor)} · ${esc(row.note)}</div>`).join('');
}

// ---------- explanations ----------
async function runExplain() {
  try {
    const rows = await api(`/devices/${state.deviceId}/explanations`, { body: {} });
    renderExplain(rows);
    status(`生成 ${rows.length} 条解释`, 'ok');
  } catch (error) { status(error.message, 'error'); }
}

async function loadExplain() {
  if (!state.deviceId) return renderExplain([]);
  const rows = await api(`/devices/${state.deviceId}/explanations`);
  renderExplain(rows);
}

function renderExplain(rows) {
  const host = $('#explain-result');
  if (!rows.length) { host.innerHTML = '<p class="muted">还没有解释记录。</p>'; return; }
  host.innerHTML = rows.map((row) => `
    <article class="item">
      <div class="item-head"><code>${esc(row.rule_id)}</code>${badge(row.status, row.status === 'accepted' ? 'ok' : 'error')}<span class="muted small">${esc(row.reason)}</span></div>
      <p class="pre">${esc(row.text)}</p>
      <div class="muted small">引用：${row.citations.map((c) => `${esc(c.rule_id)} 第 ${c.lines.map(Number).filter(Number.isInteger).join(',')} 行`).join('；') || '无'} · 模型 ${esc(row.model)}</div>
    </article>`).join('');
}

// ---------- evidence lines ----------
async function showLines(versionId, from, to, title) {
  try {
    const data = await api(`/versions/${versionId}/lines?start=${from}&end=${to}`);
    $('#lines-title').textContent = `${title} · 第 ${data.start}–${data.end} 行`;
    $('#lines-body').textContent = data.lines.map((line) => `${String(line.line).padStart(4, ' ')} | ${line.text}`).join('\n');
    $('#lines-dialog').showModal();
  } catch (error) { status(error.message, 'error'); }
}

// ---------- wiring ----------
$('#device-select').onchange = async (event) => { state.deviceId = event.target.value || null; await refreshDevice(); };
$('#create-device').onclick = createDevice;
$('#import-form').onsubmit = importConfig;
$('#compute-drift').onclick = computeDrift;
$('#run-rules').onclick = runRules;
$('#run-explain').onclick = runExplain;
$('#cr-submit').onclick = submitChange;
$('#cr-cancel').onclick = () => $('#change-form').classList.add('hidden');
$('#lines-close').onclick = () => $('#lines-dialog').close();
function activateTab(name) {
  const target = document.querySelector(`.tab[data-tab="${name}"]`);
  if (!target) return;
  document.querySelectorAll('.tab').forEach((node) => node.classList.toggle('active', node === target));
  document.querySelectorAll('.tabpane').forEach((pane) => pane.classList.toggle('active', pane.id === `pane-${name}`));
}

document.querySelectorAll('.tab').forEach((tab) => {
  tab.onclick = () => {
    activateTab(tab.dataset.tab);
    history.replaceState(null, '', `#${tab.dataset.tab}`);
  };
});

activateTab(location.hash.replace('#', '') || 'drift');
loadDevices().catch((error) => status(error.message, 'error'));

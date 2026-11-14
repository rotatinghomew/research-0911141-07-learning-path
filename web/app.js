const state = {context: null, current: null, mastery: null, dataset: null, policy: null};
const element = identifier => document.getElementById(identifier);
const LABEL_TEXT = {mastered: '已掌握', needs_practice: '需要练习', emerging: '形成中', unknown: '无证据'};

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '请求失败');
  return value;
}

function message(value, error = false) {
  element('status').textContent = value;
  element('status').className = error ? 'error' : '';
}

async function guarded(action) {
  try { await action(); } catch (error) { message(error.message, true); }
}

function selected() {
  const selection = {};
  for (const control of state.context.product.options) selection[control[0]] = element('select-' + control[0]).value;
  return {revision: element('revision').value, selection};
}

function learner() {
  return element('select-learner') ? element('select-learner').value : state.context.product.selection.learner;
}

function renderControls() {
  const revision = element('revision').value || state.context.default_revision;
  element('revision').replaceChildren();
  for (const dataset of state.context.datasets) {
    const option = document.createElement('option');
    option.value = dataset.revision;
    option.textContent = dataset.label + ' · ' + dataset.revision.slice(0, 9);
    element('revision').append(option);
  }
  element('revision').value = revision;
  if (!element('revision').value) element('revision').value = state.context.default_revision;
  if (element('controls').children.length) return;
  for (const [key, labelText, choices] of state.context.product.options) {
    const label = document.createElement('label');
    label.textContent = labelText;
    const select = document.createElement('select');
    select.id = 'select-' + key;
    for (const choice of choices) {
      const option = document.createElement('option');
      option.value = choice;
      option.textContent = choice;
      select.append(option);
    }
    select.value = state.context.product.selection[key];
    label.append(select);
    element('controls').append(label);
  }
}

function displayValue(value) {
  if (value === null || value === undefined) return '未观测';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4);
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

function drawChart(series) {
  const canvas = element('chart');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  const values = series.filter(point => typeof point.y === 'number' && Number.isFinite(point.y));
  context.font = '12px sans-serif';
  if (!values.length) { context.fillText('该范围没有可定量的描述值', 30, 50); return; }
  const low = Math.min(0, ...values.map(point => point.y));
  const high = Math.max(1, ...values.map(point => point.y));
  const scale = value => 250 - (value - low) / (high - low || 1) * 210;
  context.strokeStyle = '#90a5ad';
  context.beginPath(); context.moveTo(45, 20); context.lineTo(45, 250); context.lineTo(940, 250); context.stroke();
  context.fillStyle = '#0a7168';
  const scatter = values.every(point => typeof point.x === 'number');
  const minimumX = scatter ? Math.min(...values.map(point => point.x)) : 0;
  const maximumX = scatter ? Math.max(...values.map(point => point.x)) : 1;
  values.forEach((point, index) => {
    const position = scatter ? 60 + (point.x - minimumX) / (maximumX - minimumX || 1) * 850 : 55 + index * 875 / values.length;
    if (scatter) {
      context.beginPath(); context.arc(position, scale(point.y), 3.5, 0, Math.PI * 2); context.fill();
    } else {
      context.fillRect(position, Math.min(scale(point.y), scale(0)), Math.max(3, 800 / values.length), Math.max(1, Math.abs(scale(point.y) - scale(0))));
    }
    if (values.length < 12) context.fillText(point.label, position, 275);
  });
  context.fillStyle = '#29434e';
  context.fillText(high.toFixed(2), 0, 45); context.fillText(low.toFixed(2), 0, 250);
  context.fillText('按表中顺序展示描述值；缺测不绘制为零', 50, 20);
}

function render(result, heading) {
  state.current = result;
  element('summary').replaceChildren();
  for (const [label, value] of Object.entries(result.view.summary)) {
    const card = document.createElement('article');
    const title = document.createElement('span'); title.textContent = label;
    const amount = document.createElement('strong'); amount.textContent = displayValue(value);
    card.append(title, amount); element('summary').append(card);
  }
  element('notes').replaceChildren();
  for (const note of result.view.notes) {
    const paragraph = document.createElement('p'); paragraph.textContent = note; element('notes').append(paragraph);
  }
  const rows = result.view.rows;
  const fields = rows.length ? Object.keys(rows[0]) : [];
  const header = document.createElement('tr');
  for (const field of fields) { const cell = document.createElement('th'); cell.textContent = field; header.append(cell); }
  element('columns').replaceChildren(header); element('rows').replaceChildren();
  for (const row of rows) {
    const line = document.createElement('tr'); line.tabIndex = 0;
    for (const field of fields) { const cell = document.createElement('td'); cell.textContent = displayValue(row[field]); line.append(cell); }
    line.onclick = () => { element('details').textContent = JSON.stringify(row, null, 2); };
    line.onkeydown = event => { if (event.key === 'Enter') line.click(); };
    element('rows').append(line);
  }
  drawChart(result.view.series);
  element('details').textContent = JSON.stringify({revision: result.revision, selection: result.selection,
    history: result.view.history || null}, null, 2);
  message((heading || '已更新描述') + ' · 数据 ' + result.revision.slice(0, 12));
}

// ---------------------------------------------------------------------------
// 教学策略编辑器（查询参数，不回写数据集）
// ---------------------------------------------------------------------------

async function loadDataset() {
  state.dataset = await request('/api/data?revision=' + encodeURIComponent(element('revision').value));
  if (!state.policy || state.policy._revision !== element('revision').value) {
    state.policy = {_revision: element('revision').value,
                    prerequisites: state.dataset.prerequisites.map(pair => ({...pair})),
                    excluded_items: [], as_of: null};
  }
  renderPolicy();
}

function collectPolicy() {
  const pairs = [];
  for (const row of element('policy-prereq').querySelectorAll('.prereq-row')) {
    const selects = row.querySelectorAll('select');
    pairs.push({before: selects[0].value, after: selects[1].value});
  }
  const excluded = [];
  for (const box of element('policy-pool').querySelectorAll('input[type=checkbox]:checked')) excluded.push(box.value);
  return {prerequisites: pairs, excluded_items: excluded,
          as_of: element('policy-as-of').value || null};
}

function skillSelect(value) {
  const select = document.createElement('select');
  for (const skill of state.dataset.skills) {
    const option = document.createElement('option');
    option.value = skill; option.textContent = skill;
    if (skill === value) option.selected = true;
    select.append(option);
  }
  return select;
}

function renderPolicy() {
  const prereq = element('policy-prereq');
  prereq.replaceChildren();
  for (const pair of state.policy.prerequisites) {
    const row = document.createElement('div'); row.className = 'prereq-row';
    const before = skillSelect(pair.before);
    const arrow = document.createElement('span'); arrow.textContent = ' 先修于 ';
    const after = skillSelect(pair.after);
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '删除';
    remove.className = 'mini';
    remove.onclick = () => { state.policy.prerequisites = state.policy.prerequisites.filter(item => item !== pair); renderPolicy(); };
    row.append(before, arrow, after, remove);
    prereq.append(row);
  }
  const pool = element('policy-pool');
  pool.replaceChildren();
  const excluded = new Set(state.policy.excluded_items);
  for (const item of state.dataset.items) {
    const label = document.createElement('label'); label.className = 'pool-item';
    const box = document.createElement('input'); box.type = 'checkbox'; box.value = item.item_id;
    box.checked = excluded.has(item.item_id);
    box.onchange = () => guarded(loadMastery);
    label.append(box, document.createTextNode(item.item_id + ' · ' + item.difficulty_hint + ' · ' + item.skills.join('+')));
    pool.append(label);
  }
  element('policy-as-of').value = state.policy.as_of || '';
}

element('add-prereq').onclick = () => {
  const skills = state.dataset.skills;
  state.policy.prerequisites.push({before: skills[0], after: skills[Math.min(1, skills.length - 1)]});
  renderPolicy();
};
element('reset-policy').onclick = () => guarded(async () => {
  state.policy = {_revision: element('revision').value,
                  prerequisites: state.dataset.prerequisites.map(pair => ({...pair})),
                  excluded_items: [], as_of: null};
  renderPolicy();
  await loadMastery();
});
element('policy-as-of').onchange = () => guarded(loadMastery);
element('policy-prereq').onchange = () => guarded(loadMastery);

// ---------------------------------------------------------------------------
// 掌握判断与练习建议
// ---------------------------------------------------------------------------

function renderMastery(payload) {
  state.mastery = payload;
  const view = payload.view;
  element('model-meta').textContent = '模型 ' + view.model_version + ' · 截止 ' + view.as_of +
    ' · 可信区间为 80% 后验区间；已掌握需区间下界 ≥ 0.65 且有中等/综合题答对证据';
  const fields = ['skill', 'label', 'posterior_mean', 'credible_low', 'credible_high',
                  'correct', 'wrong', 'skipped', 'repeat_attempts', 'shared_weight_fraction'];
  const header = document.createElement('tr');
  const headers = {skill: '知识点', label: '判断', posterior_mean: '后验均值', credible_low: '区间下界',
                   credible_high: '区间上界', correct: '答对', wrong: '答错', skipped: '跳过',
                   repeat_attempts: '重做次数', shared_weight_fraction: '综合题证据占比'};
  for (const field of fields) { const cell = document.createElement('th'); cell.textContent = headers[field]; header.append(cell); }
  element('mastery-columns').replaceChildren(header);
  element('mastery-rows').replaceChildren();
  for (const row of view.skill_rows) {
    const line = document.createElement('tr'); line.tabIndex = 0;
    for (const field of fields) {
      const cell = document.createElement('td');
      cell.textContent = field === 'label' ? (LABEL_TEXT[row[field]] || row[field]) : displayValue(row[field]);
      line.append(cell);
    }
    line.onclick = () => { element('details').textContent = JSON.stringify(row, null, 2); };
    line.onkeydown = event => { if (event.key === 'Enter') line.click(); };
    element('mastery-rows').append(line);
  }
  renderRecommendation(view.recommendation);
}

function renderRecommendation(rec) {
  const panel = element('recommendation');
  panel.replaceChildren();
  const heading = document.createElement('h3');
  heading.textContent = rec.item_id ? ('目标知识点 ' + rec.target_skill + ' · 建议题目 ' + rec.item_id) : ('目标知识点 ' + String(rec.target_skill));
  panel.append(heading);
  const reason = document.createElement('p'); reason.textContent = rec.reason; panel.append(reason);
  if (rec.predicted_outcome) {
    const prediction = document.createElement('p');
    prediction.className = 'hint';
    prediction.textContent = '预测下一次结果：答对 ' + displayValue(rec.predicted_outcome.correct) +
      '，答错 ' + displayValue(rec.predicted_outcome.wrong) +
      '，跳过 ' + displayValue(rec.predicted_outcome.skipped) + '（三类概率和为1，跳过单独建模不计错）';
    panel.append(prediction);
  }
  const basisHeading = document.createElement('h4'); basisHeading.textContent = '建议针对的证据';
  panel.append(basisHeading);
  const basis = document.createElement('ul');
  for (const text of rec.evidence_basis) { const item = document.createElement('li'); item.textContent = text; basis.append(item); }
  panel.append(basis);
  const uncertaintyHeading = document.createElement('h4'); uncertaintyHeading.textContent = '不确定性';
  panel.append(uncertaintyHeading);
  const uncertainties = document.createElement('ul');
  for (const text of rec.uncertainties) { const item = document.createElement('li'); item.textContent = text; uncertainties.append(item); }
  panel.append(uncertainties);
  if (rec.rejected && rec.rejected.length) {
    const rejectedHeading = document.createElement('h4'); rejectedHeading.textContent = '未选中的候选题及原因';
    panel.append(rejectedHeading);
    const list = document.createElement('ul');
    for (const candidate of rec.rejected) {
      const item = document.createElement('li');
      item.textContent = candidate.item_id + '：' + candidate.reasons.join('；');
      list.append(item);
    }
    panel.append(list);
  }
}

async function loadMastery() {
  const payload = await request('/api/mastery', {
    revision: element('revision').value, learner: learner(), policy: collectPolicy()});
  renderMastery(payload);
  message('已更新掌握判断与建议 · 数据 ' + payload.revision.slice(0, 12));
}

element('mastery').onclick = () => guarded(loadMastery);

// ---------------------------------------------------------------------------
// 回测
// ---------------------------------------------------------------------------

function metricLine(title, metrics) {
  if (!metrics || metrics.records === 0) return title + '：无记录';
  return title + '：' + metrics.records + ' 条 · Brier ' + displayValue(metrics.brier) +
    ' · 对数损失 ' + displayValue(metrics.log_loss) + ' · 命中率 ' + displayValue(metrics.accuracy);
}

function renderBacktest(report) {
  const panel = element('backtest-panel');
  panel.replaceChildren();
  const modeText = report.mode === 'leave_one_student_out' ? '整名学生留出（模拟全新学生）' : '已知学生的增量后续表现';
  const intro = document.createElement('p');
  intro.textContent = '模式：' + modeText + ' · ' + report.leakage_check.rule +
    ' · 检查 ' + report.leakage_check.checked_records + ' 条，违规 ' + report.leakage_check.violations + ' 条';
  panel.append(intro);
  const list = document.createElement('ul');
  for (const text of report.notes) { const item = document.createElement('li'); item.textContent = text; list.append(item); }
  panel.append(list);
  const table = document.createElement('table');
  table.className = 'metric-table';
  const head = document.createElement('tr');
  for (const label of ['范围', '本模型', '全体率基线']) { const cell = document.createElement('th'); cell.textContent = label; head.append(cell); }
  table.append(head);
  for (const [label, section] of [['全部预测点', report.overall], ['冷启动（该生此前无记录）', report.cold_start]]) {
    const row = document.createElement('tr');
    row.append(Object.assign(document.createElement('td'), {textContent: label}));
    row.append(Object.assign(document.createElement('td'), {textContent: metricLine('', section.model).replace('：', '')}));
    row.append(Object.assign(document.createElement('td'), {textContent: metricLine('', section.population_baseline).replace('：', '')}));
    table.append(row);
  }
  panel.append(table);
  const caution = document.createElement('p');
  caution.className = 'hint';
  const modelBrier = report.overall.model.brier;
  const baseBrier = report.overall.population_baseline.brier;
  caution.textContent = modelBrier <= baseBrier
    ? '本模型在该历史范围的 Brier 分数不劣于全体率基线；这是回测结论，不保证未来表现。'
    : '注意：本模型在该历史范围的 Brier 分数（' + displayValue(modelBrier) + '）高于全体率基线（' +
      displayValue(baseBrier) + '），即当前证据量下个人化追踪未优于简单基线，教学决定不应只依赖模型标签。';
  panel.append(caution);
  const per = document.createElement('details');
  const summary = document.createElement('summary'); summary.textContent = '按学生展开'; per.append(summary);
  const perList = document.createElement('ul');
  for (const [student, section] of Object.entries(report.per_learner)) {
    const item = document.createElement('li');
    item.textContent = student + '：模型 Brier ' + displayValue(section.model.brier) +
      '，基线 Brier ' + displayValue(section.population_baseline.brier);
    perList.append(item);
  }
  per.append(perList);
  panel.append(per);
}

element('backtest').onclick = () => guarded(async () => {
  message('回测运行中：每个预测点只重建当时可见的证据状态…');
  const payload = await request('/api/backtest', {
    revision: element('revision').value, mode: element('backtest-mode').value, policy: collectPolicy()});
  renderBacktest(payload.report);
  message('回测完成 · 数据 ' + payload.revision.slice(0, 12));
});

// ---------------------------------------------------------------------------
// 发布与存档
// ---------------------------------------------------------------------------

element('publish').onclick = () => guarded(async () => {
  if (!state.mastery) throw new Error('请先生成掌握判断与建议');
  const published = await request('/api/advice', {
    revision: element('revision').value, learner: learner(),
    policy: collectPolicy(), title: element('advice-title').value});
  await refresh();
  element('details').textContent = JSON.stringify(published, null, 2);
  message('已发布冻结建议 #' + published.id + '，后续数据与策略变化不会修改它');
});

async function refresh() {
  state.context = await request('/api/context');
  renderControls();
  await loadDataset();
  element('archive').replaceChildren();
  for (const notebook of state.context.notebooks) {
    const button = document.createElement('button'); button.textContent = notebook.title + ' #' + notebook.id;
    button.onclick = () => guarded(async () => {
      const saved = await request('/api/notebooks/' + notebook.id);
      element('revision').value = saved.revision;
      for (const [key, value] of Object.entries(saved.selection)) {
        const control = element('select-' + key); if (control) control.value = value;
      }
      render(saved.result, '已读取保存的描述，不随新数据变化');
    });
    element('archive').append(button);
  }
  element('advice-archive').replaceChildren();
  for (const advice of state.context.advice_publications) {
    const button = document.createElement('button');
    button.textContent = advice.title + ' · ' + advice.learner_id + ' #' + advice.id;
    button.onclick = () => guarded(async () => {
      const saved = await request('/api/advice/' + advice.id);
      element('details').textContent = JSON.stringify(saved, null, 2);
      message('已读取发布建议 #' + advice.id + '（' + advice.created_at.slice(0, 10) + ' 冻结）');
    });
    element('advice-archive').append(button);
  }
}

element('inspect').onclick = () => guarded(async () => render(await request('/api/explore', selected())));
element('save').onclick = () => guarded(async () => {
  const saved = await request('/api/notebooks', {...selected(), title: element('title').value});
  await refresh(); render(saved.result, '已保存记录 #' + saved.id);
});
element('raw').onclick = () => guarded(async () => {
  const revision = state.current ? state.current.revision : element('revision').value;
  element('details').textContent = JSON.stringify(await request('/api/data?revision=' + encodeURIComponent(revision)), null, 2);
});
element('upload').onchange = () => guarded(async () => {
  const file = element('upload').files[0]; if (!file) return;
  const data = JSON.parse(await file.text());
  const uploaded = await request('/api/datasets', {label: file.name, data});
  state.policy = null;
  await refresh(); element('revision').value = uploaded.revision;
  await loadDataset();
  render(await request('/api/explore', selected()), '已导入独立数据版本');
});
element('export').onclick = () => guarded(() => {
  if (!state.current) throw new Error('请先查看一个范围');
  const blob = new Blob([JSON.stringify({description: state.current, mastery: state.mastery,
    data: state.dataset}, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob); const link = document.createElement('a');
  link.href = url; link.download = 'analysis-' + state.current.revision.slice(0, 12) + '.json'; link.click();
  URL.revokeObjectURL(url);
});
guarded(async () => {
  await refresh();
  render(await request('/api/explore', selected()));
  await loadMastery();
});

let conflicts = [
  { type: 'ADMISSIONS', title: 'J. Smith · MRN 004821', detail: 'Admission date is different between the HIS export and the manual occupancy sheet.', sources: ['HIS: 13 Nov, 19:42', 'BED: 14 Nov, 07:00'], value: '13 Nov, 19:42', note: 'HIS timestamp takes precedence', confidence: 'MEDIUM', confidenceClass: 'medium' },
  { type: 'DISCHARGES', title: 'R. Ndlovu · MRN 003109', detail: 'Manual sheet shows a discharge that is not present in the HIS event export.', sources: ['HIS: No event', 'BED: 14 Nov, 06:30'], value: 'Pending confirmation', note: 'Held out of discharge count', confidence: 'LOW', confidenceClass: 'low' },
  { type: 'LAB RESULTS', title: 'K. Lee · MRN 006774', detail: 'Two results share one order ID. Both rows remain visible for audit.', sources: ['LAB: 07:54 unverified', 'LAB: 08:16 verified'], value: '08:16 verified result', note: 'Latest verified result retained', confidence: 'MEDIUM', confidenceClass: 'medium' },
  { type: 'OCCUPANCY', title: 'Medical ward · 8 beds', detail: 'The occupancy sheet is one day behind the movement export for this unit.', sources: ['HIS: 74 occupied', 'BED: 72 occupied'], value: '74 occupied', note: 'Latest movement data used; sheet flagged stale', confidence: 'LOW', confidenceClass: 'low' }
];

const navItems = document.querySelectorAll('.nav-item');
const views = document.querySelectorAll('.view');
const toast = document.getElementById('toast');
const themeToggle = document.getElementById('theme-toggle');
let patientEvents = [];
let currentDashboard = { occupiedBeds: { value: 0, capacity: 0, available: 0 } };

function applyTheme(theme) {
  const selectedTheme = theme === 'dark' ? 'dark' : 'light';
  document.body.dataset.theme = selectedTheme;
  if (themeToggle) {
    themeToggle.setAttribute('aria-label', selectedTheme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
    themeToggle.title = selectedTheme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
    themeToggle.querySelector('.theme-toggle-thumb').textContent = selectedTheme === 'dark' ? '☾' : '☀';
  }
  try {
    localStorage.setItem('medigrid-theme', selectedTheme);
  } catch (error) {
    console.info('Theme preference could not be saved locally.', error);
  }
}

if (themeToggle) {
  const savedTheme = localStorage.getItem('medigrid-theme');
  applyTheme(savedTheme || 'light');
  themeToggle.addEventListener('click', () => {
    const nextTheme = document.body.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme(nextTheme);
  });
}
let queueOpenOnly = false;
let flowFilter = 'all';
let liveRefreshInProgress = false;
let bedTypes = [
  { name: 'VIP', total: 8, vacant: 5, className: 'vip' },
  { name: 'AC', total: 46, vacant: 7, className: 'ac' },
  { name: 'Non-AC', total: 54, vacant: 6, className: 'non-ac' },
  { name: 'General ward', total: 62, vacant: 8, className: 'general' }
];

function showView(name) {
  views.forEach(view => view.classList.toggle('active-view', view.id === `${name}-view`));
  navItems.forEach(item => item.classList.toggle('active', item.dataset.view === name));
  if (name === 'flow') loadPatientFlow();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function renderQueue() {
  const list = document.getElementById('queue-list');
  list.innerHTML = conflicts.map((item, index) => ({ item, index })).filter(({ item }) => !queueOpenOnly || !item.reviewed).map(({ item, index }) => `
    <div class="queue-item" data-index="${index}" data-conflict-id="${item.id || ''}">
      <div><div class="queue-label">${item.type}</div><h3>${item.title}</h3><p>${item.detail}</p></div>
      <div><div class="queue-label">Source values</div><div class="source-pair"><span>${item.sources[0]}</span><br>${item.sources[1]}</div><small>${item.occurrences || 1} matching record${(item.occurrences || 1) === 1 ? '' : 's'} summarized</small></div>
      <div class="working-value"><div class="queue-label">Working value</div><strong>${item.value}</strong><small>${item.note}</small><small class="rule-reference">${item.ruleId}: ${item.ruleName}</small></div>
      <div><div class="queue-label">Confidence</div><span class="confidence ${item.confidenceClass}">${item.confidence}</span></div>
      <button class="resolve-btn ${item.reviewed ? 'resolved' : ''}">${item.reviewed ? 'Reviewed' : 'Mark reviewed'}</button>
    </div>`).join('');
  list.querySelectorAll('.resolve-btn').forEach(button => button.addEventListener('click', async () => {
    const conflict = conflicts[Number(button.closest('.queue-item').dataset.index)];
    button.textContent = button.classList.toggle('resolved') ? 'Reviewed' : 'Mark reviewed';
    button.closest('.queue-item').classList.toggle('reviewed');
    if (conflict?.id) {
      try {
        await fetch(`/api/conflicts/${conflict.id}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reviewed: button.classList.contains('resolved') }) });
      } catch (error) {
        console.info('Review state will remain local until the API is available.', error);
      }
    }
    if (button.classList.contains('resolved')) showToast('Record marked reviewed. Source rows remain in the audit trail.');
  }));
  updateQueueTabCounts();
  updateQueueSummary();
}

function renderOverviewReview() {
  const list = document.getElementById('overview-review-list');
  if (!list) return;
  list.innerHTML = conflicts.filter(item => !item.reviewed).slice(0, 3).map(item => `
    <div class="table-row">
      <div class="record-cell"><span class="record-avatar">${item.type.slice(0, 2)}</span><div><strong>${item.title}</strong><small>${item.type}</small></div></div>
      <div><strong class="warning-text">Needs reconciliation</strong><small>${item.detail}</small></div>
      <div><strong>${item.value}</strong><small>${item.note}</small></div>
      <span class="confidence ${item.confidenceClass}">${item.confidence}</span>
      <button class="row-arrow" data-view-target="reconcile" aria-label="Open reconciliation">→</button>
    </div>`).join('') || '<div class="table-row"><span>No open conflicts</span></div>';
  list.querySelectorAll('[data-view-target]').forEach(button => button.addEventListener('click', () => showView(button.dataset.viewTarget)));
}

function conflictMatchesTab(item, tabText) {
  if (tabText.startsWith('All')) return true;
  if (tabText.startsWith('Lab')) return item.type === 'LAB RESULTS';
  if (tabText.startsWith('Admissions')) return item.type === 'ADMISSIONS';
  if (tabText.startsWith('Discharges')) return item.type === 'DISCHARGES';
  if (tabText.startsWith('Occupancy')) return item.type === 'OCCUPANCY';
  return false;
}

function updateQueueTabCounts() {
  document.querySelectorAll('.queue-tab').forEach(tab => {
    const count = conflicts.filter(item => conflictMatchesTab(item, tab.textContent)).length;
    const badge = tab.querySelector('span');
    if (badge) badge.textContent = count;
  });
}

function updateQueueSummary() {
  const open = conflicts.filter(item => !item.reviewed).length;
  const reviewed = conflicts.length - open;
  document.getElementById('open-conflicts-count').textContent = open;
  document.getElementById('reconciliation-count').textContent = open;
  document.getElementById('attention-count').textContent = open;
  const summaryValues = document.querySelectorAll('.queue-summary > div:not(.queue-rule) strong');
  summaryValues[1].textContent = reviewed;
  summaryValues[2].textContent = `${conflicts.length ? Math.round((reviewed / conflicts.length) * 100) : 100}%`;
  document.getElementById('strip-conflict-count').textContent = open;
}

function showToast(message) { toast.textContent = message; toast.classList.add('show'); window.clearTimeout(showToast.timer); showToast.timer = window.setTimeout(() => toast.classList.remove('show'), 2600); }

function renderPatientFlow(filter = flowFilter) {
  flowFilter = filter;
  const visible = patientEvents.filter(event => filter === 'all' || (filter === 'admissions' ? event.admission_at : event.discharge_at));
  document.getElementById('event-list').innerHTML = visible.slice(0, 60).map(event => `<div class="event-row"><div><strong>${event.patient_id}</strong><small>Department: ${event.department || 'Not recorded'}</small></div><span>${event.admission_at ? event.admission_at.replace('T', ' ').slice(0, 16) : '—'}</span><span>${event.discharge_at ? event.discharge_at.replace('T', ' ').slice(0, 16) : '—'}</span><span>${event.ward_normalized || 'Unassigned'}</span><span class="event-status ${event.discharge_at ? 'complete' : 'current'}">${event.discharge_at ? 'Discharged' : 'In care'}</span></div>`).join('');
  const barGroups = document.querySelectorAll('.chart-bars .bar-group');
  const hours = Array.from(barGroups, group => Number(group.querySelector('span').textContent));
  const admissions = hours.map(hour => patientEvents.filter(event => event.admission_at && Number(event.admission_at.slice(11, 13)) === hour).length);
  const discharges = hours.map(hour => patientEvents.filter(event => event.discharge_at && Number(event.discharge_at.slice(11, 13)) === hour).length);
  const maximum = Math.max(1, ...admissions, ...discharges);
  barGroups.forEach((group, index) => {
    group.querySelector('.bar.admit').style.height = `${(admissions[index] / maximum) * 100}%`;
    group.querySelector('.bar.discharge').style.height = `${(discharges[index] / maximum) * 100}%`;
  });
  const insight = document.getElementById('flow-insight');
  if (insight) insight.textContent = `${admissions.reduce((sum, value) => sum + value, 0)} admissions and ${discharges.reduce((sum, value) => sum + value, 0)} discharges in the selected reporting window.`;
}

async function loadPatientFlow() {
  try {
    const date = document.getElementById('flow-date').value;
    const response = await fetch(`/api/patient-flow?date=${encodeURIComponent(date)}`);
    if (!response.ok) throw new Error('Patient flow request failed');
    const data = await response.json();
    patientEvents = data.events;
    document.getElementById('flow-admissions').textContent = data.admissions;
    document.getElementById('flow-discharges').textContent = data.discharges;
    document.getElementById('flow-in-care').textContent = data.inCare;
    renderPatientFlow(flowFilter);
  } catch (error) {
    console.info('Patient flow API unavailable.', error);
  }
}

async function loadBottlenecks() {
  try {
    const response = await fetch('/api/bottlenecks');
    if (!response.ok) throw new Error('Bottleneck request failed');
    const data = await response.json();
    document.getElementById('bottleneck-date').textContent = `Reporting date: ${formatDate(data.reportingDate)}`;
    document.getElementById('bottleneck-note').textContent = data.bedAssignmentNote;
    document.getElementById('bottleneck-list').innerHTML = data.departments.map(item => `
      <div class="bottleneck-row">
        <div><strong>${item.department}</strong><small>${item.inCare} currently in care</small></div>
        <div><strong>${item.admissions} in / ${item.discharges} out</strong><small>${item.netFlow >= 0 ? '+' : ''}${item.netFlow} net movement</small></div>
        <div><strong>${item.labAverage}</strong><small>${item.labPending} pending of ${item.labOrders} orders</small></div>
        <span class="bottleneck-status ${item.severity.toLowerCase()}">${item.severity}</span>
      </div>`).join('');
  } catch (error) {
    console.info('Bottleneck API unavailable.', error);
  }
}

async function loadTrustScores() {
  try {
    const response = await fetch('/api/trust-score');
    if (!response.ok) throw new Error('Trust score request failed');
    const data = await response.json();
    document.getElementById('trust-method-text').textContent = data.method;
    document.getElementById('trust-list').innerHTML = data.metrics.map(metric => `
      <article class="trust-card ${metric.rating.toLowerCase()}">
        <div class="trust-card-head"><div><div class="eyebrow">${metric.source}</div><h2>${metric.name}</h2></div><div class="trust-score"><strong>${metric.score}%</strong><span>${metric.rating} TRUST</span></div></div>
        <div class="trust-value">${metric.value}</div>
        <div class="trust-components">${Object.entries(metric.components).map(([name, value]) => `<div><span>${name === 'sourceReliability' ? 'Source reliability' : name}</span><strong>${value}%</strong><i><b style="width:${value}%"></b></i></div>`).join('')}</div>
        <p class="trust-explanation">${metric.explanation}</p>
      </article>`).join('');
  } catch (error) {
    console.info('Trust score API unavailable.', error);
  }
}

function updateSimulation() {
  const discharges = Number(document.getElementById('discharges-value').value || document.getElementById('discharges-value').textContent);
  const emergency = Number(document.getElementById('emergency-value').value || document.getElementById('emergency-value').textContent);
  const icu = Number(document.getElementById('icu-range').value);
  const capacity = Number(currentDashboard.occupiedBeds.capacity || 0);
  const available = Number(currentDashboard.occupiedBeds.available || 0) + discharges - emergency;
  const occupancy = capacity ? Math.round(((capacity - available) / capacity) * 100) : 0;
  const badge = document.getElementById('scenario-badge');
  const message = document.getElementById('scenario-message');
  document.getElementById('projected-beds').textContent = available;
  document.getElementById('net-movement').textContent = `${discharges - emergency >= 0 ? '+' : '−'}${Math.abs(discharges - emergency)} beds`;
  document.getElementById('projected-occupancy').textContent = `${occupancy}%`;
  document.getElementById('icu-status').textContent = icu >= 100 ? 'At capacity' : `${icu}% targeted`;
  document.getElementById('outcome-meter-fill').style.width = `${Math.max(0, Math.min(100, occupancy))}%`;
  const danger = available < 5 || icu >= 100 && emergency > 5;
  const good = available >= 30 && icu < 100;
  badge.className = `scenario-badge ${danger ? 'danger' : good ? 'good' : 'caution'}`;
  badge.textContent = danger ? 'CAPACITY EXCEEDED' : good ? 'ACCEPT' : 'ACCEPT WITH CONDITIONS';
  message.className = `scenario-message ${danger ? 'danger-message' : ''}`;
  message.querySelector('strong').textContent = danger ? 'Escalate before accepting new arrivals.' : good ? 'Capacity remains comfortable.' : 'Emergency intake is possible, but reserve is thin.';
  message.querySelector('p').textContent = danger ? 'Protect remaining beds and route additional demand to the escalation team.' : good ? 'The projected reserve gives the team room to absorb normal variation.' : 'Keep 5 beds protected for unscheduled demand and confirm the discharge list before noon.';
}

document.querySelectorAll('.step-btn').forEach(button => button.addEventListener('click', () => {
  const output = document.getElementById(`${button.dataset.stepTarget}-value`);
  output.textContent = Math.max(0, Math.min(30, Number(output.textContent) + Number(button.dataset.step)));
  updateSimulation();
}));
document.getElementById('icu-range').addEventListener('input', event => { document.getElementById('icu-value').textContent = `${event.target.value}%`; updateSimulation(); });
document.getElementById('run-simulation').addEventListener('click', () => { updateSimulation(); showToast('Scenario calculated from the latest bed snapshot.'); });
document.getElementById('reset-scenario').addEventListener('click', () => { document.getElementById('discharges-value').textContent = '5'; document.getElementById('emergency-value').textContent = '8'; document.getElementById('icu-range').value = '100'; document.getElementById('icu-value').textContent = '100%'; updateSimulation(); showToast('Scenario reset to the current planning example.'); });

async function loadApiData() {
  if (liveRefreshInProgress) return false;
  liveRefreshInProgress = true;
  try {
    const [conflictResponse, bedResponse, dashboardResponse, sourceResponse] = await Promise.all([fetch('/api/conflicts'), fetch('/api/bed-types'), fetch('/api/dashboard'), fetch('/api/sources')]);
    if (!conflictResponse.ok || !bedResponse.ok || !dashboardResponse.ok || !sourceResponse.ok) throw new Error('API data request failed');
    const conflictData = await conflictResponse.json();
    const bedData = await bedResponse.json();
    const dashboardData = await dashboardResponse.json();
    const sourceData = await sourceResponse.json();
    currentDashboard = dashboardData;
    conflicts = conflictData.items.map(item => ({ ...item, id: item.id }));
    bedTypes = bedData.categories;
    document.querySelectorAll('.source-card').forEach((card, index) => {
      const source = sourceData.sources[index];
      if (!source) return;
      card.querySelector('.source-stats strong').textContent = source.rows.toLocaleString();
      card.querySelectorAll('.source-stats strong')[1].textContent = source.freshness;
      card.querySelector('.source-footer').firstChild.textContent = source.completeness;
    });
    document.getElementById('attention-count').textContent = conflictData.openCount;
    document.getElementById('reconciliation-count').textContent = conflictData.openCount;
    document.getElementById('open-conflicts-count').textContent = conflictData.openCount;
    document.getElementById('all-conflicts-tab-count').textContent = conflictData.items.length;
    document.getElementById('occupied-beds-value').innerHTML = `${dashboardData.occupiedBeds.value} <span>/ ${dashboardData.occupiedBeds.capacity}</span>`;
    document.getElementById('occupied-beds-rate').textContent = `${Math.round((dashboardData.occupiedBeds.value / dashboardData.occupiedBeds.capacity) * 100)}% occupancy`;
    document.getElementById('occupancy-total-value').textContent = dashboardData.occupiedBeds.available;
    document.getElementById('admissions-value').textContent = dashboardData.admissionsToday.value;
    document.getElementById('lab-turnaround-value').textContent = dashboardData.labTurnaround.value;
    document.getElementById('lab-pending-value').textContent = `${dashboardData.labTurnaround.pending} pending`;
    document.getElementById('discharges-recorded-value').textContent = dashboardData.dischargesDue.value;
    document.querySelector('#occupied-beds-value').closest('.metric-card').querySelector('.confidence').textContent = `${dashboardData.trustScores['Bed availability']}% TRUST`;
    document.querySelector('#admissions-value').closest('.metric-card').querySelector('.confidence').textContent = `${dashboardData.trustScores.Admissions}% TRUST`;
    document.querySelector('#lab-turnaround-value').closest('.metric-card').querySelector('.confidence').textContent = `${dashboardData.trustScores['Lab turnaround']}% TRUST`;
    document.querySelector('#discharges-recorded-value').closest('.metric-card').querySelector('.confidence').textContent = `${dashboardData.trustScores.Discharges}% TRUST`;
    document.getElementById('alert-summary').textContent = dashboardData.alerts.length ? dashboardData.alerts.map(alert => `${alert.title}: ${alert.detail}`).join(' ') : 'No active operational alerts.';
    document.getElementById('overview-date').textContent = formatDate(dashboardData.reportingDate);
    document.querySelector('.source-date strong').textContent = formatDate(dashboardData.reportingDate);
    document.querySelector('.scenario-status strong').textContent = `Checked ${formatLiveTime(new Date())} · snapshot ${formatDate(dashboardData.reportingDate)}`;
    document.getElementById('flow-date').value = dashboardData.reportingDate;
    renderQueue();
    renderOverviewReview();
    renderBedMap();
    renderUnitList();
    updateSimulation();
    updateLiveClock();
    return true;
  } catch (error) {
    document.getElementById('live-status').textContent = 'Data connection unavailable';
    console.info('Using embedded prototype data until the Flask API is running.', error);
    return false;
  } finally {
    liveRefreshInProgress = false;
  }
}

function formatDate(value) {
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' });
}

function formatLiveDate(value) {
  return new Date(value).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' });
}

function formatLiveTime(value) {
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function updateLiveClock() {
  const now = new Date();
  const timeText = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const dateText = now.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' });

  const liveStatus = document.getElementById('live-status');
  const pipelineChecked = document.getElementById('pipeline-checked');
  const latestSourceTime = document.getElementById('latest-source-time');
  const liveDate = document.getElementById('live-date');
  const overviewDate = document.getElementById('overview-date');
  const sourceDate = document.querySelector('.source-date strong');

  if (liveStatus) liveStatus.textContent = `Live · checked ${timeText}`;
  if (pipelineChecked) pipelineChecked.textContent = `Checked ${timeText}`;
  if (latestSourceTime) latestSourceTime.textContent = `Checked ${timeText} · ${dateText}`;
  if (liveDate) liveDate.textContent = dateText;
  if (overviewDate) overviewDate.textContent = dateText;
  if (sourceDate) sourceDate.textContent = dateText;
}

function exportBrief() {
  const rows = [
    ['Metric', 'Value'],
    ['Reporting date', currentDashboard.reportingDate],
    ['Occupied beds', `${currentDashboard.occupiedBeds.value}/${currentDashboard.occupiedBeds.capacity}`],
    ['Available beds', currentDashboard.occupiedBeds.available],
    ['Admissions', currentDashboard.admissionsToday.value],
    ['Discharges', currentDashboard.dischargesDue.value],
    ['Lab turnaround', currentDashboard.labTurnaround.value],
    ['Open conflicts', conflicts.filter(item => !item.reviewed).length],
  ];
  const csv = rows.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(',')).join('\n');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  link.download = `medigrid-brief-${currentDashboard.reportingDate}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

function renderBedMap() {
  const map = document.querySelector('.bed-map');
  map.innerHTML = bedTypes.map(type => {
    const cells = Array.from({ length: type.total }, (_, index) => `<i class="bed-cell ${index >= type.total - type.vacant ? 'vacant' : ''}" title="${type.name} bed ${index + 1}: ${index >= type.total - type.vacant ? 'Vacant' : 'Full'}"></i>`).join('');
    return `<div class="bed-map-row"><div class="vacancy-label"><span class="vacancy-swatch ${type.className}"></span><div><strong>${type.name}</strong><small>${type.total} total beds</small></div></div><div class="bed-cells">${cells}</div><strong class="vacancy-count">${type.vacant} free</strong></div>`;
  }).join('');
  map.hidden = false;
  document.querySelector('.vacancy-chart').hidden = true;
}

function renderUnitList() {
  document.querySelector('.unit-list').innerHTML = bedTypes.map(type => {
    const occupied = type.total - type.vacant;
    const rate = type.total ? Math.round((occupied / type.total) * 100) : 0;
    return `<div class="unit-row-data"><div class="unit-name"><span class="unit-color ${type.className}"></span><div><strong>${type.name}</strong><small>${occupied} / ${type.total} beds</small></div></div><div class="unit-meter"><div><span style="width:${rate}%"></span></div><strong>${rate}%</strong></div></div>`;
  }).join('');
}

document.querySelectorAll('[data-bed-view]').forEach(button => button.addEventListener('click', () => {
  const mapView = button.dataset.bedView === 'map';
  document.querySelectorAll('[data-bed-view]').forEach(item => item.classList.toggle('active', item === button));
  document.querySelector('.vacancy-chart').hidden = mapView;
  document.querySelector('.bed-map').hidden = !mapView;
}));

navItems.forEach(item => item.addEventListener('click', () => showView(item.dataset.view)));
document.querySelectorAll('[data-view-target]').forEach(button => button.addEventListener('click', () => showView(button.dataset.viewTarget)));
document.getElementById('refresh-btn').addEventListener('click', async () => { await loadApiData(); await loadPatientFlow(); showToast('Brief refreshed from latest available sources.'); });
document.getElementById('controls-refresh').addEventListener('click', async () => { await loadApiData(); showToast('Bed inventory refreshed from the latest occupancy snapshot.'); });
document.getElementById('flow-refresh').addEventListener('click', async () => { await loadPatientFlow(); showToast('Admissions and discharge events refreshed.'); });
document.getElementById('bottlenecks-refresh').addEventListener('click', async () => { await loadBottlenecks(); showToast('Flow bottlenecks refreshed.'); });
document.getElementById('trust-refresh').addEventListener('click', async () => { await loadTrustScores(); showToast('Operational trust scores refreshed.'); });
document.getElementById('export-btn').addEventListener('click', exportBrief);
document.getElementById('filter-btn').addEventListener('click', event => { queueOpenOnly = !queueOpenOnly; event.currentTarget.innerHTML = `${queueOpenOnly ? 'Filter: Open only' : 'Filter: All'} <span>⌄</span>`; renderQueue(); showToast(queueOpenOnly ? 'Showing open conflicts only.' : 'Showing all conflicts.'); });
document.getElementById('flow-date').addEventListener('change', loadPatientFlow);

document.querySelectorAll('.queue-tab').forEach(tab => tab.addEventListener('click', () => {
  document.querySelectorAll('.queue-tab').forEach(item => item.classList.remove('active'));
  tab.classList.add('active');
  document.querySelectorAll('.queue-item').forEach(item => {
    const conflict = conflicts[Number(item.dataset.index)];
    item.style.display = conflictMatchesTab(conflict, tab.textContent) ? 'grid' : 'none';
  });
}));

document.querySelectorAll('[data-event-filter]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-event-filter]').forEach(item => item.classList.toggle('active', item === button));
  renderPatientFlow(button.dataset.eventFilter);
}));

renderQueue();
renderBedMap();
updateLiveClock();
loadApiData();
loadPatientFlow();
loadBottlenecks();
loadTrustScores();
updateSimulation();
window.setInterval(updateLiveClock, 1000);
window.setInterval(async () => {
  if (document.hidden) return;
  const loaded = await loadApiData();
  if (loaded) await loadPatientFlow();
}, 30000);

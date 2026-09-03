/* =========================================================
   NIDHI — analysis view
   Renders the /generate SSE stream section by section as it
   arrives, so results appear progressively.
   ========================================================= */

const analysis = {
  running: false,
  currency: 'INR',
  scorecards: [],
  lastRunId: null,
};

const STAGES = ['retrieval', 'extraction', 'scoring', 'optimization', 'ngo_matching'];
const STAGE_LABELS = {
  retrieval: 'Retrieving evidence',
  extraction: 'Extracting candidates',
  scoring: 'Scoring (deterministic)',
  optimization: 'Optimising portfolio',
  ngo_matching: 'Matching partners',
};

function el(id) { return document.getElementById(id); }

/** Escape untrusted text before putting it in innerHTML. */
function esc(text) {
  return String(text ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function meterClass(score) {
  if (score >= 70) return '';
  if (score >= 45) return 'mid';
  return 'low';
}

function stars(rating) {
  const full = Math.floor(rating);
  const half = rating % 1 >= 0.5 ? 1 : 0;
  return '★'.repeat(full) + (half ? '☆' : '') + '·'.repeat(Math.max(0, 5 - full - half));
}

/* ---------------------------------------------------------
   Stage progress
   --------------------------------------------------------- */
function resetAnalysisView() {
  analysis.scorecards = [];
  el('an-empty').style.display = 'none';
  el('an-results').style.display = 'none';
  el('an-progress').style.display = 'block';
  el('an-spinner').classList.remove('done');
  el('an-progress-title').textContent = 'Running analysis…';
  el('an-source-pill').style.display = 'none';
  el('an-warnings').innerHTML = '';
  ['an-kpis', 'an-fundsplit', 'an-scorecards', 'an-ngos',
   'an-comparison', 'an-insights', 'an-flags', 'an-runid']
    .forEach(id => { el(id).innerHTML = ''; });

  el('an-stages').innerHTML = STAGES.map(s =>
    `<span class="an-stage" id="an-stage-${s}">${esc(STAGE_LABELS[s])}</span>`
  ).join('');
}

function onProgress(event) {
  const node = el(`an-stage-${event.step}`);
  if (node) {
    node.classList.remove('running', 'done');
    node.classList.add(event.status === 'done' ? 'done' : 'running');
  }
  if (event.source) {
    const pill = el('an-source-pill');
    pill.style.display = 'inline-block';
    pill.textContent = event.source === 'TAVILY_PRIMARY'
      ? 'Live web search' : 'Knowledge base';
    pill.title = event.source;
  }
}

function onWarning(event) {
  const box = el('an-warnings');
  const div = document.createElement('div');
  div.className = 'an-warning';
  div.textContent = event.content?.message || 'Pipeline warning';
  box.appendChild(div);
}

/* ---------------------------------------------------------
   Overview
   --------------------------------------------------------- */
function renderOverview(c) {
  analysis.currency = c.currency || 'INR';
  el('an-results').style.display = 'block';

  const cards = [
    ['Total Budget', formatMoney(c.total_budget, c.currency), 'kpi-card-1'],
    ['Recommended', `${c.projects_recommended} of ${c.projects_evaluated}`, 'kpi-card-2'],
    ['Allocated', `${formatMoney(c.total_allocated, c.currency)}`, 'kpi-card-3'],
    ['Avg. Confidence', `${Math.round((c.average_confidence || 0) * 100)}%`, 'kpi-card-4'],
  ];
  el('an-kpis').innerHTML = cards.map(([label, value, cls]) => `
    <div class="kpi-card ${cls}">
      <div class="kpi-label">${esc(label)}</div>
      <div class="kpi-value">${esc(value)}</div>
    </div>`).join('');

  const evidence = Object.entries(c.evidence_summary || {})
    .map(([k, v]) => `${v} ${k.toLowerCase().replace(/_/g, ' ')}`).join(', ');
  el('an-runid').innerHTML =
    `Run ${esc(c.run_id)} &middot; ${esc(c.ngos_considered)} organisations considered &middot; ` +
    `evidence: ${esc(evidence)}`;
}

/* ---------------------------------------------------------
   Fund split
   --------------------------------------------------------- */
function renderFundSplit(c) {
  const cur = c.currency || analysis.currency;
  const max = Math.max(...c.allocations.map(a => a.allocated_amount), 1);

  const rows = c.allocations.map(a => `
    <div class="an-alloc">
      <div>
        <div class="an-alloc-name">${esc(a.project_name)}</div>
        <div class="an-alloc-meta">${esc(a.region)} &middot;
          ${esc(String(a.category).replace(/_/g, ' '))} &middot;
          score ${a.final_score.toFixed(1)} &middot;
          confidence ${Math.round(a.confidence * 100)}%</div>
      </div>
      <div>
        <div class="an-alloc-amt">${esc(formatMoney(a.allocated_amount, cur))}</div>
        <div class="an-alloc-pct">${a.percentage_of_budget.toFixed(1)}% of budget</div>
      </div>
      <div class="an-bar"><span style="width:${(a.allocated_amount / max * 100).toFixed(1)}%"></span></div>
    </div>`).join('');

  const report = c.constraint_report || {};
  const relaxed = (report.relaxations_applied || []).length ? `
    <div class="an-note"><strong>Constraints relaxed to reach this result:</strong>
      ${esc(report.relaxations_applied.join('; '))}.
      ${esc((report.notes || []).join(' '))}</div>` : '';

  const excluded = (c.excluded || []).length ? `
    <div class="an-excluded">
      <h4>Not recommended (${c.excluded.length})</h4>
      ${c.excluded.map(e => `
        <div class="an-ex-row">
          <strong>${esc(e.project_name)}</strong> (score ${e.final_score.toFixed(1)}) &mdash;
          ${esc(e.reason)}.<br>${esc(e.what_would_change_it)}
        </div>`).join('')}
    </div>` : '';

  el('an-fundsplit').innerHTML = `
    <div class="an-card">
      ${rows || '<p class="an-alloc-meta">No projects met the criteria for a funding recommendation.</p>'}
      ${relaxed}
      ${excluded}
    </div>`;
}

/* ---------------------------------------------------------
   Scorecards — one frame per project, appended as it arrives
   --------------------------------------------------------- */
function renderScorecard(index, c) {
  const cur = analysis.currency;
  const meters = (c.dimensions || []).map(d => `
    <div class="an-meter">
      <span class="an-meter-label">${esc(d.name.replace(/_/g, ' '))}</span>
      <span class="an-meter-track">
        <span class="an-meter-fill ${meterClass(d.score)}" style="width:${d.score}%"></span>
      </span>
      <span class="an-meter-val">${d.score.toFixed(0)} &middot; ${d.weight}%</span>
    </div>`).join('');

  const unknowns = (c.unknown_fields || []).length ? `
    <div class="an-unknowns">
      <strong>No evidence found for:</strong>
      ${esc(c.unknown_fields.join(', '))}.
      These are unknowns, not negative findings &mdash; they lower confidence
      rather than the score, and should be confirmed before any decision.
    </div>` : '';

  const card = document.createElement('div');
  card.className = 'an-card';
  card.innerHTML = `
    <div class="an-score-head">
      <div>
        <div class="an-score-title">${esc(c.project_name)}</div>
        <div class="an-score-sub">${esc(c.region)} &middot;
          ${esc(String(c.category).replace(/_/g, ' '))}
          ${c.selected ? `&middot; ${esc(formatMoney(c.allocated_amount, cur))} suggested` : ''}</div>
        <span class="an-rec ${esc(c.recommendation)}">${esc(c.recommendation)}</span>
      </div>
      <div style="text-align:right;">
        <div class="an-score-big" style="color:${
          c.final_score > 75 ? 'var(--green-600)'
          : c.final_score >= 60 ? 'var(--amber-600)' : 'var(--gray-400)'}">
          ${c.final_score.toFixed(1)}<small>/100</small>
        </div>
      </div>
    </div>

    <div class="an-conf">
      <div class="an-conf-item"><strong>${Math.round(c.confidence * 100)}%</strong>confidence in this score</div>
      <div class="an-conf-item"><strong>${c.completeness.toFixed(0)}%</strong>proposal completeness</div>
      <div class="an-conf-item"><strong>${(c.estimated_beneficiaries || 0).toLocaleString('en-IN')}</strong>estimated beneficiaries</div>
    </div>

    <div class="an-meters">${meters}</div>
    ${c.explanation ? `<div class="an-prose">${esc(c.explanation)}</div>` : ''}
    ${unknowns}`;

  el('an-scorecards').appendChild(card);
}

/* ---------------------------------------------------------
   Partner recommendations
   --------------------------------------------------------- */
function renderNGOs(c) {
  const blocks = (c.recommendations || []).map(rec => {
    if (!rec.matches.length) {
      return `<div class="an-card">
        <div class="an-score-title">${esc(rec.project_name)}</div>
        <p class="an-alloc-meta" style="margin-top:6px;">
          No organisation passed the mandatory eligibility filters
          (${rec.ineligible_count} assessed). Relax a requirement or widen the
          target geography, then run again.</p>
      </div>`;
    }
    const matches = rec.matches.map(m => `
      <div class="an-ngo">
        <div>
          <div class="an-ngo-name">${esc(m.ngo_name)}
            ${m.temenos_partner ? '<span class="an-pill">EXISTING PARTNER</span>' : ''}
          </div>
          <div class="an-alloc-meta">${esc(m.evidence_source)} &middot;
            compatibility ${m.compatibility_score.toFixed(0)}/100</div>
        </div>
        <div style="text-align:right;">
          <div class="an-stars">${stars(m.star_rating)}</div>
          <div class="an-alloc-pct">${m.star_rating} / 5</div>
        </div>
        <div style="grid-column:1/-1; margin-top:6px;">
          ${m.strengths.map(s => `<div class="an-plus">+ ${esc(s)}</div>`).join('')}
          ${m.limitations.map(l => `<div class="an-minus">&minus; ${esc(l)}</div>`).join('')}
        </div>
      </div>`).join('');

    return `<div class="an-card">
      <div class="an-score-title" style="margin-bottom:4px;">${esc(rec.project_name)}</div>
      <div class="an-alloc-meta" style="margin-bottom:8px;">
        ${rec.ineligible_count} organisation(s) ruled out by mandatory filters</div>
      ${matches}
    </div>`;
  }).join('');

  el('an-ngos').innerHTML = blocks ||
    '<div class="an-card"><p class="an-alloc-meta">No partner matches to show.</p></div>';
}

function renderComparison(c) {
  if (!c.rows || !c.rows.length) {
    el('an-comparison').innerHTML =
      '<div class="an-card"><p class="an-alloc-meta">No partners to compare.</p></div>';
    return;
  }
  const dims = c.dimensions || [];
  el('an-comparison').innerHTML = `
    <div class="an-card">
      <table class="an-table">
        <thead><tr>
          <th>Project</th><th>Rank</th><th>Partner</th><th>Score</th><th>Stars</th>
          ${dims.map(d => `<th>${esc(d.replace(/_/g, ' '))} (${c.weights[d]}%)</th>`).join('')}
          <th>Key limitation</th>
        </tr></thead>
        <tbody>
          ${c.rows.map(r => `<tr>
            <td>${esc(r.project_name)}</td>
            <td>#${r.rank}</td>
            <td><strong>${esc(r.ngo_name)}</strong>
              ${r.temenos_partner ? '<span class="an-pill">PARTNER</span>' : ''}</td>
            <td>${r.compatibility_score.toFixed(0)}</td>
            <td class="an-stars">${stars(r.star_rating)}</td>
            ${dims.map(d => `<td>${r['score_' + d] !== undefined
              ? Number(r['score_' + d]).toFixed(0) : '&mdash;'}</td>`).join('')}
            <td>${esc(r.key_limitation || '—')}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

/* ---------------------------------------------------------
   Strategic insights and review flags
   --------------------------------------------------------- */
function renderInsights(c) {
  // The narrative arrives as markdown-ish bullets; render them as a list.
  const bullets = String(c.narrative || '')
    .split('\n')
    .map(line => line.replace(/^[-*\u2022]\s*/, '').trim())
    .filter(Boolean);

  const distribution = (label, rows) => !rows || !rows.length ? '' : `
    <div style="margin-top:16px;">
      <div class="an-alloc-meta" style="margin-bottom:6px;"><strong>${esc(label)}</strong></div>
      ${rows.map(r => `
        <div class="an-meter" style="grid-template-columns:150px 1fr 92px;">
          <span class="an-meter-label">${esc(String(r.name).replace(/_/g, ' '))}</span>
          <span class="an-meter-track">
            <span class="an-meter-fill" style="width:${Math.min(r.percentage, 100)}%"></span>
          </span>
          <span class="an-meter-val">${r.percentage.toFixed(1)}%</span>
        </div>`).join('')}
    </div>`;

  el('an-insights').innerHTML = `
    <div class="an-card">
      <ul style="margin:0; padding-left:18px;">
        ${bullets.map(b => `<li style="font-size:13.5px; line-height:1.7;
          color:var(--gray-600); margin-bottom:8px;">${esc(b)}</li>`).join('')}
      </ul>
      ${distribution('Regional distribution', c.regional_distribution)}
      ${distribution('Sector distribution', c.sector_distribution)}
    </div>`;
}

function renderFlags(c) {
  const flags = c.flags || [];
  if (!flags.length) {
    el('an-flags').innerHTML =
      '<div class="an-card"><p class="an-alloc-meta">Nothing flagged for review.</p></div>';
    return;
  }
  el('an-flags').innerHTML = `
    <div class="an-card">
      ${flags.map(f => `
        <div class="an-flag">
          <span class="an-flag-sev ${esc(f.severity)}">${esc(f.severity)}</span>
          <div>
            <div class="an-flag-cat">${esc(f.category)} &mdash; ${esc(f.subject)}</div>
            <div class="an-flag-detail">${esc(f.detail)}</div>
            ${f.suggested_action
              ? `<div class="an-flag-action">&rarr; ${esc(f.suggested_action)}</div>` : ''}
          </div>
        </div>`).join('')}
    </div>`;
}

/* ---------------------------------------------------------
   Driver
   --------------------------------------------------------- */
async function runAnalysis(payload) {
  if (analysis.running) return;
  analysis.running = true;

  showWorkspaceView('ws-analysis');
  resetAnalysisView();

  try {
    await api.generate(payload, (section, event) => {
      switch (section) {
        case 'progress':            onProgress(event); break;
        case 'warning':             onWarning(event); break;
        case 'overview':            renderOverview(event.content); break;
        case 'scorecard':           renderScorecard(event.project_index, event.content); break;
        case 'fund_split':          renderFundSplit(event.content); break;
        case 'ngo_recommendations': renderNGOs(event.content); break;
        case 'ngo_comparison':      renderComparison(event.content); break;
        case 'strategic_insights':  renderInsights(event.content); break;
        case 'human_review_flags':  renderFlags(event.content); break;
        case 'complete':
          analysis.lastRunId = event.run_id;
          el('an-spinner').classList.add('done');
          el('an-progress-title').textContent = 'Analysis complete';
          break;
        default: break;
      }
    });
  } catch (err) {
    el('an-spinner').classList.add('done');
    el('an-progress-title').textContent = 'Analysis failed';
    onWarning({ content: { message: err.message } });
    el('an-empty').style.display = 'none';
  } finally {
    analysis.running = false;
    STAGES.forEach(s => el(`an-stage-${s}`)?.classList.remove('running'));
  }
}

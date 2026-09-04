/* =========================================================
   NIDHI — company data loader
   Replaces the demo dataset with the real Temenos CSR record
   from the backend once the user signs in.
   ========================================================= */

const CATEGORY_TAGS = {
  education: 'tag-education',
  healthcare: 'tag-healthcare',
  environment: 'tag-environment',
  water_sanitation: 'tag-water',
  women_livelihood: 'tag-gender',
  skill_development: 'tag-skill',
  youth_development: 'tag-education',
  poverty_alleviation: 'tag-skill',
  financial_inclusion: 'tag-skill',
};

const CATEGORY_IMAGES = {
  education: 'photo-1509062522246-3755977927d7',
  healthcare: 'photo-1576091160399-112ba8d25d1d',
  water_sanitation: 'photo-1541888946425-d0fbb186a5b7',
  environment: 'photo-1542601906990-b4d3fb778b09',
  skill_development: 'photo-1531482615713-2afd69097998',
  women_livelihood: 'photo-1573496359142-b8d87734a5a2',
};

function imageFor(category) {
  const id = CATEGORY_IMAGES[category] || CATEGORY_IMAGES.education;
  return `https://images.unsplash.com/${id}?auto=format&fit=crop&w=600&q=80`;
}

/** Map one backend historical project onto the card shape app.js renders. */
function toCard(project, index) {
  const category = project.canonical_category || 'education';
  const year = project.year || '';
  const ongoing = /ongoing|present|long-term/i.test(year + ' ' + (project.duration || ''));

  return {
    id: project.project_id || `hist-${index}`,
    title: project.project_name,
    desc: project.expected_impact || project.problem || 'No public description available.',
    category: project.csr_category || 'CSR',
    categoryClass: CATEGORY_TAGS[category] || 'tag-education',
    status: ongoing ? 'In Progress' : 'Completed',
    statusClass: ongoing ? 'status-in-progress' : 'status-completed',
    location: [project.region, project.country].filter(Boolean).join(', ') || 'Not disclosed',
    // The knowledge base leaves most budgets undisclosed. Say so rather than
    // inventing a figure.
    budget: project.budget || 'Not disclosed',
    budgetValue: 0,
    duration: project.duration || year || 'Not disclosed',
    img: imageFor(category),
    // Historical projects were never scored by NIDHI. Showing a number here
    // would be fabrication, so the card renders a dash instead.
    impactScore: null,
    impactLevel: project.actual_impact ? 'Reported' : 'Not scored',
    addedDate: year || '—',
    beneficiaries: project.beneficiaries || 'Not disclosed',
  };
}

/** Load the signed-in company's real record and refresh every view. */
async function loadCompanyData(companyId) {
  try {
    const profile = await api.company(companyId || 'temenos');
    state.companyProfile = profile;

    const projects = (profile.historical_projects || []).map(toCard);
    if (projects.length) state.projects = projects;

    // Recommendations are produced by a run, not by the profile. Until the
    // user runs one, point them at the wizard instead of showing stale cards.
    state.recommendations = [];

    const banner = document.getElementById('ws-load-error');
    if (banner) banner.style.display = 'none';
    updateOverviewKpis(profile);
    if (typeof renderProjectsGrid === 'function') renderProjectsGrid();
    if (typeof renderRecommendations === 'function') renderRecommendations();
    if (typeof renderSavedProjectsTable === 'function') renderSavedProjectsTable();
  } catch (err) {
    // Failing silently here leaves the dashboard showing whatever markup was
    // already on screen, which a user reasonably reads as real figures.
    console.warn('NIDHI: could not load company data —', err.message);
    showLoadError(
      err.message === 'Failed to fetch'
        ? 'Cannot reach the NIDHI backend, so no company data is shown. ' +
          'Start it with: uvicorn main:app --port 8000'
        : `Could not load company data: ${err.message}`);
    ['0', '1', '2', '3'].forEach((_, i) => setKpi(i, '—'));
  }

  try {
    const runs = await api.history(20);
    state.runs = runs;
    updateHistoryKpi(runs);
  } catch { /* history is optional context */ }
}

function showLoadError(message) {
  const banner = document.getElementById('ws-load-error');
  if (!banner) return;
  banner.textContent = message;
  banner.style.display = 'block';
}


function setKpi(index, value, label) {
  const cards = document.querySelectorAll('#ws-overview .kpi-card');
  const card = cards[index];
  if (!card) return;
  const valueEl = card.querySelector('.kpi-value');
  const labelEl = card.querySelector('.kpi-label');
  if (valueEl) valueEl.textContent = value;
  if (labelEl && label) labelEl.textContent = label;
}

function updateOverviewKpis(profile) {
  const projects = profile.historical_projects || [];
  const direct = projects.filter(p => p.is_direct_csr);
  setKpi(0, String(projects.length), 'Documented CSR Projects');
  setKpi(1, String(direct.length), 'Direct Community Investment');
  setKpi(2, String((profile.ngo_partners || []).length), 'Known Partners');
  setKpi(3, String((profile.priority_themes || []).length), 'Priority Themes');
}

function updateHistoryKpi(runs) {
  const banner = document.getElementById('ws-runs-note');
  if (!banner) return;
  banner.textContent = runs.length
    ? `${runs.length} previous analysis run(s) on record. Latest funded ` +
      `${runs[0].projects_funded} project(s).`
    : 'No analysis runs yet — use the wizard to generate your first recommendation.';
}

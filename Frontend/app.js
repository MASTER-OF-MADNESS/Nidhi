// NIDHI CSR Platform Application Logic

// Escaping for anything interpolated into innerHTML. Project and partner text
// can originate from live web search, so it is never trusted markup.
// analysis.js defines esc() first; this is the fallback if it is absent.
if (typeof esc !== 'function') {
  window.esc = function (text) {
    return String(text ?? '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
  };
}

// Initial Data Store
const state = {
  activeEntity: 'Company', // 'Company', 'NGO', 'Government'
  currentUser: {
    name: 'Temenos AG',
    code: 'TM',
    role: 'Company Admin'
  },
  wizardStep: 1,
  savedIds: [],
  compareIds: [],
  
  // Populated from the backend on sign-in (company.js).
  projects: [],

  // Produced by an analysis run, not preloaded.
  recommendations: [],
  companyProfile: null,
  runs: [],
};

// Global DOM Loaded Initializer
document.addEventListener('DOMContentLoaded', () => {
  initNavigation();
  renderProjectsGrid();
  renderRecommendations();
  renderSavedProjectsTable();
  initWizardEvents();
});

// Screen Switching Function
function showScreen(screenId) {
  document.querySelectorAll('.app-screen').forEach(screen => {
    screen.classList.remove('active');
  });
  const targetScreen = document.getElementById(screenId);
  if (targetScreen) {
    targetScreen.classList.add('active');
    window.scrollTo(0, 0);
  }
}

// Portal Selection (Company / NGO / Government)
function selectPortal(entityType) {
  state.activeEntity = entityType;
  
  // Update Portal Selector Card Highlights
  document.querySelectorAll('.portal-card').forEach(card => card.classList.remove('active'));
  const entityCard = document.getElementById(`portal-card-${entityType.toLowerCase()}`);
  if (entityCard) entityCard.classList.add('active');

  // Update Login Screen Title & Placeholders
  const loginTitle = document.getElementById('login-entity-title');
  const entityIdLabel = document.getElementById('entity-id-label');
  const entityIdInput = document.getElementById('username-input');
  const registerBtn = document.getElementById('register-entity-btn');

  if (loginTitle) loginTitle.innerHTML = `${entityType} <span>Login</span>`;
  if (entityIdLabel) entityIdLabel.textContent = entityType === 'Company' ? 'Username / Company ID' : `${entityType} ID / Email`;
  if (entityIdInput) entityIdInput.placeholder = entityType === 'Company' ? 'Enter username or company ID' : `Enter ${entityType.toLowerCase()} ID`;
  if (registerBtn) registerBtn.textContent = `Register Your ${entityType}`;

  // Navigate to Login Screen
  showScreen('screen-login');
}

// Toggle Password Visibility
function togglePasswordVisibility(inputId, iconId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
  } else {
    input.type = 'password';
  }
}

// Login Handler -- validates against the backend before entering the workspace.
async function loginUser(event) {
  if (event) event.preventDefault();

  const username = document.getElementById('username-input')?.value || '';
  const password = document.getElementById('password-input')?.value || '';
  const errorEl = document.getElementById('login-error');
  const submitBtn = document.getElementById('login-submit-btn');

  if (errorEl) errorEl.style.display = 'none';

  // Only the Company portal is backed by a real account today; the NGO and
  // Government portals remain UI previews.
  if (state.activeEntity === 'Company') {
    if (!username || !password) {
      return showLoginError('Enter your username and password.');
    }
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Signing in...'; }
    try {
      const result = await api.login(username, password);
      state.currentUser.name = result.company_name || 'Temenos AG';
      state.currentUser.companyId = result.company_id;
    } catch (err) {
      return showLoginError(
        err.message === 'Failed to fetch'
          ? 'Cannot reach the NIDHI backend. Start it with: uvicorn main:app --port 8000'
          : err.message);
    } finally {
      if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Login →'; }
    }
  }

  const avatarEl = document.getElementById('user-avatar-badge');
  const nameEl = document.getElementById('user-name-badge');
  if (avatarEl) avatarEl.textContent = state.activeEntity === 'Company' ? 'TM' : (state.activeEntity === 'NGO' ? 'NG' : 'GV');
  if (nameEl) nameEl.textContent = state.activeEntity === 'Company' ? state.currentUser.name : (state.activeEntity === 'NGO' ? 'SMILE Foundation' : 'Ministry of CSR');

  showScreen('screen-workspace');
  showWorkspaceView('ws-landing');

  // Swap the demo dataset for the signed-in company's real CSR record.
  if (state.activeEntity === 'Company') {
    loadCompanyData(state.currentUser.companyId || 'temenos');
  }
}

function showLoginError(message) {
  const errorEl = document.getElementById('login-error');
  if (errorEl) {
    errorEl.textContent = message;
    errorEl.style.display = 'block';
  } else {
    alert(message);
  }
  return false;
}

// Registration Handler
function openRegistration() {
  const regTitle = document.getElementById('reg-title');
  if (regTitle) regTitle.textContent = `${state.activeEntity} Registration`;
  showScreen('screen-register');
}

function submitRegistration(event) {
  if (event) event.preventDefault();
  alert(`${state.activeEntity} registration submitted successfully!`);
  loginUser();
}

// Navigation Handler for Workspace Views
function initNavigation() {
  document.querySelectorAll('.sidebar-menu-item').forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      const targetView = item.getAttribute('data-view');
      showWorkspaceView(targetView);
    });
  });
}

function showWorkspaceView(viewId) {
  document.querySelectorAll('.ws-view').forEach(view => {
    view.classList.remove('active');
  });
  
  document.querySelectorAll('.sidebar-menu-item').forEach(item => {
    item.classList.remove('active');
    if (item.getAttribute('data-view') === viewId) {
      item.classList.add('active');
    }
  });

  const targetView = document.getElementById(viewId);
  if (targetView) {
    targetView.classList.add('active');
    window.scrollTo(0, 0);
  }
}

// Logout Handler
function logoutUser() {
  showScreen('screen-portal-select');
}


/* =========================================================
   CSR LAUNCH WIZARD FUNCTIONS
   ========================================================= */
function initWizardEvents() {
  const descTextarea = document.getElementById('wiz-description');
  const charCounter = document.getElementById('char-counter');
  if (descTextarea && charCounter) {
    descTextarea.addEventListener('input', () => {
      charCounter.textContent = `${descTextarea.value.length}/1000`;
    });
  }
}

function goToWizardStep(step) {
  state.wizardStep = step;
  
  // Update Stepper UI
  for (let i = 1; i <= 5; i++) {
    const stepEl = document.getElementById(`step-item-${i}`);
    if (stepEl) {
      stepEl.classList.remove('active', 'completed');
      if (i < step) {
        stepEl.classList.add('completed');
      } else if (i === step) {
        stepEl.classList.add('active');
      }
    }

    const contentEl = document.getElementById(`wiz-step-content-${i}`);
    if (contentEl) {
      contentEl.classList.remove('active');
      if (i === step) contentEl.classList.add('active');
    }
  }

  // Update Stepper Line Progress
  const lineProgress = document.getElementById('stepper-line-progress');
  if (lineProgress) {
    const percentage = ((step - 1) / 4) * 100;
    lineProgress.style.width = `${percentage}%`;
  }

  // Dynamic Update for Step 5 Review
  if (step === 5) {
    const revBudget = document.getElementById('rev-budget');
    const revRegion = document.getElementById('rev-region');
    const revSector = document.getElementById('rev-sector');
    
    if (revBudget) revBudget.textContent = document.getElementById('wiz-budget')?.value || '₹25,00,000 - ₹50,00,000';
    if (revRegion) revRegion.textContent = `${selectedOptions('wiz-location').join(', ') || 'not selected'} (${document.getElementById('wiz-districts')?.value || 'all districts'})`;
    if (revSector) revSector.textContent = document.getElementById('wiz-cause')?.value || 'Education & Digital Literacy';
  }
}

function nextWizardStep() {
  if (state.wizardStep < 5) {
    goToWizardStep(state.wizardStep + 1);
  }
}

function prevWizardStep() {
  if (state.wizardStep > 1) {
    goToWizardStep(state.wizardStep - 1);
  }
}

function completeLaunchCSR() {
  const { payload, errors } = parseWizard();

  if (errors.length) {
    const lines = ['Please complete the following before running the analysis:', '']
      .concat(errors.map(function (e) { return '- ' + e; }));
    alert(lines.join(String.fromCharCode(10)));
    return;
  }

  // Hand off to the backend. Results stream into the AI Analysis view.
  // Nothing is 'launched' or approved here -- the output is a recommendation
  // for a human to decide on.
  runAnalysis(payload);
}



/* =========================================================
   ALL PROJECTS GRID RENDERER & FILTERS (Image 8)
   ========================================================= */
function renderProjectsGrid(filteredCategory = 'All') {
  const grid = document.getElementById('projects-grid-container');
  if (!grid) return;

  const displayList = filteredCategory === 'All' 
    ? state.projects 
    : state.projects.filter(p => p.category === filteredCategory);

  grid.innerHTML = displayList.map(p => `
    <div class="project-card" onclick="openProjectModal('${esc(p.id)}')">
      <div class="project-card-img-wrap">
        <img class="project-card-img" src="${esc(p.img)}" alt="${esc(p.title)}" />
      </div>
      <div class="project-card-body">
        <div class="project-tags">
          <span class="tag-pill ${esc(p.categoryClass)}">${esc(p.category)}</span>
          <span class="status-pill ${esc(p.statusClass)}">${esc(p.status)}</span>
        </div>
        <h3 class="project-card-title">${esc(p.title)}</h3>
        <p class="project-card-desc">${esc(p.desc)}</p>
        
        <div class="project-meta-list">
          <div class="project-meta-item">
            <svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
            <span>${esc(p.location)}</span>
          </div>
          <div class="project-meta-item">
            <svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            <span>${esc(p.budget)}</span>
          </div>
          <div class="project-meta-item">
            <svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
            <span>${esc(p.duration)}</span>
          </div>
        </div>

        <div class="project-card-footer">
          <svg class="arrow-icon" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
        </div>
      </div>
    </div>
  `).join('');
}


/* =========================================================
   RECOMMENDATIONS RENDERER (Image 9)
   ========================================================= */
function renderRecommendations() {
  const container = document.getElementById('recs-grid-container');
  if (!container) return;

  container.innerHTML = state.recommendations.map(rec => {
    const isSaved = state.savedIds.includes(rec.projectId);
    return `
      <div class="rec-card">
        <div class="rec-impact-pill ${esc(rec.impactClass)}">
          <div class="rec-impact-score">
            <svg width="16" height="16" fill="currentColor" viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>
            <span>${esc(rec.score)}</span>
          </div>
          <span class="rec-impact-label">${esc(rec.impactLevel)}</span>
        </div>

        <div class="rec-card-header">
          <img class="rec-card-img" src="${esc(rec.img)}" alt="${esc(rec.title)}" />
        </div>

        <span class="tag-pill ${esc(rec.categoryClass)}" style="align-self: flex-start; margin-bottom: 8px;">${esc(rec.category)}</span>
        <h3 class="rec-card-title">${esc(rec.title)}</h3>
        
        <div class="rec-location">
          <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
          <span>${esc(rec.location)}</span>
        </div>

        <p class="rec-desc">${esc(rec.desc)}</p>

        <div class="rec-why-box ${esc(rec.impactClass)}">
          <svg class="why-icon" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
          <div>
            <div class="why-title">Why it is recommended?</div>
            <div class="why-text">${esc(rec.whyText)}</div>
          </div>
        </div>

        <div class="rec-actions">
          <button class="btn-navy" onclick="openProjectModal('${esc(rec.projectId)}')">View Details &rarr;</button>
          <button class="btn-save-toggle ${isSaved ? 'saved' : ''}" onclick="toggleSaveProject('${esc(rec.projectId)}')">
            <svg width="16" height="16" fill="${isSaved ? 'currentColor' : 'none'}" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z"/></svg>
            <span>${isSaved ? 'Saved' : 'Save'}</span>
          </button>
        </div>
      </div>
    `;
  }).join('');
}


/* =========================================================
   SAVED PROJECTS TABLE & COMPARE MATRIX (Image 10)
   ========================================================= */
function renderSavedProjectsTable() {
  const tbody = document.getElementById('saved-table-tbody');
  if (!tbody) return;

  const savedList = state.projects.filter(p => state.savedIds.includes(p.id));

  tbody.innerHTML = savedList.map(p => {
    const isChecked = state.compareIds.includes(p.id);
    return `
      <tr>
        <td>
          <input type="checkbox" class="table-checkbox" ${isChecked ? 'checked' : ''} onchange="toggleCompareSelect('${esc(p.id)}', this.checked)" />
        </td>
        <td>
          <div class="table-project-cell">
            <img class="table-thumb" src="${esc(p.img)}" alt="${esc(p.title)}" />
            <div>
              <div class="table-project-name">${esc(p.title)}</div>
              <div class="table-project-sub">${esc(p.desc)}</div>
            </div>
          </div>
        </td>
        <td>
          <span class="tag-pill ${esc(p.categoryClass)}">${esc(p.category)}</span>
        </td>
        <td>
          <span style="display: flex; align-items: center; gap: 4px;">
            <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/></svg>
            ${esc(p.location)}
          </span>
        </td>
        <td style="font-weight: 600;">${esc(p.budget)}</td>
        <td>
          <span class="table-impact-pill ${esc(p.impactLevel.toLowerCase())}">
            <span class="table-impact-dot"></span>
            ${p.impactScore === null ? '&mdash;' : p.impactScore} ${esc(p.impactLevel)}
          </span>
        </td>
        <td style="color: var(--gray-500);">${esc(p.addedDate)}</td>
        <td>
          <span class="table-action-link" onclick="openProjectModal('${esc(p.id)}')">
            View &rarr;
          </span>
        </td>
        <td>
          <button class="dots-menu-btn" title="More options">
            <svg width="18" height="18" fill="currentColor" viewBox="0 0 24 24"><path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"/></svg>
          </button>
        </td>
      </tr>
    `;
  }).join('');

  updateCompareCountUI();
}

function toggleCompareSelect(id, checked) {
  if (checked) {
    if (!state.compareIds.includes(id)) state.compareIds.push(id);
  } else {
    state.compareIds = state.compareIds.filter(item => item !== id);
  }
  updateCompareCountUI();
}

function toggleSelectAllSaved(checked) {
  const savedList = state.projects.filter(p => state.savedIds.includes(p.id));
  if (checked) {
    state.compareIds = savedList.map(p => p.id);
  } else {
    state.compareIds = [];
  }
  renderSavedProjectsTable();
}

function updateCompareCountUI() {
  const countBtn = document.getElementById('compare-selected-btn');
  const countText = document.getElementById('selected-counter-text');
  if (countBtn) countBtn.textContent = `Compare Selected (${state.compareIds.length})`;
  if (countText) countText.textContent = `${state.compareIds.length} of ${state.savedIds.length} selected`;
}

function toggleSaveProject(id) {
  if (state.savedIds.includes(id)) {
    state.savedIds = state.savedIds.filter(itemId => itemId !== id);
    state.compareIds = state.compareIds.filter(itemId => itemId !== id);
  } else {
    state.savedIds.push(id);
  }
  renderRecommendations();
  renderSavedProjectsTable();
}


/* =========================================================
   MODAL CONTROLLERS (Compare Matrix & Project Details)
   ========================================================= */
function openCompareModal() {
  if (state.compareIds.length === 0) {
    alert('Please select at least one project to compare.');
    return;
  }

  const selectedProjects = state.projects.filter(p => state.compareIds.includes(p.id));
  const container = document.getElementById('compare-matrix-body');
  if (!container) return;

  container.innerHTML = `
    <table class="compare-table">
      <thead>
        <tr>
          <th>Metric / Attribute</th>
          ${selectedProjects.map(p => `
            <th class="compare-project-head">
              <img src="${esc(p.img)}" alt="${esc(p.title)}" />
              <div class="compare-project-title">${esc(p.title)}</div>
            </th>
          `).join('')}
        </tr>
      </thead>
      <tbody>
        <tr>
          <th>Category</th>
          ${selectedProjects.map(p => `<td><span class="tag-pill ${esc(p.categoryClass)}">${esc(p.category)}</span></td>`).join('')}
        </tr>
        <tr>
          <th>Location</th>
          ${selectedProjects.map(p => `<td>${esc(p.location)}</td>`).join('')}
        </tr>
        <tr>
          <th>Budget (INR)</th>
          ${selectedProjects.map(p => `<td style="font-weight:700;">${esc(p.budget)}</td>`).join('')}
        </tr>
        <tr>
          <th>Impact Score</th>
          ${selectedProjects.map(p => `
            <td>
              <span class="table-impact-pill ${esc(p.impactLevel.toLowerCase())}">
                ${p.impactScore === null ? '&mdash;' : p.impactScore} ${esc(p.impactLevel)}
              </span>
            </td>
          `).join('')}
        </tr>
        <tr>
          <th>Target Beneficiaries</th>
          ${selectedProjects.map(p => `<td>${(p.budgetValue / 100).toLocaleString('en-IN')} individuals</td>`).join('')}
        </tr>
        <tr>
          <th>SDG Alignment</th>
          ${selectedProjects.map(p => `<td>SDG 3 (Health), SDG 4 (Quality Education), SDG 13 (Climate Action)</td>`).join('')}
        </tr>
        <tr>
          <th>Status</th>
          ${selectedProjects.map(p => `<td><span class="status-pill ${esc(p.statusClass)}">${esc(p.status)}</span></td>`).join('')}
        </tr>
      </tbody>
    </table>
  `;

  document.getElementById('compare-modal').classList.add('active');
}

function closeCompareModal() {
  document.getElementById('compare-modal').classList.remove('active');
}

function openProjectModal(id) {
  const p = state.projects.find(item => item.id === id);
  if (!p) return;

  const container = document.getElementById('project-detail-body');
  if (!container) return;

  container.innerHTML = `
    <div style="display: flex; gap: 24px; margin-bottom: 24px;">
      <img src="${esc(p.img)}" style="width: 280px; height: 180px; border-radius: 12px; object-fit: cover;" alt="${esc(p.title)}" />
      <div>
        <div style="display: flex; gap: 10px; margin-bottom: 12px;">
          <span class="tag-pill ${esc(p.categoryClass)}">${esc(p.category)}</span>
          <span class="status-pill ${esc(p.statusClass)}">${esc(p.status)}</span>
        </div>
        <h2 style="font-size: 24px; font-weight: 800; color: var(--navy-900); margin-bottom: 8px;">${esc(p.title)}</h2>
        <p style="font-size: 14px; color: var(--gray-600); line-height: 1.5; margin-bottom: 16px;">${esc(p.desc)}</p>
        
        <div style="display: flex; gap: 20px; font-size: 13px; font-weight: 600; color: var(--gray-700);">
          <div>📍 ${esc(p.location)}</div>
          <div>💰 ${esc(p.budget)}</div>
          <div>📅 ${esc(p.duration)}</div>
        </div>
      </div>
    </div>
    
    <div style="background-color: #F8FAF9; padding: 20px; border-radius: 12px; margin-bottom: 20px;">
      <h4 style="font-size: 15px; font-weight: 700; color: var(--navy-900); margin-bottom: 8px;">Key Performance Indicators (KPIs)</h4>
      <ul style="padding-left: 20px; font-size: 13px; color: var(--gray-700); line-height: 1.6;">
        <li>${p.impactScore === null ? 'No NIDHI impact score: this is a historical record, not a scored proposal.' : 'Baseline impact metric score of ' + p.impactScore + '/100'}</li>
        <li>Direct coverage across 12 aspirational districts</li>
        <li>Quarterly audit compliance verified by NIDHI CSR Governance Board</li>
      </ul>
    </div>
  `;

  document.getElementById('project-detail-modal').classList.add('active');
}

function closeProjectModal() {
  document.getElementById('project-detail-modal').classList.remove('active');
}

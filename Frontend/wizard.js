/* =========================================================
   NIDHI — wizard form reader
   Converts the 5-step wizard into the backend's GenerateRequest.
   Every numeric field is coerced here; the backend rejects strings.
   ========================================================= */

function $val(id, fallback = '') {
  const el = document.getElementById(id);
  return el && el.value !== '' ? el.value : fallback;
}

function $checkedValues(group) {
  return Array.from(
    document.querySelectorAll(`input[data-group="${group}"]:checked`)
  ).map(el => el.value);
}

/** Parse "₹10,00,000" / "50 lakh" / "1.5 cr" into a number. */
function parseMoney(text, fallback = 0) {
  if (typeof text === 'number') return text;
  if (!text) return fallback;
  const cleaned = String(text).replace(/[₹,\s]/g, '').toLowerCase();
  const num = parseFloat(cleaned.replace(/[^\d.]/g, ''));
  if (!isFinite(num)) return fallback;
  if (/cr|crore/.test(cleaned)) return num * 10000000;
  if (/l|lakh|lac/.test(cleaned)) return num * 100000;
  return num;
}

/** Multi-select values, or [] when nothing is chosen. */
function selectedOptions(id) {
  const el = document.getElementById(id);
  if (!el) return [];
  return Array.from(el.selectedOptions || []).map(o => o.value).filter(Boolean);
}

/**
 * Build the GenerateRequest payload.
 * Returns { payload, errors } — errors is empty when the form is usable.
 */
function parseWizard() {
  const errors = [];

  const budget = parseMoney($val('wiz-budget'), 0);
  if (!budget) errors.push('Select a total CSR budget (Step 1).');

  const states = selectedOptions('wiz-location');
  if (!states.length) errors.push('Select at least one target state (Step 1).');

  const sector = $val('wiz-cause');
  if (!sector) errors.push('Select a primary sector (Step 2).');

  let minFunding = parseMoney($val('wiz-min-funding'), 0);
  let maxFunding = parseMoney($val('wiz-max-funding'), 0);
  const projects = parseInt($val('wiz-num-projects', '5'), 10) || 5;

  // The backend rejects max < min outright, so catch it here with a message
  // the user can act on rather than surfacing a 422.
  if (minFunding && maxFunding && maxFunding < minFunding) {
    errors.push('Maximum project funding cannot be less than the minimum (Step 1).');
  }
  // A floor that cannot be met by any single project is unsatisfiable.
  if (minFunding && minFunding > budget) {
    errors.push('Minimum project funding exceeds the total budget (Step 1).');
  }

  const districts = $val('wiz-districts')
    .split(',').map(d => d.replace(/\(.*?\)/g, '').trim()).filter(Boolean);

  const collaboration = $checkedValues('collaboration');
  const employeeBox = document.getElementById('wiz-employee-involvement');

  // Darpan registration cannot be verified against any NGO record, so it is
  // carried as a stated preference rather than a hard eligibility filter that
  // would silently exclude every candidate.
  const notes = $checkedValues('compliance-note');
  const subSectors = $checkedValues('subsector');
  const comments = [
    $val('wiz-description'),
    notes.length ? `Preferred: ${notes.join(', ')}.` : '',
    $val('wiz-ngo-rating') ? `Partner rating preference: ${$val('wiz-ngo-rating')}.` : '',
    $val('wiz-ngo-geo') ? `Geographic capability: ${$val('wiz-ngo-geo')}.` : '',
  ].filter(Boolean).join(' ');

  const payload = {
    total_csr_budget: budget,
    funding_type: $val('wiz-funding-type', 'Grant'),
    project_duration: parseInt($val('wiz-duration', '12'), 10) || 12,
    number_of_projects: projects,
    min_project_funding: minFunding,
    max_project_funding: maxFunding,

    country: $val('wiz-country', 'India'),
    states,
    districts,

    csr_sector: sector,
    csr_sub_sector: subSectors.join('; '),
    beneficiary_categories: $checkedValues('beneficiary'),

    ngo_experience: parseInt($val('wiz-ngo-exp', '0'), 10) || 0,
    similar_project_experience: $val('wiz-ngo-similar', 'false') === 'true',
    geographic_capability: $val('wiz-ngo-geo', ''),
    ngo_rating: $val('wiz-ngo-rating', ''),
    compliance_requirements: $checkedValues('compliance'),

    collaboration_type: collaboration.join(', '),
    employee_involvement: !!(employeeBox && employeeBox.checked),
    additional_comments: comments,
    currency: 'INR',
  };

  return { payload, errors };
}

/** Format a number as Indian-grouped currency. */
function formatMoney(amount, currency = 'INR') {
  const n = Number(amount) || 0;
  if (currency === 'USD') return '$' + n.toLocaleString('en-US');
  return '₹' + n.toLocaleString('en-IN');
}

/** Second half of the e2e run: wizard parsing, live analysis, and language. */

async function run(window, doc, check, sleep, ev) {
  const before = 0;
  let failures = 0;
  const wrap = (label, cond, detail) => { if (!check(label, cond, detail)) failures++; };

  console.log('\n--- 3. wizard parsing ---');
  const parsed = ev('parseWizard()');
  wrap('form parses without errors', parsed.errors.length === 0, parsed.errors.join('; '));
  const p = parsed.payload;
  wrap('budget is a number',
    typeof p.total_csr_budget === 'number' && p.total_csr_budget > 0, String(p.total_csr_budget));
  wrap('duration is an int', Number.isInteger(p.project_duration), String(p.project_duration));
  wrap('project count is an int', Number.isInteger(p.number_of_projects), String(p.number_of_projects));
  wrap('ngo_experience is an int', Number.isInteger(p.ngo_experience), String(p.ngo_experience));
  wrap('similar_project_experience is a bool', typeof p.similar_project_experience === 'boolean');
  wrap('states is a non-empty array',
    Array.isArray(p.states) && p.states.length > 0, JSON.stringify(p.states));
  wrap('beneficiaries read from checkboxes',
    p.beneficiary_categories.length > 0, JSON.stringify(p.beneficiary_categories));
  wrap('compliance read from checkboxes',
    p.compliance_requirements.length > 0, JSON.stringify(p.compliance_requirements));
  wrap('collaboration read from checkboxes', p.collaboration_type.length > 0, p.collaboration_type);
  wrap('Darpan kept out of hard filters',
    !p.compliance_requirements.some(c => /darpan/i.test(c)) && /Darpan/i.test(p.additional_comments),
    JSON.stringify(p.compliance_requirements));
  wrap('min/max funding are numbers',
    typeof p.min_project_funding === 'number' && typeof p.max_project_funding === 'number',
    `${p.min_project_funding} / ${p.max_project_funding}`);

  console.log('\n--- 4. live analysis rendered in the UI ---');
  await ev('runAnalysis')(p);
  await sleep(400);

  wrap('analysis view is active', doc.getElementById('ws-analysis').classList.contains('active'));
  wrap('results section shown', doc.getElementById('an-results').style.display === 'block');
  const cards = doc.querySelectorAll('#an-scorecards .an-card');
  wrap('scorecards rendered', cards.length >= 3, `${cards.length} cards`);
  wrap('scorecard shows all 10 dimensions',
    cards[0] && cards[0].querySelectorAll('.an-meter').length === 10,
    cards[0] ? String(cards[0].querySelectorAll('.an-meter').length) : 'none');
  wrap('confidence shown separately from score',
    cards[0] && cards[0].textContent.includes('confidence in this score'));
  wrap('unknown fields surfaced',
    doc.getElementById('an-scorecards').textContent.includes('No evidence found for'));
  wrap('fund split rendered',
    doc.querySelectorAll('#an-fundsplit .an-alloc').length > 0);
  wrap('partner recommendations rendered',
    doc.getElementById('an-ngos').textContent.length > 50);
  wrap('comparison table rendered', !!doc.querySelector('#an-comparison table'));
  wrap('strategic insights rendered',
    doc.querySelectorAll('#an-insights li').length >= 3,
    String(doc.querySelectorAll('#an-insights li').length));
  wrap('review flags rendered', doc.querySelectorAll('#an-flags .an-flag').length > 0);
  wrap('run id captured', !!ev('analysis.lastRunId'), String(ev('analysis.lastRunId')));
  wrap('progress reached complete',
    doc.getElementById('an-progress-title').textContent.includes('complete'),
    doc.getElementById('an-progress-title').textContent);
  wrap('data source pill shown',
    doc.getElementById('an-source-pill').style.display !== 'none',
    doc.getElementById('an-source-pill').textContent);

  console.log('\n--- 5. language ---');
  const body = doc.getElementById('ws-analysis').textContent.toLowerCase();
  wrap('no approval language',
    !['has been approved', 'is approved', 'successfully launched'].some(s => body.includes(s)));
  wrap('uses recommendation language', body.includes('recommend'));

  console.log('\n--- 6. validation guard ---');
  const budgetSel = doc.getElementById('wiz-budget');
  budgetSel.value = '';
  const bad = ev('parseWizard()');
  wrap('missing budget is caught', bad.errors.length > 0, JSON.stringify(bad.errors));
  ev('completeLaunchCSR()');
  wrap('submit blocked with a readable message',
    window.__lastAlert && window.__lastAlert.includes('budget'), String(window.__lastAlert));

  return failures;
}

module.exports = { run };

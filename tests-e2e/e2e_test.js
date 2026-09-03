/**
 * End-to-end browser test for the NIDHI frontend.
 *
 * Loads the real index.html into jsdom, runs the real app scripts against the
 * live backend, and asserts the UI ends up showing real results.
 * Requires the backend running on :8000.   Run:  node e2e_test.js
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const FRONTEND = path.join(__dirname, '..', 'Frontend');
const BASE = 'http://127.0.0.1:8000';
let failures = 0;

function check(label, condition, detail = '') {
  const ok = !!condition;
  if (!ok) failures++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail && !ok ? ' -> ' + detail : ''}`);
  return ok;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  const html = fs.readFileSync(path.join(FRONTEND, 'index.html'), 'utf8');
  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    url: BASE + '/app/',
    pretendToBeVisual: true,
    beforeParse(window) {
      window.fetch = (...args) => fetch(...args);
      window.alert = msg => { window.__lastAlert = msg; };
      window.TextDecoder = TextDecoder;
    },
  });
  const { window } = dom;
  const doc = window.document;

  for (const file of ['api.js', 'wizard.js', 'analysis.js', 'company.js', 'app.js']) {
    const script = doc.createElement('script');
    script.textContent = fs.readFileSync(path.join(FRONTEND, file), 'utf8');
    doc.body.appendChild(script);
  }
  doc.dispatchEvent(new window.Event('DOMContentLoaded'));
  await sleep(150);

  // Top-level const/function bindings are lexical, not window properties.
  const ev = expr => window.eval(expr);

  console.log('\n--- 1. page + scripts ---');
  check('all app functions defined',
    ['loginUser', 'parseWizard', 'runAnalysis', 'completeLaunchCSR', 'loadCompanyData']
      .every(fn => ev(`typeof ${fn}`) === 'function'));
  check('analysis view exists', !!doc.getElementById('ws-analysis'));
  check('brand is Temenos',
    doc.getElementById('user-name-badge').textContent.includes('Temenos'),
    doc.getElementById('user-name-badge').textContent);

  console.log('\n--- 2. login ---');
  ev("state.activeEntity = 'Company'");
  doc.getElementById('username-input').value = 'temenos_admin';
  doc.getElementById('password-input').value = 'wrong-password';
  await ev('loginUser()');
  await sleep(500);
  const err = doc.getElementById('login-error');
  check('bad password shows an error', err && err.style.display !== 'none',
    err ? err.textContent : 'no element');
  check('bad password does not enter workspace',
    !doc.getElementById('screen-workspace').classList.contains('active'));

  doc.getElementById('password-input').value = 'temenos@nidhi2026';
  await ev('loginUser()');
  await sleep(1200);
  check('correct password enters workspace',
    doc.getElementById('screen-workspace').classList.contains('active'));
  check('company data loaded from backend',
    ev("state.companyProfile && state.companyProfile.legal_name") &&
    String(ev("state.companyProfile.legal_name")).includes('Temenos'),
    String(ev("state.companyProfile && state.companyProfile.legal_name")));
  check('projects replaced with the real Temenos record',
    ev('state.projects.length') === 15, `got ${ev('state.projects.length')}`);
  check('no Tata Steel data remains',
    !ev('JSON.stringify(state.projects)').includes('Tata Steel'));

  return { window, doc, ev };
}

module.exports = { main, check, sleep };

if (require.main === module) {
  main().then(async ({ window, doc, ev }) => {
    const rest = require('./e2e_part2.js');
    failures += await rest.run(window, doc, check, sleep, ev);
    console.log(`\n${failures === 0 ? 'ALL CHECKS PASSED' : failures + ' CHECK(S) FAILED'}`);
    window.close();
    process.exit(failures === 0 ? 0 : 1);
  }).catch(err => { console.error('\nE2E CRASHED:', err); process.exit(1); });
}

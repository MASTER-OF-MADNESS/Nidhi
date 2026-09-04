/**
 * What the user sees when the backend is unreachable.
 * The dashboard must never present leftover markup as real figures.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const FRONTEND = path.join(__dirname, '..', 'Frontend');
let failures = 0;
const check = (label, cond, detail = '') => {
  if (!cond) failures++;
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${label}${detail && !cond ? ' -> ' + detail : ''}`);
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const dom = new JSDOM(fs.readFileSync(path.join(FRONTEND, 'index.html'), 'utf8'), {
    runScripts: 'dangerously',
    url: 'http://127.0.0.1:9/app/',        // nothing listens here
    pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
      w.alert = m => { w.__lastAlert = m; };
      w.TextDecoder = TextDecoder;
    },
  });
  const { window } = dom, doc = window.document;
  for (const f of ['api.js', 'wizard.js', 'analysis.js', 'company.js', 'app.js']) {
    const s = doc.createElement('script');
    s.textContent = fs.readFileSync(path.join(FRONTEND, f), 'utf8');
    doc.body.appendChild(s);
  }
  doc.dispatchEvent(new window.Event('DOMContentLoaded'));
  await sleep(100);
  const ev = e => window.eval(e);

  console.log('\n--- backend unreachable ---');
  ev("state.activeEntity = 'Company'");
  doc.getElementById('username-input').value = 'temenos_admin';
  doc.getElementById('password-input').value = 'temenos@nidhi2026';
  await ev('loginUser()');
  await sleep(300);

  const err = doc.getElementById('login-error');
  check('login failure is explained, not silent',
    err && err.style.display !== 'none' && /backend/i.test(err.textContent),
    err ? err.textContent : 'no banner');

  // Force past login to inspect the dashboard itself.
  await ev("loadCompanyData('temenos')");
  await sleep(300);

  const kpis = [...doc.querySelectorAll('#ws-overview .kpi-value')].map(n => n.textContent.trim());
  console.log(`      KPI values on screen: ${JSON.stringify(kpis)}`);
  check('no fabricated figures shown',
    kpis.every(v => v === '\u2014' || v === '' || v === '-'), JSON.stringify(kpis));
  check('the failure is visible on the dashboard',
    doc.getElementById('ws-load-error').style.display !== 'none',
    doc.getElementById('ws-load-error').textContent);
  check('no stale demo project data',
    ev('state.projects.length') === 0, `${ev('state.projects.length')} projects`);

  console.log(`\n${failures === 0 ? 'ALL CHECKS PASSED' : failures + ' FAILED'}`);
  window.close();
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error('CRASHED:', e); process.exit(1); });

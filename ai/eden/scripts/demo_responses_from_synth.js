/*
 * demo_responses_from_synth.js
 *
 * Reads `gold_eden_synthesised` and generates the EDEN customer-support answers for
 * Q1 / Q2 / Q3, applying the constraints from
 *   src/eden/docs/EDEN_CustomerSupport_Reasoning_Constraints.md
 *
 * This is a REFERENCE IMPLEMENTATION of the answer logic - the shape the real
 * reporting/insight agent code should follow. It proves the synthetic data actually
 * drives correct, confidence-gated answers rather than being merely well-formed.
 *
 * RUN: mongosh "$URI" --quiet --file src/eden/scripts/demo_responses_from_synth.js
 */

const t = db.getSiblingDB('gold_eden_synthesised');
const ph = t.project_health;
const up = t.user_permissions;

// --- constraint 32: v1 allow-list is EXACTLY 'admin'. superAdmin is refused. -------
const MARGIN_ALLOWED_ROLES = ['admin'];
function canSeeMargin(user) {
  return (user.roles || []).some(r => MARGIN_ALLOWED_ROLES.indexOf(r) >= 0);
}

// --- constraint 34: strip margin fields from the payload BEFORE it reaches the LLM /
//     the response. Not merely "don't mention it".
const MARGIN_FIELDS = ['margin', 'profitMargin', 'marginDelta'];

// deep clone that PRESERVES Date objects (JSON round-trip would stringify them)
function deepClone(v) {
  if (v === null || typeof v !== 'object') return v;
  if (v instanceof Date) return new Date(v.getTime());
  if (Array.isArray(v)) return v.map(deepClone);
  const o = {};
  Object.keys(v).forEach(k => { o[k] = deepClone(v[k]); });
  return o;
}

function redactMargin(doc, user) {
  if (canSeeMargin(user)) return doc;
  const clone = deepClone(doc);
  MARGIN_FIELDS.forEach(f => { delete clone[f]; });
  if (clone.estimateSummary) MARGIN_FIELDS.forEach(f => { delete clone.estimateSummary[f]; });
  (clone.estimateHistory || []).forEach(e => {
    if (e.snapshot) delete e.snapshot.margin;
    if (e.delta) delete e.delta.margin;
  });
  return clone;
}

function money(n) {
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}
function shortDate(d) {
  if (!d) return null;
  return ['January','February','March','April','May','June','July',
          'August','September','October','November','December'][d.getMonth()] + ' ' + d.getDate();
}

// ---------------------------------------------------------------------------
// Q1 - "Why did the estimate price change?"
// ---------------------------------------------------------------------------
function answerQ1(doc) {
  const hist = (doc.estimateHistory || []).slice().sort((a, b) => a.version - b.version);
  if (hist.length < 2) {
    return 'Your estimate has not changed since it was first prepared.';
  }
  const cur = hist[hist.length - 1];
  const prev = hist[hist.length - 2];
  const from = prev.snapshot.materialCost;
  const to = cur.snapshot.materialCost;

  // constraint 36: branch on explicit confidence, never assume causation
  if (cur.confidence === 'high' && cur.evidence && cur.evidence.bomReconciles) {
    const e = cur.evidence;
    if (e.newUnitCost !== e.priorUnitCost) {
      return 'Your material cost went from ' + money(from) + ' to ' + money(to) +
             ' because the unit price of ' + e.materialName + ' was updated from ' +
             money(e.priorUnitCost) + ' to ' + money(e.newUnitCost) + ' per unit' +
             (e.quantity ? ' across ' + e.quantity + ' units' : '') +
             ', effective ' + shortDate(e.effectiveDate) +
             '. That was recorded when the supplier receipt was captured.';
    }
    return 'Your material cost went from ' + money(from) + ' to ' + money(to) +
           ' because an additional ' + e.materialName + ' line was added to the bill of ' +
           'materials. The unit price itself did not change.';
  }

  // constraint 11: report the figure, assert NO cause
  return 'Your material cost estimate was updated from ' + money(from) + ' to ' + money(to) +
         ' on ' + shortDate(cur.validFrom) + '. I can\'t confirm the specific driver of that ' +
         'change from the project records - I can loop in your project manager for detail.';
}

// ---------------------------------------------------------------------------
// Q2 - "Why did the tentative start date move?"
// ---------------------------------------------------------------------------
function answerQ2(doc) {
  const revs = (doc.scheduleRevisions || []).slice().sort((a, b) => a.version - b.version);
  if (revs.length < 2) {
    return 'Your start date has not changed since the schedule was first set.';
  }
  const cur = revs[revs.length - 1];
  const newStart = cur.snapshot.jobStartDate;
  const shift = cur.delta ? cur.delta.startShiftDays : null;
  const dir = shift == null ? null : (shift < 0 ? 'earlier' : 'later');

  // constraint 16/36: causation is NOT provable -> hedge, never assert
  if (cur.confidence === 'low' && cur.evidence) {
    const e = cur.evidence;
    return 'Your start date moved ' + dir + ' to ' + shortDate(newStart) +
           (shift ? ' (by ' + Math.abs(shift) + ' day' + (Math.abs(shift) === 1 ? '' : 's') + ')' : '') +
           '. The crew assigned to your job finished a previous project about ' +
           e.daysFinishedEarly + ' day' + (e.daysFinishedEarly === 1 ? '' : 's') +
           ' ahead of schedule just before that, so their availability may have shifted your ' +
           'dates. Your project manager can confirm the exact reason.';
  }

  return 'Your project\'s start date moved to ' + shortDate(newStart) +
         '. I don\'t have a confirmed reason for the change on file - I can check with your ' +
         'project manager.';
}

// ---------------------------------------------------------------------------
// Q3 - "What is the profit margin?"
// ---------------------------------------------------------------------------
function answerQ3(doc, user) {
  if (!canSeeMargin(user)) {
    return 'I\'m not able to share margin or profitability details - that isn\'t available at ' +
           'your access level. I can walk you through the estimate breakdown (materials, ' +
           'labor, overhead) if that\'s helpful.';
  }
  const total = doc.estimatedCost;
  const pct = total ? (doc.margin / total * 100).toFixed(1) : null;
  return 'The profit margin on this project is ' + money(doc.margin) + ' on a ' + money(total) +
         ' total estimate' + (pct ? ' (about ' + pct + '%)' : '') +
         ', based on the latest estimate version.';
}

// ---------------------------------------------------------------------------
// DRIVE THE DEMO
// ---------------------------------------------------------------------------
const users = up.find({}).sort({ user_account_id: 1 }).toArray();
const admin = users.filter(u => u.roles.indexOf('admin') >= 0)[0];
const sales = users.filter(u => u.roles.indexOf('sales') >= 0)[0];
const customer = users.filter(u => u.roles.indexOf('customer') >= 0)[0];
const superAdmin = users.filter(u => u.roles.indexOf('superAdmin') >= 0)[0];

const scenarios = [
  { pid: '9500005', label: 'GOLDEN PATH - Q1 high confidence + Q2 low confidence' },
  { pid: '9500001', label: 'Q1 high confidence (receipt-driven unit price rise)' },
  { pid: '9500002', label: 'Q1 NO confidence (BOM does not reconcile - guard fires)' },
  { pid: '9500003', label: 'Q2 low confidence (crew freed early)' },
  { pid: '9500004', label: 'Q2 NO confidence (no linkable evidence)' }
];

scenarios.forEach(s => {
  const raw = ph.findOne({ projectId: s.pid });
  print('');
  print('###############################################################');
  print('# project ' + s.pid + '  -  ' + s.label);
  print('###############################################################');

  print('');
  print('--- Q1: "Why did the estimate price change?"  (asked as CUSTOMER) ---');
  print(answerQ1(redactMargin(raw, customer)));

  print('');
  print('--- Q2: "Why did the tentative start date move?"  (asked as CUSTOMER) ---');
  print(answerQ2(redactMargin(raw, customer)));
});

// Q3 across every role, on the golden-path project
const gp = ph.findOne({ projectId: '9500005' });
print('');
print('###############################################################');
print('# Q3 RBAC GATE  -  "What is the profit margin?"  (project 9500005)');
print('###############################################################');
[admin, superAdmin, sales, customer].forEach(u => {
  print('');
  print('--- as ' + u.displayName + '  roles=[' + u.roles.join(',') + '] ---');
  print(answerQ3(redactMargin(gp, u), u));
});

// prove the redaction is structural, not cosmetic (constraint 34)
print('');
print('###############################################################');
print('# constraint 34 proof: margin is STRIPPED from the payload,');
print('# not merely omitted from the wording');
print('###############################################################');
[admin, sales].forEach(u => {
  const red = redactMargin(gp, u);
  print('');
  print('as ' + u.displayName + ' (roles=' + u.roles.join(',') + '):');
  print('  payload.margin                        = ' + JSON.stringify(red.margin));
  print('  payload.estimateSummary.marginDelta   = ' + JSON.stringify(red.estimateSummary.marginDelta));
  print('  payload.estimateHistory[1].snapshot.margin = ' +
        JSON.stringify(red.estimateHistory[1].snapshot.margin));
});
print('');

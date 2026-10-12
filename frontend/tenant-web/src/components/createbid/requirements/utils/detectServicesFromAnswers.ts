import { QAResponse } from '../../../../services/BallParkEstimateService';

// Exact-match answer text/keywords → canonical BPE service name.
const SERVICE_EXACT_MAP: Record<string, string> = {
  'tuneup': 'Tuneup', 'tune up': 'Tuneup', 'tune-up': 'Tuneup', 'roofmaxx': 'Tuneup', 'roof maxx': 'Tuneup',
  'enhancement': 'Enhancement', 'roof enhancement': 'Enhancement',
  'cleaning': 'Cleaning', 'clean up': 'Cleaning', 'cleanup': 'Cleaning', 'cleaner': 'Cleaning', 'gutter cleaning': 'Cleaning', 'gutter': 'Cleaning',
  'cericade': 'Tuneup', 'repair': 'Tuneup', 'replacement': 'Tuneup',
};
const SERVICE_PARTIAL: Array<[string, string]> = [
  ['enhancement', 'Enhancement'], ['roofmaxx', 'Tuneup'], ['cericade', 'Tuneup'],
  ['replacement', 'Tuneup'], ['repair', 'Tuneup'], ['tuneup', 'Tuneup'], ['tune', 'Tuneup'],
  ['clean', 'Cleaning'], ['gutter', 'Cleaning'],
];

/**
 * Detect the canonical BPE service names ("Enhancement" / "Cleaning" / "Tuneup")
 * named in the I.65/I.66 job-type answer(s) within `qaResponses`.
 *
 * Handles three answer shapes:
 *  - legacy multiselect array: ["Tuneup", "Cleaning"]
 *  - legacy space-joined string: "Tuneup Cleaning Enhancement"
 *  - the current single-select COMBO string from "What kind of job is going
 *    to be done?": "Enhancement + Tuneup"
 *
 * A combo answer is split on "+"/"," into individual service segments before
 * matching — matching the whole string as one key would only ever find the
 * FIRST partial keyword (e.g. "enhancement" inside "enhancement + tuneup"),
 * silently dropping every other service named in the combo.
 *
 * PR review finding: that generic split also shatters a multi-word canonical
 * phrase (e.g. "roof maxx", "gutter cleaning") into separate words the moment
 * it appears as ONE PART of a combo — "roof maxx + cleaning" split on
 * whitespace becomes "roof", "maxx", "cleaning", and neither "roof" nor
 * "maxx" alone matches SERVICE_EXACT_MAP or SERVICE_PARTIAL (SERVICE_PARTIAL
 * only has the space-free "roofmaxx"), so Tuneup silently disappears and only
 * Cleaning is detected. Fixed by gluing every known multi-word phrase's
 * internal spaces together BEFORE the generic split runs — "roof maxx"
 * becomes "roofmaxx", which is already an exact-match key — so it survives
 * the split as a single segment, in its original position/order, instead of
 * being torn apart.
 */
const MULTI_WORD_SERVICE_PHRASES = Object.keys(SERVICE_EXACT_MAP)
  .filter((phrase) => phrase.includes(' '))
  // Longest first: a shorter phrase that happens to be a substring of a
  // longer one (none currently, but cheap insurance) must not glue first and
  // leave the longer phrase's remaining spaces unmatched.
  .sort((a, b) => b.length - a.length);

export function detectServicesFromAnswers(qaResponses: QAResponse[]): string[] {
  const detectedServices: string[] = [];
  for (const qr of qaResponses) {
    const qid = qr.question_id;
    if (
      qid !== 'I.65' && qid !== 'i.65' && qid !== 'I.66' && qid !== 'i.66' &&
      !qid.startsWith('I.65_') && !qid.startsWith('i.65_')
    ) continue;
    const vals = Array.isArray(qr.answer_value)
      ? qr.answer_value
      : (qr.answer_value ? [String(qr.answer_value)] : []);
    for (const val of vals) {
      const whole = val.toLowerCase().trim();
      if (!whole) continue;
      // Try the FULL string as one exact phrase first — several map entries are
      // themselves multi-word ("roof maxx", "gutter cleaning", "clean up") and
      // must stay intact for a genuine single-service answer.
      const wholeMatch = SERVICE_EXACT_MAP[whole];
      if (wholeMatch) {
        if (!detectedServices.includes(wholeMatch)) detectedServices.push(wholeMatch);
        continue;
      }
      // Glue any known multi-word phrase's internal spaces together first
      // (see MULTI_WORD_SERVICE_PHRASES's own doc) so it survives the generic
      // split below intact, then split into individual service segments —
      // handles combo answers like "Enhancement + Tuneup" (space-plus-space)
      // AND legacy space-joined multiselect strings like "Tuneup Cleaning
      // Enhancement".
      let glued = whole;
      for (const phrase of MULTI_WORD_SERVICE_PHRASES) {
        if (glued.includes(phrase)) {
          glued = glued.split(phrase).join(phrase.replace(/\s+/g, ''));
        }
      }
      const segments = glued.split(/[+,\s]+/).map((s) => s.trim()).filter(Boolean);
      for (const key of segments) {
        let svc = SERVICE_EXACT_MAP[key];
        if (!svc) { const partial = SERVICE_PARTIAL.find(([kw]) => key.includes(kw)); svc = partial?.[1] ?? ''; }
        if (svc && !detectedServices.includes(svc)) detectedServices.push(svc);
      }
    }
  }
  return detectedServices;
}

/**
 * The canonical BPE service names named by ONE answer or option string.
 *
 * Delegates to detectServicesFromAnswers with a synthetic I.66 response rather than
 * repeating its parsing — that function already handles the exact-phrase case, the
 * multi-word gluing and the +/,/whitespace split, and a second copy of that logic here is
 * exactly how the two would drift.
 *
 * Needed by the job-type write-back: now that "What kind of job is going to be done?"
 * offers the 5 COMBO options instead of one per service, an option Decode like
 * "Enhancement + Tuneup" has to be read as a SET of services — both to locate the question
 * and to decide which single option the confirmed package corresponds to.
 */
export function servicesInAnswerText(value: string): string[] {
  return detectServicesFromAnswers([{ question_id: 'I.66', answer_value: String(value ?? '') }]);
}

/**
 * Purpose : Decide WHICH tasks a schedule run left with unconfirmed dates, shield
 *           exactly those, and say why in plain words.
 *
 * Why
 * ---
 * Vincy, 2026-08-13: "If a schedule happens and changes any set of tasks in a
 * project's status to PROVISIONAL, detect it and mark just those tasks of the
 * project as shielded/protected."
 *
 * The rest of that original instruction — "show a refresh button on the top-most
 * provisioned task, which will pass just the provisioned tasks for rescheduling" —
 * IS GONE, deliberately, and the anchor-picking code that served it went with it.
 * The V2 spec (§8, 2026-08-22) makes every mark carry a case and a named finalizer:
 * a deferred budget chunk is finished by the engine's next chunk, a settling island
 * by its child solve, and work blocked on an undecidable input by whatever unblocks
 * it. The shield lifts on its own. The button that used to lift it re-planned WITHOUT
 * the job-start anchor that produced the dates, so pressing it changed dates that were
 * correct — that is ADO #28358, and retiring the affordance is the fix, not hiding it.
 *
 * Pure on purpose — no React, no context, no clock, no network. Every rule below is
 * a unit test rather than a screenshot, and the decision that will be argued about
 * later (what "provisional" means on the wire) is ONE function, so changing it is one
 * edit in one file.
 *
 * ---------------------------------------------------------------------------
 * WHAT THE ENGINE ACTUALLY WRITES — read before changing anything here
 * ---------------------------------------------------------------------------
 * The provisional stamp is a per-task attribute, not a task status:
 *
 *   • tenant DB `task_attribute` under catalog attribute **567**,
 *     namespace `task`, key `provisional_since`, data type TEXT
 *     [migrations/V115__seed_task_provisional_since_attribute.sql]
 *   • constant: `TaskAttrId.PROVISIONAL_SINCE = 567`
 *     [next-gen-ase shared/utils/constants/task_attribute_ids.py:179]
 *   • writer: `write_provisional_since_sa(...)`
 *     [next-gen-ase shared/service/schedule_writer.py:1247], called inside the
 *     persist transaction from the solve handler — atomic with the schedule dates
 *   • value semantics: a NON-EMPTY in-force ISO-8601 instant = provisional.
 *     It is cleared by writing an EMPTY value (a new version), not by deleting.
 *
 * So "provisional" is NOT one of the TASK_STATUS values, and must never be folded
 * into that enum: a provisional task still has its real status (Scheduled, Blocked,
 * …) and the two facts are independent.
 *
 * ---------------------------------------------------------------------------
 * THE READ PATH IS LIVE — this note used to say the opposite; re-verified 2026-08-22
 * ---------------------------------------------------------------------------
 * On 2026-08-13 this block recorded that nothing carried 567 to the browser, and
 * concluded "this shield is inert on production data". That is no longer true, and the
 * correction matters: it is why a real customer could see greyed rows and press the
 * Reload button that ADO #28358 is about. All three Java layers now carry it:
 *
 *   • `TaskAttributeIds.PROVISIONAL_SINCE = 567`
 *     [src/api/shared/fieldcraft-shared-data/fieldcraft-tenant-data/src/main/java/
 *      com/fieldcraft/tenant/data/domain/TaskAttributeIds.java]
 *   • `TaskViewData.provisionalSince` and `TaskDTO.provisionalSince`, both String —
 *     the attribute is TEXT and an EMPTY value is how the engine clears the mark, so
 *     it must never be typed as a date
 *   • `TaskMapperV3` lifts it out of `attrMap` beside scheduledStart (both the DTO and
 *     the view-data mappings)
 *
 * The camelCase wire name was therefore confirmed, not merely proposed.
 * `PROVISIONAL_SINCE_API_FIELD` below is still the single place the front end spells
 * it; `readProvisionalSince` also accepts the snake_case spelling in case the value
 * ever arrives flattened by ASE (Python) instead.
 *
 * ---------------------------------------------------------------------------
 * WHY THERE IS NO "RE-PLAN THESE TASK UIDS" CALL HERE ANY MORE
 * ---------------------------------------------------------------------------
 * There never was one. Every reschedule route the tenant app has takes either a whole
 * project or ONE trigger task [services/ProjectService.ts], so the old button faked it
 * with an ANCHORED reschedule at the earliest marked task and let ruling C-6's horizon
 * window ripple forward. That is precisely the mechanism that broke #28358: the re-plan
 * re-anchored, C-35 dropped the unlocked job-start floor on the way through, and dates
 * that were correct moved. Under V2 §8 finalizing re-proves with hints and NEVER
 * re-anchors, and it is the engine's job, not a button's.
 *
 * So this module no longer picks an anchor, no longer describes a submission's outcome,
 * and nothing in the web app posts to /refresh-provisional. If a future feature needs to
 * ask the engine for a re-plan, it must state its own anchor explicitly — do not
 * resurrect the "earliest marked task" heuristic, which is what made the damage
 * invisible.
 */

// The grey-out's detector. Imported rather than reimplemented so the shield and the
// grey can never disagree about which rows are provisional — see isTaskProvisional.
import { isProvisional } from "./provisional";

/**
 * The field name the task API is expected to carry attribute 567 under. ONE place,
 * because it is a proposal (see MISSING CONTRACT #1) and not yet confirmed.
 */
export const PROVISIONAL_SINCE_API_FIELD = "provisionalSince";

/**
 * The snake_case alias. Accepted as well as the camelCase name because attribute 567
 * may arrive flattened by either stack — ASE (Python) or TaskServiceV3 (Java) — and
 * whichever wins, the adapter should not need a second change.
 */
export const PROVISIONAL_SINCE_API_FIELD_SNAKE = "provisional_since";

/**
 * The absolute minimum needed to answer "is this shielded?" — deliberately smaller
 * than a task, so the edit-permission gate (whose `EditableTaskLike` has no uid and
 * no dates) can ask the question without widening its own shape.
 */
export interface ProvisionalStampLike {
  isGroup?: boolean;
  /**
   * Attribute 567. A non-empty in-force ISO-8601 instant means the dates are not
   * confirmed. Empty string means explicitly cleared by a later solve — which is a
   * DIFFERENT fact from `undefined` (never told), even though both mean "not
   * provisional" for shielding purposes.
   */
  provisionalSince?: string | null;
}

/** What the set function needs on top of the stamp — satisfied by GanttTask. `startMin`
 *  and `wbs` used to live here to order the re-plan anchor; the anchor is gone, and so
 *  are they. */
export interface ProvisionalTaskLike extends ProvisionalStampLike {
  taskUid: string;
  projectUid?: string;
}

/**
 * Pull attribute 567 off a raw API task without asserting the field exists.
 *
 * Returns `null` for absent / non-string / blank, so an unknown-shaped response can
 * never make a task provisional by accident. Whitespace is trimmed before the
 * emptiness test because the engine clears the flag by writing an EMPTY value, and a
 * value that arrives as `" "` must read as cleared, not as a live instant.
 */
export function readProvisionalSince(raw: unknown): string | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  // BOTH casings, because the two halves of this payload are produced by different
  // stacks: attribute 567 is surfaced by ASE (Python, snake_case) while the rest of the
  // task view comes from TaskServiceV3 (Java, camelCase). Whichever layer ends up
  // flattening it onto the task, the adapter reads it without a second change — the
  // approach Riyaz's adapter took, kept here so there is ONE reader rather than two.
  const value = row[PROVISIONAL_SINCE_API_FIELD] ?? row[PROVISIONAL_SINCE_API_FIELD_SNAKE];
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Whether this task's dates are unconfirmed and it must therefore be shielded.
 *
 * Group / phase rows are excluded: they carry no dates of their own (their span is a
 * rollup of their children), so a shield on a header would lock a row that has
 * nothing editable to protect while telling the user something untrue about it.
 */
export function isTaskProvisional(task: ProvisionalStampLike | null | undefined): boolean {
  if (!task || task.isGroup) return false;
  // DELEGATES to lib/provisional.ts rather than re-testing the field. That module is
  // the grey-out's detector, and the shield must agree with it exactly: a row painted
  // grey but still editable, or shielded but not greyed, is worse than either
  // behaviour alone. The header exclusion above stays here because it is a SHIELD
  // concern — a phase row has no dates of its own to protect, but it is still legitimate
  // to grey it as a rollup.
  return isProvisional(task as Parameters<typeof isProvisional>[0]);
}

/**
 * The shielded set for ONE project — never the board.
 *
 * `projectUid` is required in spirit and optional in signature only so a
 * single-project caller that has already narrowed its list can omit it. Passing it
 * is what stops a multi-project ("All Jobs") board from shielding, or refreshing,
 * another project's tasks.
 */
export function provisionalTaskUids(
  tasks: readonly ProvisionalTaskLike[],
  projectUid?: string | null
): Set<string> {
  const out = new Set<string>();
  for (const task of tasks) {
    if (projectUid && task.projectUid !== projectUid) continue;
    if (isTaskProvisional(task)) out.add(task.taskUid);
  }
  return out;
}

/**
 * The plain-words reason a task is shielded.
 *
 * Deliberately never says "PROVISIONAL". That word is an engine enum; a scheduler
 * reading it learns nothing and reasonably guesses it means "provisionally booked",
 * which is the opposite of the truth. What is true is that the schedule has not
 * finished settling this task, so the dates on screen can still move.
 *
 * It also never tells the user to press anything, and that is the load-bearing part.
 * The previous copy ended "Use Re-plan to bring them up to date" — pointing at the
 * button that changed correct dates (#28358). Under V2 §8 the engine finishes the job
 * itself, so the only honest instruction is that there is no instruction.
 *
 * `count` is the size of the shielded set, used only to tell one task from several.
 */
export function provisionalShieldReason(count: number): {
  /** Row badge tooltip — must fit on one line. */
  badge: string;
  /** Dialog footer / row detail — says why it is held, and that it clears itself. */
  detail: string;
} {
  const these = count === 1 ? "This task" : "These tasks";
  const is = count === 1 ? "is" : "are";
  return {
    badge: "Dates confirming… — the schedule is still settling this",
    detail:
      `${these} ${is} still being settled by the schedule, so the dates on screen ` +
      "will change. Editing is held until they are confirmed, so a change cannot be " +
      "aimed at a date that is about to move. This finishes on its own — there is " +
      "nothing to press.",
  };
}

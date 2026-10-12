// projectScheduling/lib/taskEditPermissions.ts — FIELDCRAFT Gantt edit-permission rules (LAYER C).
//
// Central, single-source-of-truth gate for "can this task be changed from the Gantt?".
// Two lifecycle rules drive the whole UI (grid inline edits, task dialog `ro`, bulk actions):
//
//   1. FINISHED tasks (completed / force-completed / skipped / cancelled) are fully locked —
//      no field edits and no mutating operations (skip, force-complete, block, delete).
//   2. ACTIVE tasks are locked for general field editing. The ONE exception is a
//      "Create BOM Task", whose BOM tab stays open in ADD-ONLY mode (add material/stock;
//      no editing or deleting existing rows). Skip / Force Complete still apply to active
//      tasks — that is the normal GoPAL field-execution workflow.
//
// Status casing varies by source (the Gantt adapter normalizes to lowercase TASK_STATUS
// values; GoPAL / raw API responses can differ), so every check here is case-insensitive.
// FORCE_COMPLETED has no backend equivalent — a forced completion is written as plain
// "completed" (see TaskStatus.java) — but the synthetic value is included for completeness.

import { TASK_STATUS, TaskStatus } from "../types";
import {
  BOM_GROUP_TASKS,
  CREATE_BOM_TASK,
  MISCELLANEOUS_SERVICE,
  nonDeletableTasks,
  deleteWarningTasks,
} from "../../../utils/TaskConstants";
import { isTaskProvisional } from "./provisionalShield";

/** Statuses that mean the task is finished — nothing about it may change anymore. */
const DONE_STATUSES: ReadonlySet<string> = new Set([
  TASK_STATUS.COMPLETED,
  TASK_STATUS.FORCE_COMPLETED,
  TASK_STATUS.SKIPPED,
  TASK_STATUS.CANCELLED,
]);

/**
 * Project lifecycle statuses that make the WHOLE Gantt view-only, regardless of any
 * individual task's own status. Exact Title-Case string literals — confirmed source
 * of truth: treegridGantt/Gantt/GanttChartComponent.tsx (the legacy Gantt, which
 * already locks editing on these). GanttV2/ProjectScheduling never checked project
 * status before; this set is what closes that gap.
 */
const BLOCKED_PROJECT_STATUSES: ReadonlySet<string> = new Set([
  "on hold",
  "project closed",
  "project complete",
  "project inactive",
]);
const MILESTONE_GROUP_DELETE_LOCKED_STATUSES: ReadonlySet<string> = new Set([
  TASK_STATUS.ACTIVE,
  TASK_STATUS.COMPLETED,
  TASK_STATUS.FORCE_COMPLETED,
  TASK_STATUS.SKIPPED,
  TASK_STATUS.SNOOZED,
  TASK_STATUS.PARKED,
  "in_progress",
  "inprogress",
]);

/** The minimal task shape these rules read — satisfied by both GanttTask and the dialog's DialogTask. */
export interface EditableTaskLike {
  status?: TaskStatus | string | null;
  taskType?: string | null;
  isGroup?: boolean;
  isUnmodifiable?: boolean;
  /**
   * Attribute 567 `provisional_since` — a non-empty in-force ISO instant means the
   * last schedule run left this task's dates unconfirmed. Shielding lands HERE, in
   * the one gate every editing surface already asks, rather than as a second
   * parallel protection mechanism: see the note on canEditTaskFields.
   */
  provisionalSince?: string | null;
  /** Promote/Demote/Create-Group synthetic group (workflowStore.ts) — see canEditGroupName. */
  isSyntheticGroup?: boolean;
}

/** The minimal task shape findMilestoneGroupLockingDescendant needs to walk a hierarchy. */
export interface HierarchyTaskLike {
  taskUid: string;
  parentTaskUid: string | null;
  taskName?: string | null;
  status?: TaskStatus | string | null;
}

/** A descendant whose status locks the group against delete. */
export interface MilestoneGroupLock {
  taskUid: string;
  taskName: string;
  status: string;
}

/** The minimal task shape hasCrossProjectSelection reads. */
export interface ProjectScopedTaskLike {
  projectUid?: string | null;
}

/**
 * True when the given tasks span more than one project. Sequential view (the "All
 * Jobs" cross-project feed) is the only place a single selection can mix tasks from
 * different projects at all — By-Project grouping and single-job boards can't produce
 * this shape structurally, so the predicate is a safe no-op there.
 *
 * Deletion rule (product spec, "Sequential View Task Deletion Rules"): a single task
 * may always be deleted regardless of project; multiple tasks may be deleted together
 * only when they all belong to the SAME project. A selection of 0 or 1 tasks can never
 * span multiple projects, so this returns false for those without needing a length
 * check — the Set can only grow past size 1 when there are ≥2 distinct project uids,
 * which requires ≥2 tasks.
 */
export function hasCrossProjectSelection(tasks: readonly ProjectScopedTaskLike[]): boolean {
  const projectUids = new Set(tasks.map((t) => t.projectUid ?? ""));
  return projectUids.size > 1;
}

const norm = (status: TaskStatus | string | null | undefined): string =>
  String(status ?? "").toLowerCase();

/** Finished task (completed / force-completed / skipped / cancelled). */
export function isTaskDone(status: TaskStatus | string | null | undefined): boolean {
  return DONE_STATUSES.has(norm(status));
}

/** Statuses that are operation-locked — task may not be deleted while in this state. */
const DELETE_LOCKED_STATUSES: ReadonlySet<string> = new Set([
  TASK_STATUS.SNOOZED,
  TASK_STATUS.PARKED,
]);

/** True when a task is in a snooze-lifecycle status that must survive until woken/resumed. */
export function isTaskDeleteLocked(status: TaskStatus | string | null | undefined): boolean {
  return DELETE_LOCKED_STATUSES.has(norm(status));
}

/**
 * AB#30316 — statuses that LOCK a whole PO/BOM or Miscellaneous group against delete.
 * Deleting any member of such a group deletes the group (anchor included), so once ANY
 * member has started or finished — someone clocked in, a BOM was created, material was
 * received — no member may be deleted, whichever one the user clicked. Mirrors the
 * engine's `DELETE_LOCKED_STATUSES` (completed / active / in-progress, both spellings)
 * plus the UI-only FORCE_COMPLETED, which the engine stores as completed. The dialog
 * drops the locked group from the request and NAMES it; the engine refuses whole if a
 * locked member ever reaches it (task_handlers.py Phase 1.6b).
 *
 * A GanttTask's status arrives through `normalizeStatus` (apiAdapter.ts), which maps the
 * backend in-progress spellings to ACTIVE (PR 8471 review) — that is how a started task
 * reaches this lock. The raw literals stay for callers that pass an un-normalised status.
 */
const GROUP_DELETE_LOCKED_STATUSES: ReadonlySet<string> = new Set([
  TASK_STATUS.ACTIVE,
  TASK_STATUS.COMPLETED,
  TASK_STATUS.FORCE_COMPLETED,
  "in_progress",
  "inprogress",
]);

/** True when this task's status locks the PO/BOM / Miscellaneous group it belongs to. */
export function isGroupDeleteLockedStatus(status: TaskStatus | string | null | undefined): boolean {
  return GROUP_DELETE_LOCKED_STATUSES.has(norm(status));
}

/** True when this task's status locks the milestone-parent trade group it belongs to. */
export function isMilestoneGroupDeleteLockedStatus(
  status: TaskStatus | string | null | undefined
): boolean {
  return MILESTONE_GROUP_DELETE_LOCKED_STATUSES.has(norm(status));
}

export function findMilestoneGroupLockingDescendants(
  groupUid: string,
  tasksByUid: Map<string, HierarchyTaskLike>
): MilestoneGroupLock[] {
  const seen = new Set<string>([groupUid]);
  const queue: string[] = [];
  const locks: MilestoneGroupLock[] = [];
  tasksByUid.forEach((child, childUid) => {
    if (child.parentTaskUid === groupUid) {
      seen.add(childUid);
      queue.push(childUid);
    }
  });
  while (queue.length > 0) {
    const uid = queue.shift() as string;
    const t = tasksByUid.get(uid);
    if (!t) continue;
    if (isMilestoneGroupDeleteLockedStatus(t.status)) {
      locks.push({ taskUid: uid, taskName: t.taskName ?? "", status: norm(t.status) });
    }
    tasksByUid.forEach((child, childUid) => {
      if (child.parentTaskUid === uid && !seen.has(childUid)) {
        seen.add(childUid);
        queue.push(childUid);
      }
    });
  }
  return locks;
}

/**
 * Task TYPES that the system generates and owns — the invoice/payment chain and the
 * customer-facing billing tasks. They are steps of the billing workflow, not user work,
 * so a person may never delete them from the board (AB#28644).
 *
 * Reuses `nonDeletableTasks` rather than declaring a second list: that constant is the
 * rule the legacy surfaces have always enforced (treegridGantt's OnCanRowDelete,
 * BidTaskList, TaskList), and Gantt V2 simply never asked it — which is the whole bug.
 * One list means the two generations cannot drift about what is protected.
 *
 * PROTECTION IS UNCONDITIONAL, NOT SCOPED TO A PROJECT STATUS. AB#28644 was reported
 * against "Pending Contract Approval" because that is where the tester met it, but the
 * legacy rule these tasks came from never had a status condition, and allowing the
 * delete once the contract is approved would leave V2 and the legacy grids disagreeing
 * about the same task on the same board.
 */
// Built LAZILY and re-derived whenever the source array identity changes, rather than
// once at module scope. Two reasons, both hit in practice:
//   - Import safety. A module-scope `nonDeletableTasks.map(...)` throws at IMPORT time in
//     any suite that partially mocks utils/TaskConstants (the mock returns undefined for
//     the keys it does not name). That took down the whole Gopal suite — a file that
//     never calls this function — because taskEditPermissions sits in its import graph.
//   - Mock correctness. Keying the cache on the array REFERENCE means a suite that swaps
//     the list gets the swapped list, instead of whichever one won the import race.
// Cost is one Set build per distinct source array, then O(1) per lookup.
let cachedSource: readonly string[] | undefined;
let cachedProtectedTypes: ReadonlySet<string> = new Set<string>();

/** True when the task's TYPE is a system-owned billing step that must never be deleted. */
export function isTaskTypeDeleteProtected(taskType: string | null | undefined): boolean {
  const source: readonly string[] = nonDeletableTasks ?? [];
  if (source !== cachedSource) {
    cachedSource = source;
    cachedProtectedTypes = new Set(source.map((t) => String(t).trim().toLowerCase()));
  }
  return cachedProtectedTypes.has(String(taskType ?? "").trim().toLowerCase());
}

let cachedWarnSource: readonly string[] | undefined;
let cachedWarnedTypes: ReadonlySet<string> = new Set<string>();

/**
 * True when deleting this task TYPE is allowed but should be confirmed by name — the
 * reminder/remainder chasers. Deletable by ruling, but a bulk delete must not sweep them
 * up silently. Same lazy/mock-safe build as isTaskTypeDeleteProtected above.
 */
export function isTaskTypeDeleteWarned(taskType: string | null | undefined): boolean {
  const source: readonly string[] = deleteWarningTasks ?? [];
  if (source !== cachedWarnSource) {
    cachedWarnSource = source;
    cachedWarnedTypes = new Set(source.map((t) => String(t).trim().toLowerCase()));
  }
  return cachedWarnedTypes.has(String(taskType ?? "").trim().toLowerCase());
}

/**
 * Project lifecycle statuses from Pending Contract Approval through the rest of the
 * project's life — the point at which a Create BOM Task's bill of materials has become
 * the project's binding record and must stop being deletable from the Gantt. An explicit
 * ALLOW-LIST (not a "not before" negation): an unrecognized/missing status is deliberately
 * NOT locked, matching isBeforeEstimateApproval's convention that an unknown status must
 * not silently change existing behavior. Normalised the same way as
 * PRE_ESTIMATE_APPROVAL_STATUSES — lowercase with separators stripped — because this
 * tenant stores both spaced and screaming-snake variants of the same status.
 */
const POST_CONTRACT_APPROVAL_STATUSES: ReadonlySet<string> = new Set([
  "contractapproval",
  "projectactive",
  "payment",
  "pogeneration",
  "paymentapproval",
  "constructioncomplete",
  "projectcomplete",
  "projectclosed",
  "projectinactive",
  "onhold",
  "paymentskipped",
]);

export function isResourceAssignmentLocked(
  projectStatus: string | null | undefined,
  isSalesPhaseTask: boolean
): boolean {
  if (isSalesPhaseTask) return false;
  return !POST_CONTRACT_APPROVAL_STATUSES.has(norm(projectStatus).replace(/[\s_-]/g, ""));
}

/** The two task types that anchor a PO/BOM group (Create BOM Task, Miscellaneous Service). */
const ANCHOR_TASK_TYPES: ReadonlySet<string> = new Set([CREATE_BOM_TASK, MISCELLANEOUS_SERVICE]);

/**
 * True when a PO/BOM group ANCHOR task (Create BOM Task or Miscellaneous Service) may
 * not have its BOM/Misc data added to, edited, or deleted, and may not be DELETED as a
 * task, because the project has entered Pending Contract Approval and moved on into
 * its build/billing lifecycle — by then the BOM/service it defines has become part of
 * the project's binding record, so changing or removing it would corrupt that record
 * out from under contracts, purchase orders and billing already built on it. Covers
 * BOTH anchor types identically — a Miscellaneous Service group is locked the same way
 * a Create BOM Task group is, same lifecycle cutoff, same reasoning. False for any
 * other task type and false for a null/undefined task or an unrecognized project
 * status (see POST_CONTRACT_APPROVAL_STATUSES).
 *
 * This is the ANCHOR-ONLY check — it does not resolve PO/BOM group membership. Delete
 * additionally blocks a PO-stage sibling too, because deleting one cascades the anchor
 * out with it (see isDeleteBlockedByBomLock).
 *
 * CORRECTED — does NOT lock the task's Edit affordance or its general fields anymore.
 * Product feedback reversed the first pass (which folded this into canEditTaskFields
 * and disabled the grid/header Edit icons): Edit stays enabled and every other tab
 * stays editable at every lifecycle stage. Only the anchor's own BOM tab (Create BOM
 * Task) or Misc tab (Miscellaneous Service) is locked once this is true — wired via
 * useTaskDialog's `bomTabLocked` into that one tab's `ro`/`addOnly`, with
 * BOM_TAB_LOCKED_MESSAGE / MISC_TAB_LOCKED_MESSAGE shown as the reason. A sibling
 * PO-stage task (Approve/Confirm/Verify/Receive/Create PO Task) was never covered by
 * this check and stays fully editable, same as before.
 */
export function isBomAnchorLocked(
  task: EditableTaskLike | null | undefined,
  projectStatus?: string | null
): boolean {
  if (!task) return false;
  if (!ANCHOR_TASK_TYPES.has(task.taskType ?? "")) return false;
  return POST_CONTRACT_APPROVAL_STATUSES.has(norm(projectStatus).replace(/[\s_-]/g, ""));
}

/**
 * ONE shared warning message for every Delete-lock entry point (Toolbar.tsx,
 * BulkBar.tsx, ConfirmDialog.tsx) — kept generic on purpose: the task the user
 * actually clicked Delete on may be the anchor itself (Create BOM Task /
 * Miscellaneous Service) or any PO-stage sibling of it (see
 * isDeleteBlockedByBomLock), so the message must read correctly for either case.
 *
 * CORRECTED — Edit is no longer blocked at this level. The Edit affordance (grid
 * icon, header button) always stays enabled; only the anchor's own BOM/Misc dialog
 * tab is locked once the project is Pending Contract Approval or later — see
 * BOM_TAB_LOCKED_MESSAGE / MISC_TAB_LOCKED_MESSAGE below. This message therefore
 * names Delete only.
 */
export const BOM_ANCHOR_LOCKED_MESSAGE =
  "This task cannot be deleted. Create BOM Task and Miscellaneous Service " +
  "tasks — along with their linked purchase order tasks — are locked once the project " +
  "reaches Pending Contract Approval, and stay locked for the rest of the project.";

/**
 * User-facing warning shown ON the dialog's BOM tab (Create BOM Task) when
 * `isBomAnchorLocked` is true for the task being viewed — the BOM has become the
 * project's binding record and may not be added to, edited, or removed from here.
 * The task's other fields/tabs are NOT locked by this — see canEditTaskFields'
 * own comment for why the anchor lock no longer folds into the whole-task gate.
 */
export const BOM_TAB_LOCKED_MESSAGE = "BOM editing is not allowed at this project stage.";

/** Same warning as BOM_TAB_LOCKED_MESSAGE, worded for a Miscellaneous Service anchor's Misc tab. */
export const MISC_TAB_LOCKED_MESSAGE = "Service editing is not allowed at this project stage.";

/** A task shape carrying enough to resolve PO/BOM group membership (see resolveBomGroupAnchorUid). */
export interface AnchorResolvableTask extends EditableTaskLike {
  dependencies?: { predecessorUid: string }[] | null;
}

/**
 * Every task type that can be a member of a PO/BOM group — the anchor types plus the
 * five PO-stage types (Create/Approve/Confirm/Receive/Verify PO). Gates the dependency
 * walk below: a task's OWN type must be one of these before its `dependencies` are even
 * inspected for an anchor. Without this gate, an ordinary schedule dependency on a
 * Create BOM Task (e.g. "Create Estimate" simply scheduled after it, with no PO/BOM
 * relationship at all) would be misread as PO/BOM group membership — found live: the
 * BOM delete-lock warning fired for "Create Estimate" because it happened to carry the
 * Create BOM Task as a predecessor.
 */
const PO_BOM_TASK_TYPES: ReadonlySet<string> = new Set([...BOM_GROUP_TASKS, CREATE_BOM_TASK, MISCELLANEOUS_SERVICE]);

/**
 * Resolves the PO/BOM group anchor (Create BOM Task or Miscellaneous Service) that
 * `seedUid` belongs to — itself if it IS an anchor type, or the anchor referenced in
 * its own `dependencies` if it's a PO-stage sibling (Create/Approve/Confirm/Receive/
 * Verify PO — every PO-stage task carries its group's anchor as one of its own
 * `dependencies` entries; there is no link from the anchor back to its PO tasks).
 * Returns null when the task isn't part of a PO/BOM group at all — including any task
 * whose own type isn't a PO/BOM group member, REGARDLESS of what its `dependencies`
 * happen to contain (see PO_BOM_TASK_TYPES's comment for why that gate is required).
 *
 * ONE canonical resolver — shared with ConfirmDialog.tsx's own PO/BOM group display
 * logic, which used to keep an independent copy of this same walk. Two copies is how
 * a lock keyed only on a task's own type went undetected for a PO-stage sibling: the
 * backend's delete pipeline cascades a sibling's delete into removing the whole group
 * INCLUDING its anchor (task_handlers.py's PO-group resolution), so any lock that
 * only asks "is this exact selected task a Create BOM Task" misses every sibling.
 */
export function resolveBomGroupAnchorUid(
  seedUid: string,
  tasksByUid: Map<string, AnchorResolvableTask>
): string | null {
  const seed = tasksByUid.get(seedUid);
  if (!seed) return null;
  if (ANCHOR_TASK_TYPES.has(seed.taskType ?? "")) return seedUid;
  if (!PO_BOM_TASK_TYPES.has(seed.taskType ?? "")) return null;

  for (const dep of seed.dependencies ?? []) {
    if (ANCHOR_TASK_TYPES.has(tasksByUid.get(dep.predecessorUid)?.taskType ?? "")) {
      return dep.predecessorUid;
    }
  }
  return null;
}

/**
 * True when deleting `uid` must be blocked because it — or the Create BOM Task anchor
 * of the PO/BOM group it belongs to — is locked (see isBomAnchorLocked). Use
 * this (not isBomAnchorLocked directly) for any delete-permission decision over
 * a real selection, so a PO-stage sibling's delete is blocked exactly when deleting it
 * would cascade the locked anchor out with it.
 */
export function isDeleteBlockedByBomLock(
  uid: string,
  tasksByUid: Map<string, AnchorResolvableTask>,
  projectStatus?: string | null
): boolean {
  const anchorUid = resolveBomGroupAnchorUid(uid, tasksByUid) ?? uid;
  return isBomAnchorLocked(tasksByUid.get(anchorUid), projectStatus);
}

/**
 * True when a task's schedule (start, end, duration) must not be edited — SNOOZED and
 * PARKED tasks are owned by the snooze lifecycle until it resolves; manually shifting
 * their dates would break the SZ10 park/unpark sequencing.
 */
export function isTaskScheduleLocked(status: TaskStatus | string | null | undefined): boolean {
  return DELETE_LOCKED_STATUSES.has(norm(status));
}

/** Task is currently being executed (clocked-in / in progress). */
export function isTaskActive(status: TaskStatus | string | null | undefined): boolean {
  return norm(status) === TASK_STATUS.ACTIVE;
}

/** The BOM-creation task — the only active task whose BOM may still be added to. */
export function isCreateBomTaskType(taskType: string | null | undefined): boolean {
  return (taskType ?? "") === CREATE_BOM_TASK;
}

/**
 * Whether the project's own lifecycle status makes the entire Gantt view-only —
 * independent of any individual task's status. Case-insensitive; true only for the
 * four blocked lifecycle statuses (On Hold / Project Closed / Project Complete /
 * Project Inactive), false for null/undefined/empty/any other status (e.g. "Active",
 * "bidReview"). Single source of truth for this check — callers should use this
 * rather than re-checking the literal list.
 */
export function isProjectEditLocked(status: string | null | undefined): boolean {
  return BLOCKED_PROJECT_STATUSES.has(norm(status));
}

/**
 * "By Project" is a read-only overview of the multi-project board — it shows every
 * project's tasks grouped by project but is not the surface tasks are edited from
 * (that's Single Job or Sequential). True only in multi-project mode with groupMode
 * "byProject"; false for single-job and for Sequential grouping.
 */
export function isByProjectViewReadOnly(
  mode: "single" | "multi",
  groupMode: "byProject" | "sequential"
): boolean {
  return mode === "multi" && groupMode === "byProject";
}

/**
 * Project lifecycle statuses that sit BEFORE the estimate is approved.
 *
 * Normalised to lowercase with separators stripped, because this tenant genuinely stores
 * both `awaitingEstimation` and `AWAITING_ESTIMATION` — a plain lowercase compare would
 * silently miss the screaming-snake variant and treat those projects as post-approval.
 */
const PRE_ESTIMATE_APPROVAL_STATUSES: ReadonlySet<string> = new Set([
  "draft",
  "awaitingestimation",
  "bidreview",
]);

/**
 * True while the project is still pre-sale — before the estimate has been approved.
 *
 * Only the SALES phase is real work at this point; preConstruction/activeConstruction/
 * postConstruction tasks are a projection of a job that has not been sold, so resource
 * assignment must not write to them (AB#27997). Once the estimate is approved
 * (Contract Approval → PO Generation → Payment → …) every phase is live and in scope.
 *
 * Deliberately NOT used to gate the ribbon's warnings, previews or count badges — those
 * keep reporting across all phases so the user still sees the whole picture; this governs
 * only which tasks an "Apply to tasks" is allowed to WRITE.
 *
 * Unknown/absent status → false (treat as post-approval): the ribbon's pre-existing
 * behaviour is all-phases, so an unrecognised status must not silently start skipping
 * writes the user is expecting.
 */
export function isBeforeEstimateApproval(status: string | null | undefined): boolean {
  return PRE_ESTIMATE_APPROVAL_STATUSES.has(norm(status).replace(/[\s_-]/g, ""));
}

/**
 * Whether the task's general fields may be edited — task name, dates, duration,
 * resources/assignees, type, dependencies, and the Info/Resource/Tools/Misc/Action
 * dialog tabs. Locked when the task is a group/phase row, backend-protected
 * (isUnmodifiable), finished, active, or left with unconfirmed dates by the last
 * schedule run (attribute 567 — see isTaskProvisional). (An active Create BOM Task is
 * still locked here — its ONLY editable surface is the BOM tab in add-only mode; see
 * isBomAddOnly.) `projectStatus` additionally locks editing for the whole project
 * when it is one of the blocked lifecycle statuses (see isProjectEditLocked) —
 * optional and defaulting to undefined so existing single-argument call sites are
 * unaffected (they simply never see that lock fire).
 *
 * WHY THE PROVISIONAL SHIELD LIVES HERE, AND NOT IN A NEW MECHANISM. This function is
 * already the single gate every editing surface asks before letting anything change:
 * the grid's inline cells (GanttGridV2 isRowEditableBase), the task dialog's `ro`
 * (useTaskDialog), and the chart bar's drag/resize (ganttAdapter `editable`). Adding
 * one condition here shields every surface at once, per task, and leaves every other
 * row on the board fully editable.
 *
 * WHAT IS DELIBERATELY NOT HERE (ANYMORE): the Create BOM Task / Miscellaneous
 * Service anchor lock (isBomAnchorLocked). CORRECTED — a first pass folded that lock
 * in here too, which blocked the WHOLE task (every field, every tab, and the Edit
 * affordance itself) once the project reached Pending Contract Approval. Product
 * feedback reversed that: Edit must stay enabled and the task's general fields must
 * stay editable at every lifecycle stage — only the anchor's own BOM tab (or Misc
 * tab, for a Miscellaneous Service anchor) may not be added to, edited, or removed
 * from once the project is Pending Contract Approval or later, because that is the
 * data that has become the project's binding record. That narrower lock is applied
 * directly to the BOM/Misc tab's own `ro`/`addOnly` props in TaskDialogView (see
 * useTaskDialog's `bomTabLocked`), not here — folding it back into this function
 * would re-lock the whole task again, which is exactly the behavior being reversed.
 *
 * WHAT IS DELIBERATELY NOT HERE either: the doc 19 schedule lock ("a solve is
 * re-planning this board right now"). That one is a property of the PROJECT for a
 * couple of seconds, not of a task, so it is applied one layer up as `readOnly` in
 * ganttBridge's useGantt — which every caller here already checks
 * (`!readOnly && canEditTaskFields(...)`) and which additionally noops the mutating
 * actions. Adding it as an argument here would mean two switches for one condition.
 *
 * Note what is deliberately NOT changed either: `isBomAddOnly`. Adding material or stock to
 * an already-started Create BOM Task does not aim at a date, so a stale date cannot
 * spoil it — and in practice a started task is never in the frozen set the engine
 * stamps anyway (started work is excluded before the freeze).
 */
export function canEditTaskFields(
  task: EditableTaskLike | null | undefined,
  projectStatus?: string | null
): boolean {
  if (!task) return false;
  if (isProjectEditLocked(projectStatus)) return false;
  if (task.isGroup) return false;
  if (task.isUnmodifiable) return false;
  if (isTaskDone(task.status)) return false;
  if (isTaskActive(task.status)) return false;
  if (isTaskProvisional(task)) return false;
  return true;
}

/**
 * Whether a GROUP row's own NAME (only) may be renamed — the one exception to
 * canEditTaskFields' blanket `isGroup` lock.
 *
 * Gated by LEVEL, not by isSyntheticGroup: a Level 0 group (a top-level phase —
 * its own parent is the project directly, e.g. "preConstruction") always stays
 * fully locked, no exceptions. A Level 1 group (its own parent IS a Level-0
 * phase — e.g. "Roof Cleaning," or a Promote/Demote/Create-Group synthetic
 * group) may have its name edited — real or synthetic, both are treated the
 * same at Level 1. A group nested two or more levels down (its parent is
 * itself a group, not a phase) is NOT Level 1 and stays locked too — only the
 * level immediately below a phase qualifies.
 *
 * `parentIsPhase` must be resolved by the caller via hierarchyMoveRules.isPhase
 * applied to THIS task's own parent (not to the task itself) — that is the only
 * correct way to distinguish Level 1 from Level 2+: `parent.isGroup` alone
 * cannot tell them apart, since a Level-2 group's parent is also a group.
 * This module intentionally stays tree-agnostic otherwise (see EditableTaskLike).
 *
 * All other fields (dates, duration, resources, dependencies, type) remain
 * locked regardless — this governs `taskName` only. Same lifecycle/lock gating
 * as canEditTaskFields (project lock, isUnmodifiable, finished, active) still
 * applies.
 *
 * HOW THIS COMPOSES WITH THE TWO LOCKS canEditTaskFields ALSO ANSWERS TO:
 *   - Provisional shield (attribute 567): deliberately NOT repeated here, because it
 *     could never fire. isTaskProvisional short-circuits to false for any `isGroup`
 *     row (provisionalShield.ts) — a header has no dates of its own to protect — and
 *     this function returns false for anything that is NOT a group. The two
 *     predicates are disjoint by construction, so a shield check here would be dead
 *     code, not extra safety.
 *   - Doc 19 schedule lock ("a solve is re-planning this board right now"): applied
 *     one layer up as `readOnly` from ganttBridge's useGantt, exactly as it is for
 *     canEditTaskFields — callers gate on `!readOnly && canEditGroupName(...)`. It is
 *     a property of the project for a couple of seconds, not of a row, so it stays a
 *     single switch there rather than a second argument here.
 */
export function canEditGroupName(
  task: EditableTaskLike | null | undefined,
  parentIsPhase: boolean,
  projectStatus?: string | null
): boolean {
  if (!task) return false;
  if (isProjectEditLocked(projectStatus)) return false;
  if (!task.isGroup) return false;
  if (!parentIsPhase) return false;
  if (task.isUnmodifiable) return false;
  if (isTaskDone(task.status)) return false;
  if (isTaskActive(task.status)) return false;
  return true;
}

/**
 * The minimal schedule-lock shape this module reads. Declared structurally rather than
 * imported from ScheduleLockService so this rules module stays free of the service
 * layer — the same reason EditableTaskLike exists instead of importing GanttTask.
 */
export interface RowScheduleLockLike {
  state?: string | null;
}

/** The answer sequentialRowProjectLock gives: locked, plus WHY, ready to show. */
export interface SequentialRowLock {
  locked: boolean;
  /** User-facing reason, empty string when not locked (a Tooltip title of "" shows nothing). */
  reason: string;
}

const NOT_LOCKED: SequentialRowLock = { locked: false, reason: "" };

/**
 * PER-ROW project lock for the flat, cross-project Sequential view.
 *
 * WHY A PER-ROW CHECK IS NEEDED AT ALL. Everywhere else the Gantt asks the AMBIENT
 * `readOnly` from useGantt(), which folds together the ASE processing lock, the doc-19
 * schedule lock and the project's lifecycle status. In Sequential that flag is
 * structurally blind: `isProcessing` is always false there (no ProjectSchedulingProvider
 * is mounted for a board that is not a single board), and `projectStatus` is never
 * passed, so `isProjectEditLocked(projectStatus)` reads undefined and answers false. A
 * row belonging to an On-Hold project therefore looks perfectly editable. The per-row
 * data to answer honestly does exist — `task.projectStatus` from the Sequential adapter,
 * and `lockFor(projectUid)` from the container's ONE useScheduleLocks instance — it was
 * simply never asked, because the container collapses every project's lock into a single
 * `visibleLock` before the bridge sees it.
 *
 * TWO CAUSES, ONE ANSWER, in this order:
 *   1. LIFECYCLE — On Hold / Project Closed / Project Complete / Project Inactive. A
 *      durable state of the board; checked first because it is the more fundamental
 *      "this project is not being worked on" and its wording is the more useful one.
 *   2. SCHEDULE LOCK (doc 19) — a solve is re-planning that board for a few seconds.
 *      Only READ_ONLY blocks; SOFT is a warning that deliberately stays editable
 *      (see scheduleLockCopy), so treating it as locked here would freeze boards the
 *      lock design explicitly keeps open.
 *
 * `lockFor` is optional so callers without a lock source (tests, and any surface that
 * only has lifecycle data) still get the lifecycle half rather than nothing.
 */
export function sequentialRowProjectLock(
  task: { projectUid?: string | null; projectStatus?: string | null } | null | undefined,
  lockFor?: ((projectUid: string | null | undefined) => RowScheduleLockLike | null) | null
): SequentialRowLock {
  if (!task) return NOT_LOCKED;

  if (isProjectEditLocked(task.projectStatus)) {
    // Echo the project's own status back rather than a generic phrase — "This project is
    // On Hold" tells the user which of the four states they are looking at, and so what
    // they would have to change to proceed.
    return {
      locked: true,
      reason: `This project is ${String(task.projectStatus ?? "").trim()} — tasks cannot be added.`,
    };
  }

  const lock = lockFor?.(task.projectUid) ?? null;
  if (lock?.state === "READ_ONLY") {
    return {
      locked: true,
      reason: "This project is being re-planned right now — try again in a few seconds.",
    };
  }

  return NOT_LOCKED;
}

/**
 * Whether the BOM tab should run in ADD-ONLY mode: the user may add vendor material
 * or stock items, but may not edit or delete existing rows (or remove vendor groups).
 * True only for an ACTIVE "Create BOM Task". Finished BOM tasks are fully locked;
 * not-yet-started BOM tasks get full BOM editing (canEditTaskFields is true).
 * False whenever the project itself is locked (see isProjectEditLocked) — no adding
 * material to a Closed/On Hold/Complete/Inactive project either.
 */
export function isBomAddOnly(
  task: EditableTaskLike | null | undefined,
  projectStatus?: string | null
): boolean {
  if (!task) return false;
  if (isProjectEditLocked(projectStatus)) return false;
  if (task.isGroup || task.isUnmodifiable) return false;
  return isTaskActive(task.status) && isCreateBomTaskType(task.taskType);
}

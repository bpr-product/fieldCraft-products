// ganttV2/lib/apiAdapter.ts — maps getProjectTasks API response → GanttTask[] (or Task[] for
// non-Gantt consumers via adaptApiTasksToTask)

import { Dependency, GanttTask, TaskStatus, TASK_STATUS, Task } from "../types";
import { ApiDependencyLink, dependenciesFromLinks, parseDependencyToken } from "./dependencyLinks";
import { isTaskDeleteLocked, isTaskTypeDeleteProtected } from "./taskEditPermissions";
import { TODAY } from "../../ganttV2/lib/time";
import { readProvisionalSince } from "./provisionalShield";
import { parseNotes } from "../dialogs/notesUtils";
// ── Raw API shapes ──────────────────────────────────────────────────────────

export interface ApiAssignee {
  name: string;
  role?: string;
  resourceUid?: string;
  email?: string;
  type?: string;  // "W2" | "1099"
  designations?: string[];  // all roles from the API (designation[])
  /**
   * "a human chose this person", not the solver. Written into attribute 502 alongside the
   * assignee and read by the engine at shared/service/schedule_writer.py:5333-5340, which
   * SKIPS its 502 refresh for a task carrying a manual entry — i.e. this flag is what stops
   * the next solve from silently replacing the pick. It must therefore survive every
   * client-side rewrite of the assignee list; see toAssigneeWire in ResourceTab.
   */
  manual?: boolean;
}

interface ApiPhaseGroup {
  phase: string | null;
  taskViewData: ApiTask[];
}

interface ApiTask {
  id: number;
  taskUid: string;
  parentTaskUid: string | null;
  isMilestone?: boolean;
  isCritical?: boolean;
  taskName: string;
  taskType: string | null;
  phase: string | null;
  status: string | null;
  startTime: string | null;
  endTime: string | null;
  /** Baseline — raw SCHEDULED_START (526), frozen at the task's first solve. Null until a solve has run. */
  scheduledStart?: string | null;
  /** Baseline — raw SCHEDULED_END (527), frozen at the task's first solve. Null until a solve has run. */
  scheduledEnd?: string | null;
  /**
   * PROVISIONAL marker — raw PROVISIONAL_SINCE (567). Set by the engine on the tasks
   * it could NOT re-validate because they fell beyond the scheduling window; cleared
   * on the next successful re-plan. Both spellings are accepted because this attribute
   * is surfaced by the ASE (Python, snake_case) side while the rest of this payload
   * comes from TaskServiceV3 (Java, camelCase) — whichever layer ends up flattening
   * attribute 567 onto the task view, the adapter reads it without a second change.
   * Undefined on every response until that mapping lands server-side; the UI treats
   * undefined exactly like "not provisional" (see lib/provisional.ts).
   */
  provisionalSince?: string | null;
  provisional_since?: string | null;
  estimatedHours: number | null;
  /** task.min_workers (289) — minimum crew size this task needs concurrently. */
  minWorkers?: number | null;
  /** task.max_workers (290) — maximum crew size this task can use concurrently. */
  maxWorkers?: number | null;
  assignees: string | null;
  /** Names HELPING this task (R8) — already an array from TaskViewData.helpers, unlike
   *  `assignees`, which arrives as a JSON string. Absent on older responses. */
  helpers?: string[] | null;
  /**
   * RESOURCES (attr 500) JSON — what the task NEEDS, e.g.
   * `[{"group":"admin","type":"1099","count":"1"}]`. Already returned by TaskViewData; the
   * adapter simply never read it before. See parseRequiredRoles (AB#27997).
   */
  resources?: string | null;
  dependencies: string | null;
  /** 293/558/565 per link (TaskViewData.dependencyLinks): type, lag hours, HARD/SOFT, basis. */
  dependencyLinks?: ApiDependencyLink[] | null;
  resolvedDependencies?: string | null;
  percentage: number | null;
  taskOrder?: number;
  uiSequenceId?: number;
  /** Dot-decimal WBS string ("1", "1.1", ...), computed server-side in TaskServiceV3 (v3 only). */
  wbs?: string;
  subtasks?: ApiTask[];
  boms?: any[];
  /** Miscellaneous Service anchor's subcontractor/service selection — see Task.projectResources. */
  projectResources?: any[];
  allocatedUnit?: any;
  notes?: string;
  hasOpenNote?: boolean;
  checklistItems?: any[];

}

// ── Helpers ─────────────────────────────────────────────────────────────────

// Matches case-insensitively (backend/DB casing can vary — e.g. TaskStatus.java's
// own REJECTED = "Rejected") but always returns the canonical TASK_STATUS value.
// No FORCE_COMPLETED case: that status doesn't exist on the backend at all — a
// forced completion is written as plain "completed" (see TaskStatus.java).
// CANCELLED (AB#29654) IS a real backend value (TaskStatus.CANCELLED) — a
// force-completed project cancels its still-open tasks and this must render as
// Cancelled, not fall through to the default.
// Exported: also used by lib/sequentialTaskAdapter.ts, which maps a different
// API shape (SequentialTask) onto the same GanttTask contract.
export function normalizeStatus(raw: string | null): TaskStatus {
  if (!raw) return TASK_STATUS.SCHEDULED;
  switch (raw.toUpperCase()) {
    case "ACTIVE":    return TASK_STATUS.ACTIVE;
    // The engine's STARTED / DELETE_LOCKED sets treat "in_progress" / "inprogress" as
    // started work (task_status.py). In progress IS active for every frontend rule —
    // edit lock, group-delete lock (AB#30316), bar paint — so map it there instead of
    // letting it fall through to SCHEDULED (PR 8471 review).
    case "IN_PROGRESS":
    case "INPROGRESS": return TASK_STATUS.ACTIVE;
    case "COMPLETED": return TASK_STATUS.COMPLETED;
    case "SKIPPED":   return TASK_STATUS.SKIPPED;
    case "CANCELLED": return TASK_STATUS.CANCELLED;
    case "BLOCKED":   return TASK_STATUS.BLOCKED;
    case "DELAYED":   return TASK_STATUS.DELAYED;
    case "SNOOZED":   return TASK_STATUS.SNOOZED;
    case "PARKED":    return TASK_STATUS.PARKED;
    default:          return TASK_STATUS.SCHEDULED;
  }
}

export function parseAssignees(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const parsed: ApiAssignee[] = JSON.parse(raw);
    return parsed.map((a) => a.name).filter(Boolean);
  } catch {
    return [];
  }
}

export function parseAssigneeObjects(raw: string | null): ApiAssignee[] {
  if (!raw) return [];
  try {
    return (JSON.parse(raw) as ApiAssignee[]).filter((a) => Boolean(a.name));
  } catch {
    return [];
  }
}

/**
 * Role ids a task REQUIRES, from RESOURCES (attr 500) — e.g.
 * `[{"group":"admin","type":"1099","count":"1"}]` → `["admin"]`.
 *
 * This is what the task NEEDS, which is a different fact from who is currently on it
 * (ASSIGNEES, attr 502) and survives the last assignee being removed (AB#27997). It is what
 * lets a re-added Admin find the tasks an Admin was removed from — before the requirement was
 * preserved server-side, that record died with the assignment and those tasks stayed empty
 * forever.
 *
 * Deduped, blank-stripped, and NOT case-normalised: role ids are compared against the
 * ribbon's own camelCase ids (`purchaseOfficer`), and the backend is known to lowercase them
 * on some write paths — callers must compare case-insensitively rather than rely on this.
 */
function parseRequiredRoles(raw: string | null | undefined): string[] {
  if (!raw) return [];
  try {
    const entries = JSON.parse(raw) as Array<{ group?: unknown }>;
    if (!Array.isArray(entries)) return [];
    const groups = entries
      .map((e) => (typeof e?.group === "string" ? e.group.trim() : ""))
      .filter(Boolean);
    return Array.from(new Set(groups));
  } catch {
    return [];
  }
}

/**
 * Total crew size a task NEEDS, summed across RESOURCES (attr 500) entries — e.g.
 * `[{"group":"admin","count":"1"},{"group":"labour","count":"3"}]` -> 4 (AB#33174).
 *
 * Mirrors BlockerSeatResolver.readCrew (Java) decision for decision, so GoPAL's seat count
 * and the Gantt's crew badge never disagree: unreadable/absent/non-array -> null (unknown,
 * never a statement); an explicitly empty array -> 0 ("needs nobody"); a malformed entry
 * still asks for a body (counted as 1); an unparseable or blank count is 1, never 0; a
 * negative count clamps to 0. Summed, not deduped — parseRequiredRoles answers "which
 * roles", this answers "how many bodies".
 */
function parseRequiredCrewCount(raw: string | null | undefined): number | null {
  if (!raw) return null;
  let entries: unknown;
  try {
    entries = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!Array.isArray(entries)) return null;
  if (entries.length === 0) return 0;

  let total = 0;
  for (const entry of entries) {
    if (entry === null || typeof entry !== "object") {
      total += 1; // malformed entry — still asks for a body, never lose a seat
      continue;
    }
    const rawCount = (entry as { count?: unknown }).count;
    const countStr =
      typeof rawCount === "string" ? rawCount.trim() :
      typeof rawCount === "number" ? String(rawCount) : "";
    if (!countStr) {
      total += 1; // no/blank count — one seat, not zero
      continue;
    }
    // Java's Integer.parseInt rejects the WHOLE string and anything outside int range, both
    // of which land on readCrew's "unreadable count = one seat". parseInt alone stops at the
    // first non-digit ("2abc" -> 2) and has no range at all ("99999999999" -> 99 billion).
    const parsed = /^[+-]?\d+$/.test(countStr) ? parseInt(countStr, 10) : NaN;
    const inIntRange = parsed >= -2147483648 && parsed <= 2147483647;
    total += Number.isNaN(parsed) || !inIntRange ? 1 : Math.max(parsed, 0);
  }
  return total;
}

export function parseDependencies(raw: string | null): Dependency[] {
  if (!raw) return [];
  try {
    const items: string[] = JSON.parse(raw);
    // "<uuid>", "<uuid>SS+2h", "<uuid>FS-1h" — type and whole-hour lag, as the engine reads them.
    return items.map((entry) => {
      const link = parseDependencyToken(entry);
      return { predecessorUid: link.predecessorUid, type: link.type, lagMin: link.lagHours * 60 };
    });
  } catch {
    return [];
  }
}

export function isoToMin(iso: string | null): number {
  if (!iso) return 0;
  // Parse the true UTC instant (honor the Z). minToDate/getHours then render it in the
  // viewer's browser zone, so the bar sits at the viewer's local time — matching the grid
  // columns. (Was: strip the Z and parse as local — the pre-migration "fake-Z" convention,
  // which showed raw UTC digits once storage became real UTC.)
  return (new Date(iso).getTime() - TODAY.getTime()) / 60000;
}

function isoToDateStr(iso: string | null): string {
  if (!iso) return "";
  return iso.slice(0, 10); // "YYYY-MM-DD"
}


// ── Main adapter ─────────────────────────────────────────────────────────────

/** The subset of ApiTask fields that are real business data, independent of tree position. */
function mapTaskBase(t: ApiTask, projectUid: string, projectName: string, phase: string): Task {
  const durationMin = (t.estimatedHours ?? 0) * 60;
  return {
    taskUid:          t.taskUid,
    projectUid,
    projectName,
    parentTaskUid:    t.parentTaskUid ?? null,
    taskId:           t.uiSequenceId ?? t.id,
    taskName:         t.taskName,
    taskType:         t.taskType ?? "",
    // Bind every row — parent (summary / phase-header) rows included — to its real
    // backend status, instead of the old synthetic TASK_STATUS.GROUP that discarded the
    // server status (so the Gantt could never reflect e.g. a parent going ACTIVE when a
    // child clocks in). The parent/leaf TREE distinction is carried separately by isGroup,
    // so grouping/edit-gating (which keys off isGroup) is unchanged. NOTE: status-based
    // gates (isTaskDone / isTaskActive → selection, bulk actions, Skip enablement) now see
    // a parent's REAL status — intended: a completed/active summary row behaves per its
    // true state rather than the inert "group" value.
    status:           normalizeStatus(t.status),
    phase,
    // v3 computes wbs server-side (TaskServiceV3.assignWbs) from the resolved parent/child
    // tree. Fall back to the flat sequence id when the API omits it (v2 responses).
    wbs:              t.wbs ?? String(t.uiSequenceId ?? t.id),
    assignees:        parseAssignees(t.assignees),
    assigneeObjects:  parseAssigneeObjects(t.assignees),
    // Already a plain array of names from the API (TaskViewData.helpers) — no parse step,
    // unlike assignees, which arrives as a JSON string.
    helpers:          Array.isArray(t.helpers) ? t.helpers.filter(Boolean) : [],
    requiredRoles:    parseRequiredRoles(t.resources),
    requiredCrewCount: parseRequiredCrewCount(t.resources),
    minWorkers:       t.minWorkers ?? null,
    maxWorkers:       t.maxWorkers ?? null,
    resourceType:     "",
    crewId:           "",
    estimatedMin:     durationMin,
    durationMin,
    timeSpent:        0,
    startTime:        t.startTime ?? "",
    endTime:          t.endTime ?? "",
    startDate:        isoToDateStr(t.startTime),
    endDate:          isoToDateStr(t.endTime),
    progressPct:      t.percentage ?? 0,
    isMilestone:      t.isMilestone ?? false,
    // Was hardcoded false, so the grid's note indicator never lit up for a task
    // with real, saved notes — derive it from the same `notes` field the Notes
    // tab already parses (notesUtils.parseNotes), instead of duplicating a
    // second note-detection rule.
    hasOpenNote:      parseNotes(t.notes).length > 0,
    dependencies:     t.dependencyLinks ? dependenciesFromLinks(t.dependencyLinks) : parseDependencies(t.dependencies),
    version:          1,
    boms:             t.boms ?? [],
    projectResources: t.projectResources ?? [],
    allocatedUnit:    t.allocatedUnit,
    notes:            t.notes,
    checklistItems:   t.checklistItems ?? [],
    // Attribute 567 — normalised in ONE place, `readProvisionalSince`, which accepts
    // BOTH casings (the attribute is surfaced by ASE in snake_case while the rest of
    // this payload comes from TaskServiceV3 in camelCase) and returns null for an
    // absent, blank or non-string value. Blank matters: the engine CLEARS the flag by
    // writing an EMPTY value, so "" means confirmed and must not read as provisional.
    // Both consumers — the grey-out (lib/provisional.ts) and the edit shield
    // (lib/provisionalShield.ts) — then test one already-clean field.
    provisionalSince: readProvisionalSince(t),
  };
}

/**
 * Converts getProjectTasks() response into a flat GanttTask[].
 *
 * No grouping is done here — parentTaskUid from the API is preserved directly.
 * Tasks whose parentTaskUid is null are wired to projectUid (top-level).
 * The ganttV2 store builds the visible tree from parentTaskUid relationships.
 * isGroup is true when a task has subtasks (i.e. it is a parent in the API tree).
 */
export function adaptApiTasks(
  phases: ApiPhaseGroup[],
  projectUid: string,
  projectName: string
): GanttTask[] {
  const result: GanttTask[] = [];

  function mapTask(t: ApiTask, inheritedPhase: string) {
    const phase = t.phase ?? inheritedPhase;
    const children = t.subtasks ?? [];
    const hasChildren = children.length > 0;
    const startMin = isoToMin(t.startTime);
    const endMin   = isoToMin(t.endTime);

    result.push({
      ...mapTaskBase(t, projectUid, projectName, phase),
      startMin,
      endMin,
      // Carry the server task_order onto the GanttTask (adaptApiTasksToTask already does).
      // Without it, callers that key by task_order (e.g. the reference Gantt Preview resolving
      // a cross-phase dependency INTO a scheduled sales task) can't match — the field was
      // silently undefined here. Additive: previously undefined, now the real value.
      taskOrder:        t.taskOrder,
      isCritical:       !!t.isCritical,
      isGroup:          hasChildren,
      // Milestone-parents (parentTaskUid set + isMilestone) are deletable via cascade.
      // Only block deletion for plain group tasks (hasChildren but not a milestone-parent).
      // Also block deletion for SNOOZED/PARKED tasks — these are in a snooze lifecycle
      // and must not be removed until the snooze is resolved.
      // And for system-owned billing task TYPES (invoice chain, customer tasks) —
      // AB#28644; see isTaskTypeDeleteProtected.
      isUndeletable:    (hasChildren && !(t.parentTaskUid !== null && (t.isMilestone ?? false)))
                     || isTaskDeleteLocked(normalizeStatus(t.status))
                     || isTaskTypeDeleteProtected(t.taskType),
      isUnmodifiable:   false,
      resolvedDependencies: t.resolvedDependencies ?? undefined,
      successors:           [],
      // Baseline = SCHEDULED_START/END (526/527), frozen server-side at the task's
      // first solve — no longer a same-session snapshot. Falls back to the current
      // startMin/endMin when the task has no baseline yet (never solved, or solved
      // before the write-once fix shipped), so the ghost overlay simply shows zero
      // drift instead of a bogus comparison.
      baselineStartMin: t.scheduledStart ? isoToMin(t.scheduledStart) : startMin,
      baselineEndMin:   t.scheduledEnd ? isoToMin(t.scheduledEnd) : endMin,
      childTaskUids:    hasChildren ? children.map((c) => c.taskUid) : undefined,
    });

    for (const child of children) {
      mapTask(child, phase);
    }
  }

  for (const pg of phases) {
    const pgPhase = pg.phase ?? "General";
    for (const t of pg.taskViewData) {
      mapTask(t, pgPhase);
    }
  }

  return result;
}

/**
 * Converts getProjectTasks() response into a flat Task[] — the same real task
 * data as adaptApiTasks, but without the Gantt-only fields (startMin, isCritical,
 * baseline*, etc.), for consumers with no GanttProvider/canvas (e.g. GoPAL).
 */
export function adaptApiTasksToTask(
  phases: ApiPhaseGroup[],
  projectUid: string,
  projectName: string
): Task[] {
  const result: Task[] = [];

  function mapTask(t: ApiTask, inheritedPhase: string) {
    const phase = t.phase ?? inheritedPhase;
    const children = t.subtasks ?? [];

    result.push(mapTaskBase(t, projectUid, projectName, phase));

    for (const child of children) {
      mapTask(child, phase);
    }
  }

  for (const pg of phases) {
    const pgPhase = pg.phase ?? "General";
    for (const t of pg.taskViewData) {
      mapTask(t, pgPhase);
    }
  }

  return result;
}

interface BidTaskDTO {
  taskUid: string;
  parentTaskUid: string | null;
  taskName: string;
  taskOrder?: number;
  uiSequenceId?: number;
}

interface BidPhaseGroup {
  phase: string | null;
  tasks: BidTaskDTO[];
}

/**
 * The minimal task shape the Insert-After picker and getInsertTaskOrder's
 * sibling-order calculation actually need. Deliberately NOT `Task` — `Task`
 * requires ~20 business fields (status, dependencies, dates, ...) this list
 * never has real data for, since getTasks()/getBidTask's response is a plain
 * ordering/naming list, not full task records.
 *
 * `phase` IS carried (unlike the other omitted Task fields) because
 * useTaskDialog's create-submit handler reads refTask?.phase to inherit the
 * new task's phase from its insertion anchor. Without it, every GoPAL-created
 * task submits phase="" regardless of anchor, which desyncs it from the
 * PHASE_HEADER/phase-ordering constraints CP-SAT enforces for its FS
 * neighbors and makes the reschedule solve spuriously INFEASIBLE — reproduced
 * live on tn24047750 project 942eb86e-5da2-405a-b726-2ccd1bf9bf65 (every
 * GoPAL insert failed regardless of position; every Gantt insert, which does
 * carry phase, succeeded).
 */
export interface InsertAnchorTask {
  taskUid: string;
  projectUid?: string;
  parentTaskUid?: string | null;
  taskId?: number;
  taskOrder?: number;
  taskName: string;
  phase?: string | null;
}

/**
 * Converts getTasks()/getBidTask response (PhaseTaskDTO[] — phase + flat
 * TaskDTO[], no subtask nesting, taskOrder already on each task) into
 * InsertAnchorTask[], for GoPAL's standalone Insert-After picker.
 */
export function adaptBidTasksToInsertAnchorTasks(
  phases: BidPhaseGroup[],
  projectUid: string
): InsertAnchorTask[] {
  const result: InsertAnchorTask[] = [];
  for (const pg of phases) {
    for (const t of pg.tasks ?? []) {
      result.push({
        taskUid: t.taskUid,
        projectUid,
        parentTaskUid: t.parentTaskUid ?? null,
        taskId: t.uiSequenceId,
        taskOrder: t.taskOrder,
        taskName: t.taskName,
        phase: pg.phase ?? null,
      });
    }
  }
  return result;
}

/**
 * Widens InsertAnchorTask[] to Task[] so it can be handed to the shared
 * TaskDialogStateSource plumbing (which is typed on Task, since it also
 * serves the real Gantt page's full-data existingTask lookup). Only the
 * fields InsertAnchorTask actually carries are real; everything else is
 * Task's required-field placeholder — do not read them off a Task produced
 * here (see InsertAnchorTask's doc comment for why this list has no more).
 */
export function insertAnchorTasksToTask(tasks: InsertAnchorTask[]): Task[] {
  return tasks.map((t) => ({
    taskUid: t.taskUid,
    projectUid: t.projectUid ?? "",
    projectName: "",
    parentTaskUid: t.parentTaskUid ?? null,
    taskId: t.taskId ?? t.taskOrder ?? 0,
    taskOrder: t.taskOrder,
    taskName: t.taskName,
    taskType: "",
    status: TASK_STATUS.SCHEDULED,
    phase: t.phase ?? "",
    wbs: "",
    assignees: [],
    resourceType: "",
    crewId: "",
    estimatedMin: 0,
    durationMin: 0,
    timeSpent: 0,
    startTime: "",
    endTime: "",
    startDate: "",
    endDate: "",
    progressPct: 0,
    isMilestone: false,
    hasOpenNote: false,
    dependencies: [],
    version: 1,
  }));
}

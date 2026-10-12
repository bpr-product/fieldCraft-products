// projectScheduling/types.ts — FIELDCRAFT domain types (LAYER C).
//
// These are the construction/ASE/FIELDCRAFT-specific shapes the reusable Gantt deliberately
// does NOT know about: the task/project domain model, status enums, workflow actions,
// ASE jobs, the legacy combined GanttState, and the FIELDCRAFT VisibleRow (which carries a
// GanttTask/GanttProject). The reusable Gantt (ganttV2/*) imports only the generic
// types from ganttV2/types.ts + the generic GanttRow from ganttV2/ganttRow.ts.

import {
  ScaleKey,
  DensityKey,
  BarStyle,
  ColDef,
} from "../ganttV2/types";
import type { ApiAssignee } from "./lib/apiAdapter";
import type { DateWindow } from "./lib/dateWindow";

// Re-export the generic view types so existing FIELDCRAFT imports from this module keep
// working (and so the FIELDCRAFT layer has a single types entry point).
export type { ScaleKey, DensityKey, BarStyle, ColDef };
export type { TimeBounds } from "../ganttV2/types";

// Canonical runtime values for task status — the single source every file should
// import from instead of repeating string literals. Values matching a real backend
// status use the EXACT casing from com.fieldcraft.tenant.data.status.TaskStatus
// (lowercase — SCHEDULED/ACTIVE/COMPLETED/SKIPPED/CANCELLED) so nothing needs
// translating at the API boundary. BLOCKED/DELAYED/GROUP have no backend equivalent
// — they're computed client-side (BLOCKED/DELAYED from scheduling logic, GROUP for a
// parent row with children) — and FORCE_COMPLETED has no backend equivalent either: a
// forced completion is written as plain COMPLETED, same as a normal one (see
// forceCompleteRules.ts / FieldCraftGanttContainer.tsx). These synthetic values keep a
// lowercase_snake_case convention for consistency with the real ones.
/**
 * THE state reset for "the task dialog is no longer open".
 *
 * Shared by every closer — the dialog's own Cancel/backdrop (useTaskDialog.close) and the
 * Escape shortcut (useKeyboardShortcuts) — because these fields are a SET: the open task,
 * the insert anchors, and whether the anchor came from a row's Add menu (which decides the
 * mandatory dependency and the project pin). Escape used to clear only `openTaskUid`,
 * leaving the anchors and the row-menu flag behind, so the NEXT create — even one started
 * from the toolbar — could inherit a lock and a pinned project from a dialog the user had
 * dismissed. One definition means a new field cannot be added to the group and forgotten
 * by one of the two paths.
 *
 * Spread into actions.set(...) alongside anything else a given closer needs.
 */
export const CLOSED_TASK_DIALOG_STATE = {
  openTaskUid: null,
  insertAfterTaskUid: null,
  insertBeforeTaskUid: null,
  anchorFromRowMenu: false,
} as const;

export const TASK_STATUS = {
  SCHEDULED: "scheduled",
  ACTIVE: "active",
  COMPLETED: "completed",
  FORCE_COMPLETED: "force_completed",
  SKIPPED: "skipped",
  CANCELLED: "cancelled",
  BLOCKED: "blocked",
  DELAYED: "delayed",
  GROUP: "group",
  // Synthetic — a task shown in a preview that has NOT been scheduled yet (no dates).
  // Used by the Gantt Preview tab's reference tasks; no backend equivalent.
  UNSCHEDULED: "unscheduled",
  SNOOZED: "snoozed",
  PARKED: "parked",
} as const;

export type TaskStatus = (typeof TASK_STATUS)[keyof typeof TASK_STATUS];

export type DepType = "FS" | "FF" | "SS" | "SF";
export type DependencyClass = "HARD" | "SOFT";
export type LagBasis = "working" | "calendar";

export interface Dependency {
  predecessorUid: string;
  type: DepType;
  lagMin: number;
  dependencyClass?: DependencyClass;
  lagBasis?: LagBasis;
  crossProject?: boolean;
  label?: string;
}

/**
 * The construction task domain model — real, API-sourced task data only (name,
 * type, schedule, dependencies, resources, status flags). Every field here is
 * either a direct passthrough from the task API or a value the dialog itself
 * reads/edits. Non-Gantt consumers (e.g. GoPAL) use this type directly.
 *
 * Fields that only exist because the Gantt adapter *computes* them from tree
 * structure or CPM analysis (isGroup, isCritical, isUndeletable, isUnmodifiable,
 * startMin/endMin, baseline*, childTaskUids, resolvedDependencies, successors,
 * _overridden) live on `GanttTask` below, not here — see apiAdapter.ts's
 * mapTask() for exactly which fields are real API passthroughs vs. computed.
 */
export interface Task {
  taskUid: string;
  projectUid: string;
  projectName: string;
  parentTaskUid: string | null;
  taskId: number;
  taskOrder?: number;
  /**
   * Numeric project id the task belongs to. Only populated by adapters that have no
   * ambient single-project context to fall back on (e.g. Sequential view's flat,
   * cross-project feed — see adaptSequentialTasks) — used by useTaskDialog to resolve
   * which project's task record to fetch when there's no single project open.
   */
  projectId?: number;
  taskName: string;
  taskType: string;
  status: TaskStatus;
  phase: string;
  wbs: string;
  assignees: string[];
  assigneeObjects?: ApiAssignee[];
  /**
   * People HELPING this task — a booking with no seat (R8, AB#31088).
   *
   * Deliberately NOT merged into `assignees`: a helper holds no seat, counts toward no
   * crew total, and must never read as the person the task belongs to. Everything that
   * reasons about crew keeps using `assignees`; this exists so the board can SHOW that a
   * second body is on the bar.
   */
  helpers?: string[];
  resourceType: string;
  crewId: string;
  estimatedMin: number;
  durationMin: number;
  timeSpent: number;
  startTime: string;
  endTime: string;
  startDate: string;
  endDate: string;
  progressPct: number;
  isMilestone: boolean;
  hasOpenNote: boolean;
  notes?: string;
  dependencies: Dependency[];
  version: number;
  boms?: any[];
  /** Miscellaneous Service anchor's subcontractor/service selection (ProjectResourceDTO[] from the API) — needed to flag a task whose subcontractor hasn't been chosen yet, the same way `boms` flags a Create BOM Task with no lines. */
  projectResources?: any[];
  allocatedUnit?: any;
  checklistItems?: any[];
  /**
   * PROVISIONAL marker — raw PROVISIONAL_SINCE (task_attribute 567): the ISO
   * timestamp at which this task's dates stopped being re-validated, because the task
   * fell outside the run's planning window and kept its previous dates. Present ONLY
   * on tasks the engine genuinely could not re-plan (C-28 narrowed it from
   * "everything after the horizon" to the actually-affected tasks), so its presence
   * alone is BOTH the grey-out signal (lib/provisional.ts) and the edit-shield signal
   * (lib/provisionalShield.ts).
   *
   * A NON-EMPTY value means the dates will still change. The engine CLEARS the flag by
   * writing an EMPTY value rather than removing the row, so "" and null both mean
   * confirmed — which is why this stays a raw STRING and is never parsed to a Date.
   *
   * Absent entirely until the task API surfaces attribute 567 (it is in neither
   * TaskAttributeIds.java, TaskViewData nor TaskMapperV3 today), so it reads undefined
   * in production and nothing is greyed or shielded yet.
   */
  provisionalSince?: string | null;
  /** task.min_workers (289) — minimum crew size this task needs concurrently. Null/undefined if never set (caller applies its own fallback). */
  minWorkers?: number | null;
  /** task.max_workers (290) — maximum crew size this task can use concurrently. Null/undefined if never set. */
  maxWorkers?: number | null;
  /**
   * Role ids this task REQUIRES, from RESOURCES (attr 500) — e.g. `["admin"]` (AB#27997).
   *
   * Distinct from `assigneeObjects`, which is who is currently on it: the requirement
   * outlives the assignment, so a task an Admin was removed from still declares that it
   * needs an Admin. That is what makes a remove→re-add round trip reversible. Empty for
   * tasks that never recorded a requirement — including ones emptied before the backend
   * stopped erasing it, which cannot be recovered by code.
   */
  requiredRoles?: string[];
  /**
   * Total crew size this task NEEDS, summed from RESOURCES (attr 500) — AB#33174.
   *
   * A different fact from `assignees.length` (who actually holds a seat today): a 2-seat
   * task worked by one person must not read like a 1-seat task. `null` means the
   * requirement could not be read (unknown, never a statement); `0` means the task
   * explicitly needs nobody. See apiAdapter.ts's parseRequiredCrewCount, which mirrors
   * BlockerSeatResolver.readCrew (Java) so the two surfaces never disagree about seats.
   */
  requiredCrewCount?: number | null;
}

/**
 * Extends Task with fields that exist only for Gantt rendering / scheduling-graph
 * mechanics — computed by apiAdapter.ts's mapTask() or the workflow store's edit
 * overlay, never sourced directly from the task API. Used by the real Gantt
 * chart (FieldCraftGanttContainer and everything under ganttV2/); standalone consumers
 * like GoPAL only need Task.
 */
export interface GanttTask extends Task {
  isGroup: boolean;
  isCritical: boolean;
  isUndeletable: boolean;
  isUnmodifiable: boolean;
  startMin: number;
  endMin: number;
  baselineStartMin: number;
  baselineEndMin: number;
  childTaskUids?: string[];
  resolvedDependencies?: string;
  successors: string[];
  // Allocated unit ({value, unit} or string). Persisted tasks fetch it in the dialog;
  // carried on the task only for previews of un-persisted candidates (Gantt Preview),
  // where the dialog falls back to this since there's no DB record to fetch.
  allocatedUnit?: any;
  // BOM rows ({name, quantity, unit, unitCost, totalCost, sourceType, ...}). Persisted tasks
  // fetch these in the dialog; carried on the task only for previews of un-persisted candidates
  // (Gantt Preview, reference-project clone), where the dialog falls back to this since there's
  // no DB record to fetch. Display-only.
  bom?: any[];
  // runtime-only (non-destructive edit layer)
  _overridden?: boolean;
  /**
   * D3 (docs/superpowers/specs/2026-07-21-gantt-task-hierarchy-movement-design.md) —
   * true only for a group auto-created by Demote when it would otherwise make a
   * leaf-with-work a parent. `syntheticGroupOf` names the namesake leaf (its first
   * child, which keeps every work attribute). An explicit marker, not name matching
   * (per the implementation guide) — real phase headers never set this, so D4's
   * auto-dissolve can never mistake a phase header for a dissolvable synthetic group.
   */
  isSyntheticGroup?: boolean;
  syntheticGroupOf?: string;
  /**
   * UX-300 — this row is currently showing a LOCAL PREDICTION, not confirmed server
   * data: the ISO timestamp at which the optimistic overlay was applied to it.
   *
   * Deliberately shaped like `provisionalSince` (attribute 567) and read the same
   * way — the mark travels ON the task, so the grid chip, the canvas bar paint, the
   * tooltip and GoPAL's "my day" list all read one value and physically cannot
   * disagree about which rows are provisional. The two are NOT the same thing
   * though, and neither replaces the other: `provisionalSince` is the ENGINE saying
   * "I knowingly owe this task a recompute"; `_optimisticSince` is THIS BROWSER
   * saying "I am guessing while I wait for the engine". One is server truth about
   * uncertainty; the other is client-side impatience.
   *
   * Runtime-only, like `_overridden` — written by the optimistic overlay
   * (store/optimisticStore.ts), never sourced from the API, never sent to the
   * server, and cleared the moment the engine's answer lands or is refused.
   */
  _optimisticSince?: string;
  /**
   * Numeric internal project id — needed by the create dialog's predecessor/successor
   * APIs (getPredecessorTasks/getSuccessorTasks), which take a numeric projectId, not
   * the projectUid. Populated by the Sequential adapter (sequentialTaskAdapter.ts) from
   * SequentialTask.projectId; per-project views derive it from ambient context (the
   * project the Gantt is already scoped to) instead of carrying it on the task.
   */
  projectIdNum?: number;
  /**
   * Per-row project lifecycle status (e.g. "Active", "On Hold", "Project Closed"), used
   * for row-level edit-lock gating in the flat cross-project Sequential view where there
   * is no single ambient project context to read a status from. Populated by the
   * Sequential adapter from SequentialTask.projectStatus; per-project views instead read
   * projectStatus from ganttBridge's ProjectStatusContext (one project, one status).
   */
  projectStatus?: string;
}

/**
 * The construction project domain model — real business data only (name,
 * client, address, status, schedule dates, color). Non-Gantt consumers (e.g.
 * GoPAL) use this type directly. Fields that only exist for Gantt canvas
 * rendering/bookkeeping (time span in minutes, task count/uids, scheduling
 * grid slot) live on `GanttProject` below, not here.
 */
export interface Project {
  projectUid: string;
  projectName: string;
  projectType: string;
  clientName: string;
  address: string;
  status: string;
  startTime: string;
  endTime: string;
  startDate: string;
  endDate: string;
  crewId: string;
  crewLead: string;
  crewMember: string;
  manager: string;
  progressPct: number;
  color: string;
  version: number;
  roleAssignments?: Record<string, string[]>;
}

/**
 * Extends Project with fields that exist only for Gantt rendering — the
 * project's time span in minutes (derived from its tasks, for canvas bounds),
 * task count/uids, and scheduling-grid bookkeeping. Used by the real Gantt
 * chart; standalone consumers like GoPAL only need Project.
 */
export interface GanttProject extends Project {
  startMin: number;
  endMin: number;
  taskCount: number;
  taskUids: string[];
  dayOffset: number;
  slot: string;
  /** Multi-project ("All Jobs") order/job number shown in the TASK ID column for project rows — e.g. "J001". */
  orderNo?: string;
  /** Numeric project id — needed for fetch-on-expand's getProjectTasks call in multi-project mode. */
  projectId?: number;
}

export interface Resource {
  name: string;
  initials: string;
  color: string;
  role: string;
}

export interface Crew {
  id: string;
  lead: Resource;
  member: Resource;
}

export interface RoleMember {
  name: string;
  resourceUid: string;
}

export interface SampleData {
  projects: GanttProject[];
  tasks: GanttTask[];
  resources: Resource[];
  crews: Crew[];
  users: Resource[];
  crossDeps: { from: string; to: string; label: string }[];
  tasksByUid: Map<string, GanttTask>;
  projectsByUid: Map<string, GanttProject>;
  today: Date;
}

export type GroupMode = "byProject" | "sequential";

export interface ConfirmAction {
  kind: "DELETE" | "SKIP" | "FORCE_COMPLETE" | "BLOCK";
  taskUids: string[];
  requireReason?: boolean;
  /**
   * Labels of tasks the CALLER dropped from the selection because the action can never
   * apply to them (already finished, or a mandatory system type). Carried on the action
   * so whichever dialog renders it can say WHY the count is lower than what the user
   * selected — single-project mode routes SKIP / FORCE_COMPLETE to the API-wired
   * Skip/CompleteTaskWarning, not to ConfirmDialog, and a ConflictBanner behind the
   * modal overlay is invisible while it is open.
   */
  keptTaskLabels?: string[];
}

/**
 * Pending bar drag (move, edge-resize, or dependency link) awaiting user
 * confirmation before it is sent to ASE 2.0 as a MUTATE_TASK/UPDATE. MOVE changes
 * both start and end (duration held fixed); RESIZE changes only end (start held
 * fixed); DEPENDENCY adds a predecessor link to the successor task — the min-based
 * fields are unused for that kind (kept as 0) since no geometry actually changes,
 * only the successor's `dependencies` list.
 */
export interface ScheduleConfirm {
  kind: "MOVE" | "RESIZE" | "DEPENDENCY";
  taskUid: string;
  projectUid: string;
  originalStartMin: number;
  originalEndMin: number;
  newStartMin: number;
  newEndMin: number;
  /** DEPENDENCY only: the task being linked as the new predecessor. */
  predecessorTaskUid?: string;
  /** DEPENDENCY only: the task receiving the new predecessor (taskUid duplicates this). */
  successorTaskUid?: string;
}

/**
 * A Promote/Demote (D1/D2) that has been applied to the client-side overlay but
 * NOT yet sent to the backend — it awaits an explicit Save (commit) or Cancel
 * (undo) on `anchorTaskUid`'s row.
 *
 * Why a pending state exists at all: a hierarchy move is not a field edit. The
 * grid's normal dirty-row mechanism (`pendingEdits`) tracks one row's own column
 * values, but a Demote restructures up to THREE rows at once (the new synthetic
 * group, the leaf that became its first child, and the demoted task) — none of
 * which has a "pending column value". So the Save button on the anchor row must
 * commit the whole move, not just that row's fields; this record is what tells it
 * a move is outstanding and carries what the backend call needs.
 *
 * `anchorTaskUid` is the row whose Save/Cancel resolves the move: the newly
 * created synthetic group when D3 fired (that is also the row whose name the user
 * is expected to edit — the two actions are deliberately the same click), else
 * the moved task itself.
 *
 * `undoDepth` is the undo-stack length recorded BEFORE the move was applied.
 * Cancel pops back down to exactly that depth, so a move that pushed several
 * entries (Demote + a subsequent group rename are separate pushes) is reverted
 * whole rather than one entry at a time.
 */
export interface PendingHierarchyMove {
  kind: "PROMOTE" | "DEMOTE" | "GROUP";
  /** The task the user actually moved. For GROUP, the first selected member. */
  taskUid: string;
  /**
   * GROUP only — every task being bundled under the new group, so the submit can
   * send MUTATE_TASK/GROUP with the whole selection rather than a single-task MOVE.
   */
  memberTaskUids?: string[];
  /** Row that owns the Save/Cancel pair resolving this move (see above). */
  anchorTaskUid: string;
  /** D3 — synthetic group auto-created by this move, if any. */
  createdGroupUid?: string;
  /** Parent the moved task landed under (the group's uid when D3 fired). */
  newParentTaskUid: string;
  /**
   * Parent the moved task came from — for the backend's inverse/undo payload.
   * Null when it had no parent (a project-direct child), matching
   * GanttTask.parentTaskUid's own nullability.
   */
  oldParentTaskUid?: string | null;
  /** undoStack length before the move — Cancel unwinds back to this. */
  undoDepth: number;
}

export interface ConflictInfo {
  actor?: string;
  message: string;
  level?: "info" | "warning" | "error";
}

export interface AseJob {
  id: string;
  projectUid: string;
  status: "running" | "done" | "error";
  label: string;
}

export type VisibleRowType = "project" | "phase" | "task" | "daySeparator" | "skeleton";

export interface VisibleRow {
  kind: VisibleRowType;
  uid: string;
  depth: number;
  projectUid: string;
  task?: GanttTask;
  project?: GanttProject;
  /** Sequential mode only (multi-project) — day boundary marker. See daySeparator kind. */
  dayMin?: number;
  dayDate?: Date;
  /** Sequential mode only — flags task rows so the grid can paint a per-row project color stripe. */
  sequential?: boolean;
}

export interface UndoEntry {
  kind: string;
  desc: string;
  patches: { taskUid: string; before: Partial<GanttTask>; after: Partial<GanttTask> }[];
}

export interface GanttState {
  // mode
  mode: "single" | "multi";
  currentProjectUid: string | null;
  groupMode: GroupMode;
  /** Multi-project project filter — null/empty means "show all projects". */
  selectedProjectUids: Set<string> | null;

  // display
  splitRatio: number;
  scale: ScaleKey;
  density: DensityKey;
  barStyle: BarStyle;
  barRadius: number;
  theme: "light" | "dark";

  // toggles
  showCriticalPath: boolean;
  showBaseline: boolean;
  showResourcePanel: boolean;
  showMiniMap: boolean;
  showWeekendShading: boolean;
  showTodayLine: boolean;
  showDependencies: boolean;
  showCrossProjectDeps: boolean;

  // selection
  selectedTaskUids: Set<string>;

  // open dialogs
  openTaskUid: string | null;
  openTaskTab: string;
  insertAfterTaskUid: string | null;
  insertBeforeTaskUid: string | null;
  /**
   * True when the create was started from a grid ROW's own Add above / Add below menu —
   * which makes that row the new task's mandatory dependency and pins its project. NOT
   * inferable from the anchors: the toolbar's Add and GoPAL both set an anchor too. See
   * workflowStore's declaration for the full reasoning.
   */
  anchorFromRowMenu: boolean;
  confirmAction: ConfirmAction | null;
  scheduleConfirm: ScheduleConfirm | null;
  /** Promote/Demote awaiting Save/Cancel on its anchor row — see PendingHierarchyMove. */
  pendingHierarchyMove: PendingHierarchyMove | null;

  // columns
  columns: ColDef[];

  // filters
  statusFilter: Set<string>;
  searchQuery: string;

  // Sequential-mode-only filters — see workflowStore.ts.
  sequentialStatusScope: "activeUpcoming" | "all";
  selectedResourceUids: Set<string> | null;
  dateWindow: DateWindow;

  // expand/collapse
  expandedUids: Set<string>;

  // non-destructive edit overlay
  taskOverrides: Map<string, Partial<GanttTask>>;
  deletedTaskUids: Set<string>;
  addedTasks: GanttTask[];
  parentOverrides: Map<string, string>;
  /** Tasks created from the Sequential view this session — see WorkflowState.createdTaskUids. */
  createdTaskUids: Set<string>;

  // project order overrides
  projectOrder: string[];
  groupOrder: Map<string, string[]>;

  // undo/redo
  undoStack: UndoEntry[];
  redoStack: UndoEntry[];

  // conflict / presence
  conflict: ConflictInfo | null;
  aseJobs: AseJob[];

  // grid → canvas sync (arch §5.3)
  gridScrollTop: number;
  gridFirstRow: number;
  gridLastRow: number;
}

// Multi-Project Gantt active-projects feed (GET /v4/projects/active).
export interface ActiveProjectSummary {
  id: number;
  projectUid: string;
  orderNo: string;
  status: string;
  description: string;
  startDate: string | null;
  endDate: string | null;
}

export interface ActiveProjectsPage {
  projects: ActiveProjectSummary[];
  nextCursorId: number | null;
  previousCursorId: number | null;
  hasMore: boolean;
}

export interface GetActiveProjectsParams {
  cursorId?: number | null;
  backward?: boolean;
  size?: number;
}

// Gantt Sequential view task feed (GET /v3/tasks/sequential). Matches
// SequentialTaskDTO.java field-for-field — assignees/dependencies are raw JSON
// strings (same wire shape as ApiTask.assignees/dependencies), not yet parsed.
export interface SequentialTask {
  id: number;
  taskUid: string;
  taskName: string;
  status: string;
  startTime: string | null;
  endTime: string | null;
  dependencies: string | null;
  projectId: number;
  projectName: string;
  assignees: string | null;
  taskType: string | null;
  allocatedUnit: string | null;
  phase: string | null;
  parentTaskUid: string | null;
  isMilestone: boolean | null;
  isCritical?: boolean | null;
  /** The project's real external key (UUID) — matches sample.projectsByUid's keys. */
  projectUid: string | null;
  /** t.task_order — the task's position within its project's task sequence. */
  taskOrder: number | null;
  /** Project lifecycle status (e.g. "Active", "On Hold", "Project Closed") for row-level lock gating. */
  projectStatus: string | null;
  /** Raw stored notes value (not author-hydrated) — same wire shape as ApiTask.notes.
   *  Only used to derive hasOpenNote for the grid's has-note icon; the Task Dialog's
   *  Notes tab always re-fetches full note content on open, regardless of view. */
  notes?: string | null;
  /** task.estimated_hours (task_attribute id 178) — the true worked/estimated duration, distinct from the raw endTime-startTime calendar span. */
  estimatedHours?: number | null;
}

export interface SequentialTasksResponse {
  tasks: SequentialTask[];
  nextCursor: string | null;
}

export interface GetSequentialTasksParams {
  resourceUids?: string[];
  allResources?: boolean;
  statusScope?: "activeUpcoming" | "all";
  dateWindow?: "today" | "week" | "month" | "custom" | "all";
  from?: string;
  to?: string;
  cursor?: string | null;
  size?: number;
}
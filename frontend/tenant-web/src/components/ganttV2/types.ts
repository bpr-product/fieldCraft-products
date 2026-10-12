// ganttV2/types.ts — GENERIC view types for the reusable Gantt.
//
// This file is part of the reusable Gantt (Layer B). It must contain ONLY
// host-agnostic presentation types — no construction/ASE/FIELDCRAFT domain shapes.
// The generic row geometry lives in ganttRow.ts; FIELDCRAFT domain types
// (GanttTask, GanttProject, SampleData, status enums, workflow actions) live in
// projectScheduling/types.ts.

export type ScaleKey = "5min" | "15min" | "30min" | "hour" | "day" | "week";
export type DensityKey = "compact" | "standard" | "comfortable";
export type BarStyle = "gradient" | "flat" | "outline";

/** A grid column definition (view concern: which columns, widths, visibility). */
export interface ColDef {
  id: string;
  /** Fallback display label (English). Prefer labelKey for i18n-aware rendering. */
  label: string;
  /** i18n key within the 'gantt' namespace, e.g. "column.taskId". Translated at render time. */
  labelKey?: string;
  width: number;
  visible: boolean;
  resizable?: boolean;
  sortable?: boolean;
}

/** Time window for the canvas axis, in minutes from the timeline origin. */
export interface TimeBounds {
  minMin: number;
  maxMin: number;
  totalMin: number;
}

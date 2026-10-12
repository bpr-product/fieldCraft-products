/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Generic, columns-driven table for the QuickBooks workspace, with a built-in
 * pager and optional row selection. A column's `align` class is applied to BOTH
 * the header cell and the body cell, and header/body share identical horizontal
 * padding, so column headers and their values are always perfectly aligned.
 */
import React from "react";
import { useTranslation } from "react-i18next";
import { Checkbox, Skeleton } from "@mui/material";
import FirstPageRoundedIcon from "@mui/icons-material/FirstPageRounded";
import LastPageRoundedIcon from "@mui/icons-material/LastPageRounded";
import ChevronLeftRoundedIcon from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRoundedIcon from "@mui/icons-material/ChevronRightRounded";

export interface DataColumn<Row> {
  key: string;
  header: string;
  align?: "left" | "right";
  width?: string;
  render: (row: Row) => React.ReactNode;
}

/** Optional checkbox selection (for bulk actions like Assign). */
export interface DataTableSelection<Row> {
  isSelected: (row: Row) => boolean;
  onToggle: (row: Row) => void;
  allSelected: boolean;
  someSelected: boolean;
  onToggleAll: () => void;
}

export interface QuickBooksDataTableProps<Row> {
  columns: DataColumn<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string | number;
  onRowClick?: (row: Row) => void;
  loading?: boolean;
  emptyMessage?: string;
  selection?: DataTableSelection<Row>;

  // Pagination (page is zero-based)
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  pageSizeOptions?: number[];
}

function alignClass(align?: "left" | "right"): string {
  return align === "right" ? "qbw-num" : "";
}

export function QuickBooksDataTable<Row>(props: QuickBooksDataTableProps<Row>) {
  const { t } = useTranslation();
  const {
    columns,
    rows,
    rowKey,
    onRowClick,
    loading = false,
    emptyMessage,
    selection,
    page,
    pageSize,
    total,
    onPageChange,
    onPageSizeChange,
    pageSizeOptions = [25, 50, 100],
  } = props;

  const resolvedEmptyMessage = emptyMessage ?? t("quickbooks.dataTable.emptyDefault");

  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  const from = total === 0 ? 0 : page * pageSize + 1;
  const to = Math.min(total, (page + 1) * pageSize);
  const atStart = page <= 0;
  const atEnd = page >= totalPages - 1;
  const colCount = columns.length + (selection ? 1 : 0);
  // First load (or an empty result) gets skeleton rows; a refetch over rows already on
  // screen keeps them readable and shows a quiet progress line instead of blanking out.
  const showSkeleton = loading && rows.length === 0;
  const refreshing = loading && rows.length > 0;

  return (
    <div
      className={`qbw-table-region${refreshing ? " qbw-table-region--refreshing" : ""}`}
      aria-busy={loading || undefined}
    >
      {/* Decorative — `aria-busy` on the region above is the announced signal, so the
          bar stays out of the accessibility tree instead of firing a second live update
          on every page or filter change. */}
      {refreshing && (
        <div className="qbw-refreshbar" aria-hidden="true">
          <span className="qbw-refreshbar-fill" />
        </div>
      )}
      <div className="qbw-tscroll">
        <table className="qbw-table">
          <thead>
            <tr>
              {selection && (
                <th className="qbw-check-col">
                  <Checkbox
                    size="small"
                    checked={selection.allSelected}
                    indeterminate={selection.someSelected && !selection.allSelected}
                    onChange={selection.onToggleAll}
                    inputProps={{ "aria-label": t("quickbooks.dataTable.selectAllRows") }}
                  />
                </th>
              )}
              {columns.map((col) => (
                <th
                  key={col.key}
                  className={alignClass(col.align)}
                  style={col.width ? { width: col.width } : undefined}
                >
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {showSkeleton ? (
              // Calm skeleton rows while the module's data loads — no spinner text.
              Array.from({ length: Math.min(Math.max(pageSize, 4), 8) }).map((_, i) => (
                <tr key={`qbw-skel-${i}`}>
                  {selection && (
                    <td className="qbw-check-col"><Skeleton variant="rounded" width={16} height={16} /></td>
                  )}
                  {columns.map((col, ci) => (
                    <td key={col.key} className={alignClass(col.align)}>
                      <Skeleton
                        variant="text"
                        height={14}
                        width={col.align === "right" ? 56 : ci === 0 ? "68%" : "52%"}
                        style={col.align === "right" ? { marginLeft: "auto" } : undefined}
                      />
                    </td>
                  ))}
                </tr>
              ))
            ) : rows.length === 0 ? (
              <tr className="qbw-empty-row">
                <td colSpan={colCount}>{resolvedEmptyMessage}</td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr
                  key={rowKey(row)}
                  className={onRowClick ? "qbw-row--click" : undefined}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                >
                  {selection && (
                    <td className="qbw-check-col" onClick={(e) => e.stopPropagation()}>
                      <Checkbox
                        size="small"
                        checked={selection.isSelected(row)}
                        onChange={() => selection.onToggle(row)}
                        inputProps={{ "aria-label": t("quickbooks.dataTable.selectRow") }}
                      />
                    </td>
                  )}
                  {columns.map((col) => (
                    <td key={col.key} className={alignClass(col.align)}>
                      {col.render(row)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="qbw-pager">
        <div className="qbw-pager-l">
          <span>{t("quickbooks.dataTable.rowsPerPage")}</span>
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange?.(Number(e.target.value))}
            aria-label={t("quickbooks.dataTable.rowsPerPage")}
          >
            {pageSizeOptions.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </div>
        <div className="qbw-pager-r">
          <span className="qbw-pgrange">{t("quickbooks.dataTable.pagerRange", { from, to, total })}</span>
          <button type="button" className="qbw-pgbtn" aria-label={t("quickbooks.dataTable.firstPage")} disabled={atStart} onClick={() => onPageChange(0)}>
            <FirstPageRoundedIcon />
          </button>
          <button type="button" className="qbw-pgbtn" aria-label={t("quickbooks.dataTable.previousPage")} disabled={atStart} onClick={() => onPageChange(page - 1)}>
            <ChevronLeftRoundedIcon />
          </button>
          <button type="button" className="qbw-pgbtn" aria-label={t("quickbooks.dataTable.nextPage")} disabled={atEnd} onClick={() => onPageChange(page + 1)}>
            <ChevronRightRoundedIcon />
          </button>
          <button type="button" className="qbw-pgbtn" aria-label={t("quickbooks.dataTable.lastPage")} disabled={atEnd} onClick={() => onPageChange(totalPages - 1)}>
            <LastPageRoundedIcon />
          </button>
        </div>
      </div>
    </div>
  );
}

export default QuickBooksDataTable;

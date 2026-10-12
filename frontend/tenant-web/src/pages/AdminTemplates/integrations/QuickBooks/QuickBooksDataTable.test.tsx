import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import QuickBooksDataTable, {
  QuickBooksDataTable as NamedDataTable,
  DataColumn,
  DataTableSelection,
} from "./QuickBooksDataTable";

interface Row {
  id: number;
  name: string;
  amount: number;
}

const rows: Row[] = [
  { id: 1, name: "Acme Supplies", amount: 100 },
  { id: 2, name: "Northwind", amount: 250 },
];

const columns: DataColumn<Row>[] = [
  { key: "name", header: "Name", render: (r) => r.name },
  { key: "amount", header: "Amount", align: "right", render: (r) => `$${r.amount}` },
];

function renderTable(overrides: Partial<React.ComponentProps<typeof QuickBooksDataTable<Row>>> = {}) {
  const props: React.ComponentProps<typeof QuickBooksDataTable<Row>> = {
    columns,
    rows,
    rowKey: (r) => r.id,
    page: 0,
    pageSize: 25,
    total: rows.length,
    onPageChange: jest.fn(),
    ...overrides,
  };
  return { props, ...render(<QuickBooksDataTable<Row> {...props} />) };
}

describe("QuickBooksDataTable", () => {
  it("exposes the same component as the default and named export", () => {
    expect(QuickBooksDataTable).toBe(NamedDataTable);
  });

  it("renders column headers", () => {
    renderTable();
    expect(screen.getByRole("columnheader", { name: "Name" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Amount" })).toBeInTheDocument();
  });

  it("renders each row's rendered cell values", () => {
    renderTable();
    expect(screen.getByText("Acme Supplies")).toBeInTheDocument();
    expect(screen.getByText("Northwind")).toBeInTheDocument();
    expect(screen.getByText("$100")).toBeInTheDocument();
    expect(screen.getByText("$250")).toBeInTheDocument();
  });

  it("applies the right-align class to right-aligned header and body cells", () => {
    renderTable();
    expect(screen.getByRole("columnheader", { name: "Amount" })).toHaveClass("qbw-num");
    expect(screen.getByText("$100").closest("td")).toHaveClass("qbw-num");
  });

  it("shows the default empty message when there are no rows", () => {
    renderTable({ rows: [], total: 0 });
    expect(screen.getByText("Nothing to show yet.")).toBeInTheDocument();
  });

  it("shows a custom empty message when provided", () => {
    renderTable({ rows: [], total: 0, emptyMessage: "No vendors match." });
    expect(screen.getByText("No vendors match.")).toBeInTheDocument();
  });

  it("shows skeleton rows instead of the empty message while loading", () => {
    renderTable({ rows: [], total: 0, loading: true, emptyMessage: "No vendors match." });
    expect(document.querySelectorAll(".MuiSkeleton-root").length).toBeGreaterThan(0);
    expect(screen.queryByText("No vendors match.")).not.toBeInTheDocument();
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });

  it("renders the pager range as 'from–to of total'", () => {
    renderTable({ rows, total: 100, page: 0, pageSize: 25 });
    expect(screen.getByText(/1.25 of 100/)).toBeInTheDocument();
  });

  it("shows a zeroed range when total is 0", () => {
    renderTable({ rows: [], total: 0, page: 0, pageSize: 25 });
    expect(screen.getByText(/0.0 of 0/)).toBeInTheDocument();
  });

  it("disables First and Previous on the first page", () => {
    renderTable({ rows, total: 100, page: 0, pageSize: 25 });
    expect(screen.getByRole("button", { name: "First page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next page" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Last page" })).toBeEnabled();
  });

  it("disables Next and Last on the last page", () => {
    renderTable({ rows, total: 100, page: 3, pageSize: 25 });
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Last page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "First page" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeEnabled();
  });

  it("calls onPageChange with the next page when Next is clicked", () => {
    const onPageChange = jest.fn();
    renderTable({ rows, total: 100, page: 0, pageSize: 25, onPageChange });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it("calls onPageChange with the last page index when Last is clicked", () => {
    const onPageChange = jest.fn();
    renderTable({ rows, total: 100, page: 0, pageSize: 25, onPageChange });
    fireEvent.click(screen.getByRole("button", { name: "Last page" }));
    expect(onPageChange).toHaveBeenCalledWith(3);
  });

  it("calls onPageChange with the previous page when Previous is clicked", () => {
    const onPageChange = jest.fn();
    renderTable({ rows, total: 100, page: 1, pageSize: 25, onPageChange });
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(onPageChange).toHaveBeenCalledWith(0);
  });

  it("fires onRowClick when a row is clicked", () => {
    const onRowClick = jest.fn();
    renderTable({ onRowClick });
    fireEvent.click(screen.getByText("Acme Supplies"));
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
  });

  describe("with selection", () => {
    function makeSelection(over: Partial<DataTableSelection<Row>> = {}): DataTableSelection<Row> {
      return {
        isSelected: () => false,
        onToggle: jest.fn(),
        allSelected: false,
        someSelected: false,
        onToggleAll: jest.fn(),
        ...over,
      };
    }

    it("renders a header checkbox and one checkbox per row", () => {
      renderTable({ selection: makeSelection() });
      expect(screen.getByRole("checkbox", { name: "Select all rows on this page" })).toBeInTheDocument();
      expect(screen.getAllByRole("checkbox", { name: "Select row" })).toHaveLength(rows.length);
    });

    it("fires onToggleAll when the header checkbox is clicked", () => {
      const onToggleAll = jest.fn();
      renderTable({ selection: makeSelection({ onToggleAll }) });
      fireEvent.click(screen.getByRole("checkbox", { name: "Select all rows on this page" }));
      expect(onToggleAll).toHaveBeenCalled();
    });

    it("fires onToggle with the row when a row checkbox is clicked", () => {
      const onToggle = jest.fn();
      renderTable({ selection: makeSelection({ onToggle }) });
      fireEvent.click(screen.getAllByRole("checkbox", { name: "Select row" })[1]);
      expect(onToggle).toHaveBeenCalledWith(rows[1]);
    });

    it("reflects the allSelected state on the header checkbox", () => {
      renderTable({ selection: makeSelection({ allSelected: true }) });
      expect(screen.getByRole("checkbox", { name: "Select all rows on this page" })).toBeChecked();
    });
  });
});

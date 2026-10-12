/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * The Invoice and Customer Payment dialogs are user-facing accounting views. These
 * tests pin the authoritative invoice total, friendly customer/project labels, and the
 * removal of internal IDs and misleading subtotal/item reconciliation details.
 */
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import {
  CustomerPaymentDetailDialog,
  InvoiceDetailDialog,
} from "./QuickBooksFinancialDetailDialogs";
import type {
  CustomerPaymentDetailResponse,
  InvoiceDetailResponse,
} from "../../../../services/integrations/QuickBooksService";

const PAST_DUE = "2020-03-04T00:00:00Z";
const FUTURE_DUE = "2999-03-04T00:00:00Z";

function invoiceDetail(overrides: {
  invoice?: Partial<InvoiceDetailResponse["invoice"]>;
  mapping?: Partial<InvoiceDetailResponse["mapping"]>;
  document?: InvoiceDetailResponse["document"];
} = {}): InvoiceDetailResponse {
  return {
    ok: true,
    mapping: {
      id: 9001,
      external_invoice_id: "445",
      sync_status: "SYNCED",
      updated_at: "2026-08-17T10:00:00Z",
      version: 2,
      ...overrides.mapping,
    },
    invoice: {
      id: 71,
      invoice_no: "SYN-INV-1001",
      customer_uid: "SYN-CUST-9",
      customer_name: "Synthia Customer",
      project_id: 12,
      project_name: "Synthia Customer, Austin-Roof Replacement-1001",
      issue_at: "2026-08-01T00:00:00Z",
      due_date: FUTURE_DUE,
      status: "Issued",
      total_amount: 1000,
      invoiced_amount: 1100,
      amount_before_tax: 1000,
      tax_amount: 100,
      balance: 1100,
      paid_amount: 0,
      balance_due: 1100,
      items: [],
      tax_info: [],
      ...overrides.invoice,
    },
    document: overrides.document ?? null,
  } as InvoiceDetailResponse;
}

function renderInvoice(
  detail: InvoiceDetailResponse | null,
  loading = false,
  attachmentProps: Partial<React.ComponentProps<typeof InvoiceDetailDialog>> = {},
) {
  return render(
    <InvoiceDetailDialog open loading={loading} detail={detail} onClose={jest.fn()} {...attachmentProps} />,
  );
}

describe("InvoiceDetailDialog", () => {
  it("shows user-facing invoice, customer and project labels without internal IDs", () => {
    renderInvoice(invoiceDetail());
    expect(screen.getByText("SYN-INV-1001")).toBeInTheDocument();
    expect(screen.getAllByText("Synthia Customer").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Synthia Customer, Austin-Roof Replacement-1001").length).toBeGreaterThan(0);
    expect(screen.queryByText("#71")).not.toBeInTheDocument();
    expect(screen.queryByText("SYN-CUST-9")).not.toBeInTheDocument();
  });

  it("renders skeletons while loading and no invoice content", () => {
    renderInvoice(invoiceDetail(), true);
    expect(screen.queryByText("Invoice Total")).not.toBeInTheDocument();
    expect(screen.queryByText("No charge details available")).not.toBeInTheDocument();
  });

  it("shows a designed empty state when the detail could not be loaded", () => {
    renderInvoice(null);
    expect(screen.getByText("Invoice details are unavailable")).toBeInTheDocument();
  });

  // ── Money model ────────────────────────────────────────────────────────────
  describe("totals", () => {
    it("uses invoiced_amount as the user-facing Invoice Total", () => {
      renderInvoice(invoiceDetail({ invoice: { total_amount: 3613.17, invoiced_amount: 3902.23, amount_before_tax: 3613.18, tax_amount: 289.05 } }));
      expect(screen.getAllByText("$3,902.23").length).toBeGreaterThan(0);
      expect(screen.getByText("Includes $289.05 tax")).toBeInTheDocument();
      expect(screen.queryByText("$3,613.17")).not.toBeInTheDocument();
    });

    // The pre-tax amount is arithmetic, not a headline: it belongs under the charges as
    // the subtotal, so the breakdown resolves to the total. It used to sit beside the
    // total as an equal-weight card, competing with it.
    it("does not present the pre-tax amount as a headline figure", () => {
      renderInvoice(invoiceDetail({ invoice: { invoiced_amount: 3902.23, amount_before_tax: 3613.18, tax_amount: 289.05 } }));
      expect(screen.queryByText("Amount before tax")).not.toBeInTheDocument();
      expect(document.querySelectorAll(".qb-fin-money > .qb-fin-card")).toHaveLength(3);
    });

    it("uses reconciled customer-payment allocations for Amount Paid", () => {
      renderInvoice(invoiceDetail({ invoice: { invoiced_amount: 1100, paid_amount: 700, balance_due: 400, balance: 1000 } }));
      expect(screen.getByText("Amount Paid")).toBeInTheDocument();
      expect(screen.getByText("$700.00")).toBeInTheDocument();
      expect(screen.getByText("Part payment received")).toBeInTheDocument();
    });

    it("does not mistake tax for a payment when legacy balance stores pre-tax amount", () => {
      renderInvoice(invoiceDetail({
        invoice: {
          invoiced_amount: 4195.256,
          tax_amount: 199.774,
          balance: 3995.482,
          paid_amount: 4195.256,
          balance_due: 0,
        },
      }));
      // Twice by design on a fully-paid invoice: once as the Invoice Total, once as the
      // Amount Paid. The regression this guards is the third figure NOT appearing — the old
      // code derived paid from `total - balance`, which on this fixture is exactly the tax
      // (4195.256 - 3995.482 = 199.774) and showed tax as if it were a part payment.
      expect(screen.getAllByText("$4,195.26")).toHaveLength(2);
      expect(screen.getByText("Paid in full")).toBeInTheDocument();
      expect(screen.getByText("$0.00")).toBeInTheDocument();
      expect(screen.queryByText("$199.77", { selector: ".qb-fin-card-amount" })).not.toBeInTheDocument();
    });

    it("omits the paid figure entirely when payment allocation is not tracked", () => {
      renderInvoice(invoiceDetail({ invoice: { paid_amount: null, balance_due: null } }));
      expect(screen.queryByText("Amount Paid")).not.toBeInTheDocument();
      expect(screen.getAllByText("Not tracked")).toHaveLength(1);
    });

    it("falls back safely when the authoritative invoiced amount is absent", () => {
      renderInvoice(invoiceDetail({ invoice: { invoiced_amount: null, total_amount: 1750, amount_before_tax: null, tax_amount: 0 } }));
      expect(screen.getAllByText("$1,750.00").length).toBeGreaterThan(0);
    });

    it("shows tax as included rather than as an amount added after the total", () => {
      renderInvoice(invoiceDetail({ invoice: { invoiced_amount: 1100, amount_before_tax: 1000, tax_amount: 100 } }));
      expect(screen.getByText("Includes $100.00 tax")).toBeInTheDocument();
      expect(screen.queryByText(/\+ \$100\.00 tax/)).not.toBeInTheDocument();
    });
  });

  // ── Balance / overdue ──────────────────────────────────────────────────────
  describe("balance state", () => {
    it("reads a zero balance as fully paid", () => {
      renderInvoice(invoiceDetail({ invoice: { balance_due: 0, paid_amount: 1100 } }));
      expect(screen.getByText("Paid")).toBeInTheDocument();
      expect(screen.getByText("Fully paid")).toBeInTheDocument();
      expect(screen.getByText("Balance")).toBeInTheDocument();
    });

    it("treats float dust below the settled tolerance as paid", () => {
      renderInvoice(invoiceDetail({ invoice: { balance_due: 0.004, paid_amount: 1099.996 } }));
      expect(screen.getByText("Paid")).toBeInTheDocument();
    });

    it("shows an outstanding balance as open and due", () => {
      renderInvoice(invoiceDetail({ invoice: { balance_due: 1100, paid_amount: 0 } }));
      expect(screen.getByText("Open")).toBeInTheDocument();
      expect(screen.getByText("Balance Due")).toBeInTheDocument();
    });

    it("flags an unpaid invoice past its due date as overdue", () => {
      renderInvoice(invoiceDetail({ invoice: { balance_due: 500, paid_amount: 600, due_date: PAST_DUE } }));
      expect(screen.getByText("Overdue")).toBeInTheDocument();
    });

    it("does not flag a paid invoice as overdue even when the due date has passed", () => {
      renderInvoice(invoiceDetail({ invoice: { balance_due: 0, paid_amount: 1100, due_date: PAST_DUE } }));
      expect(screen.queryByText("Overdue")).not.toBeInTheDocument();
    });

    it("does not flag an unpaid invoice as overdue before its due date", () => {
      renderInvoice(invoiceDetail({ invoice: { balance_due: 500, paid_amount: 600, due_date: FUTURE_DUE } }));
      expect(screen.queryByText("Overdue")).not.toBeInTheDocument();
    });

    it("reports an untracked balance rather than showing zero", () => {
      renderInvoice(invoiceDetail({ invoice: { balance_due: null, paid_amount: null } }));
      expect(screen.getByText("Not tracked")).toBeInTheDocument();
      expect(screen.queryByText("Paid")).not.toBeInTheDocument();
    });
  });

  // ── Charges ───────────────────────────────────────────────────────────────
  describe("charges", () => {
    it("uses user-friendly charge terminology when no detail rows are stored", () => {
      renderInvoice(invoiceDetail({ invoice: { items: [] } }));
      expect(screen.getByText("Charges")).toBeInTheDocument();
      expect(screen.getByText("No charge details available")).toBeInTheDocument();
      expect(screen.queryByText("Subtotal")).not.toBeInTheDocument();
    });

    it("shows description and amount without quantity or rate developer detail", () => {
      renderInvoice(invoiceDetail({
        invoice: { items: [{ lineItemDescription: "Roofing labor", quantity: 12, rate: 25, subTotal: 300 }] },
      }));
      expect(screen.getByText("Description")).toBeInTheDocument();
      expect(screen.getByText("Roofing labor")).toBeInTheDocument();
      expect(screen.getByText("$300.00")).toBeInTheDocument();
      expect(screen.queryByText("Qty")).not.toBeInTheDocument();
      expect(screen.queryByText("Rate")).not.toBeInTheDocument();
    });

    it("uses Charge as the fallback name instead of Item", () => {
      renderInvoice(invoiceDetail({ invoice: { items: [{ amount: 10 }] } }));
      expect(screen.getByText("Charge 1")).toBeInTheDocument();
    });

    /**
     * Charge lines are pre-tax, so on a taxed invoice they cannot sum to the headline
     * figure on their own — the breakdown used to stop at the lines and appear to
     * contradict the total. These rows carry the arithmetic that closes the gap.
     */
    it("closes the charge breakdown with subtotal, tax and the invoice total", () => {
      renderInvoice(invoiceDetail({
        invoice: {
          invoiced_amount: 1100,
          amount_before_tax: 1000,
          tax_amount: 100,
          // A part-paid balance, so the total is not also the balance figure and the
          // count below measures the card and the closing row, nothing else.
          balance_due: 400, paid_amount: 700,
          tax_info: [{ name: "Sales Tax", percentage: "10" }],
          items: [{ lineItemDescription: "Roofing labor", subTotal: 1000 }],
        },
      }));
      expect(screen.getByText("Subtotal")).toBeInTheDocument();
      expect(screen.getByText("Sales Tax 10%")).toBeInTheDocument();
      expect(screen.getByText("Total")).toBeInTheDocument();
      // subtotal + tax = total, so the three rows reconcile against the hero card.
      expect(screen.getAllByText("$1,000.00").length).toBeGreaterThan(0);
      expect(screen.getAllByText("$100.00").length).toBeGreaterThan(0);
      expect(screen.getAllByText("$1,100.00").length).toBe(2);
    });

    it("drops the subtotal row when there is no tax to reconcile", () => {
      renderInvoice(invoiceDetail({
        invoice: {
          invoiced_amount: 1000, amount_before_tax: 1000, tax_amount: 0,
          items: [{ lineItemDescription: "Roofing labor", subTotal: 1000 }],
        },
      }));
      expect(screen.queryByText("Subtotal")).not.toBeInTheDocument();
      expect(screen.getByText("Total")).toBeInTheDocument();
    });

    it("does not show a technical reconciliation warning for legacy base totals", () => {
      renderInvoice(invoiceDetail({
        invoice: {
          total_amount: 3613.17,
          invoiced_amount: 3902.23,
          amount_before_tax: 3613.18,
          tax_amount: 289.05,
          items: [{ description: "Roof work", amount: 3613.18 }],
        },
      }));
      expect(screen.queryByText(/does not match/i)).not.toBeInTheDocument();
    });
  });

  // ── QuickBooks state ───────────────────────────────────────────────────────
  describe("QuickBooks status card", () => {
    it("shows the QuickBooks id when the invoice is synced", () => {
      renderInvoice(invoiceDetail({ mapping: { external_invoice_id: "445" } }));
      expect(screen.getByText("Synced")).toBeInTheDocument();
      expect(screen.getByText("ID: 445")).toBeInTheDocument();
    });

    it.each([
      ["PENDING_PUSH", "Pending sync"],
      ["SYNC_FAILED", "Sync failed"],
      ["NOT_READY", "Not ready"],
      ["SOMETHING_ELSE", "Not synced"],
    ])("describes the %s state as %s when there is no QuickBooks id", (status, label) => {
      renderInvoice(invoiceDetail({
        mapping: { external_invoice_id: null, sync_status: status },
      }));
      // "Not synced" also appears as the sync-summary's QB-ID placeholder, so allow >1.
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    });

  });

  /**
   * Each fact belongs in exactly one place. The dialog previously repeated the project,
   * the customer, the status, the total, the tax and both dates — once in the header or a
   * card, then again in a reference grid, plus a sync block that restated the whole
   * QuickBooks status card.
   */
  describe("no repeated facts", () => {
    it("shows the project exactly once", () => {
      renderInvoice(invoiceDetail());
      expect(screen.getAllByText("Synthia Customer, Austin-Roof Replacement-1001")).toHaveLength(1);
    });

    it("shows the QuickBooks id exactly once and no second sync block", () => {
      renderInvoice(invoiceDetail({ mapping: { external_invoice_id: "445" } }));
      expect(screen.getAllByText(/445/)).toHaveLength(1);
      expect(screen.queryByText("QuickBooks Sync")).not.toBeInTheDocument();
      expect(screen.queryByText(/QB ID:/)).not.toBeInTheDocument();
    });

    it("shows the invoice total figure exactly once", () => {
      // A part-paid balance, so the total and the balance are different strings — otherwise
      // an unpaid invoice legitimately shows the same figure as both.
      //
      // The paid/outstanding figures must be set as `paid_amount`/`balance_due`: the dialog
      // reads those (they come from reconciled payment allocations) and deliberately ignores
      // the legacy `invoice.balance`. Setting only `balance` left this fixture rendering as
      // an unpaid invoice, so the total appeared on both the total and the balance card.
      renderInvoice(invoiceDetail({
        invoice: {
          invoiced_amount: 1100,
          amount_before_tax: 1000,
          tax_amount: 100,
          paid_amount: 700,
          balance_due: 400,
        },
      }));
      expect(screen.getAllByText("$1,100.00")).toHaveLength(1);
      expect(screen.getAllByText("$400.00")).toHaveLength(1);
      expect(screen.getAllByText("$700.00")).toHaveLength(1);
    });

    it("does not repeat the customer or status from the header in a detail grid", () => {
      renderInvoice(invoiceDetail());
      expect(screen.getAllByText("Synthia Customer")).toHaveLength(1);
      expect(screen.getAllByText("Issued")).toHaveLength(1);
    });

    it("keeps the reason out of the way until the sync state needs attention", () => {
      renderInvoice(invoiceDetail({
        mapping: { sync_status: "SYNCED", reason: "Invoice exported to QuickBooks." },
      }));
      expect(screen.queryByText("Invoice exported to QuickBooks.")).not.toBeInTheDocument();
    });

    it("surfaces the reason when the sync failed", () => {
      renderInvoice(invoiceDetail({
        mapping: { external_invoice_id: null, sync_status: "SYNC_FAILED", reason: "Customer is not synced yet." },
      }));
      expect(screen.getByText("Customer is not synced yet.")).toBeInTheDocument();
    });

    it("unwraps a JSON reason into its message", () => {
      renderInvoice(invoiceDetail({
        mapping: {
          external_invoice_id: null,
          sync_status: "NOT_READY",
          reason: JSON.stringify({ message: "Project mapping is pending." }),
        },
      }));
      expect(screen.getByText("Project mapping is pending.")).toBeInTheDocument();
    });

    it("omits the terms section entirely when there is nothing unique to show", () => {
      renderInvoice(invoiceDetail({ invoice: { term: null, invoice_message: null } }));
      expect(screen.queryByText("Terms")).not.toBeInTheDocument();
    });
  });

  describe("attachment", () => {
    it("shows a clickable attachment with the same preview interaction pattern as Bills", () => {
      const onAttachmentClick = jest.fn();
      renderInvoice(
        invoiceDetail({
          document: { id: 5, name: "SYN-invoice.pdf", mime_type: "application/pdf", size: 4096 },
        }),
        false,
        { onAttachmentClick },
      );
      expect(screen.getByText("Attachment")).toBeInTheDocument();
      expect(screen.getByText("SYN-invoice.pdf")).toBeInTheDocument();
      expect(screen.getByText("application/pdf · 4 KB · Click to view")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /SYN-invoice\.pdf/i }));
      expect(onAttachmentClick).toHaveBeenCalledTimes(1);
    });

    it("renders the invoice attachment preview and download control when opened", () => {
      renderInvoice(
        invoiceDetail({
          document: { id: 5, name: "SYN-invoice.pdf", mime_type: "application/pdf", size: 4096 },
        }),
        false,
        { attachmentPreviewOpen: true, attachmentObjectUrl: "blob:invoice-preview", onAttachmentClick: jest.fn() },
      );
      expect(screen.getByText("Hide attachment")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute("href", "blob:invoice-preview");
    });

    it("omits the attachment card entirely when there is no document", () => {
      renderInvoice(invoiceDetail());
      expect(screen.queryByText("Attachment")).not.toBeInTheDocument();
    });
  });

});

/*
 * Demo feedback: the workspace rendered August 19th as 19/08/2026. Every date in
 * QuickBooks is US numeric MM/DD/YYYY now (see QuickBooksUtils.test.ts for the
 * formatter contract itself). These fixtures use noon UTC on purpose: the calendar
 * day is then the same in every real viewer timezone, so the month-first assertion
 * holds without pinning the CI machine's zone.
 */
describe("US date format", () => {
  /*
   * Fixtures are noon UTC and the DAY is matched loosely (0[45] / 1[89]) rather than pinned.
   * The formatter converts to the viewer's zone, so at UTC+12 and beyond noon UTC has already
   * rolled to the next day — pinning the day would make these pass only in the zones the
   * authors happened to be in. What is being asserted is component ORDER: 4 March renders as
   * 03/… under month-first and would render as 04/03 under day-first, so a leading "03/" is
   * the proof regardless of host zone.
   */
  const MARCH_4_NOON = "2026-03-04T12:00:00Z"; // month-first 03/04, day-first 04/03
  const AUG_19_NOON = "2026-08-19T12:00:00Z";
  const MARCH_4_US = /^03\/0[45]\/2026$/;
  const MARCH_4_DAY_FIRST = /^04\/0[34]\/2026$/;

  it("renders the invoice issue date month-first", () => {
    renderInvoice(invoiceDetail({ invoice: { issue_at: MARCH_4_NOON } }));
    expect(screen.getByText(MARCH_4_US)).toBeInTheDocument();
    expect(screen.queryByText(MARCH_4_DAY_FIRST)).not.toBeInTheDocument();
  });

  it("renders the due date on the balance card month-first", () => {
    renderInvoice(invoiceDetail({
      invoice: { issue_at: AUG_19_NOON, due_date: MARCH_4_NOON, paid_amount: 0, balance_due: 1100 },
    }));
    expect(screen.getByText(/^Due 03\/0[45]\/2026$/)).toBeInTheDocument();
  });

  it("renders last-synced as MM/DD/YYYY with a 12-hour clock", () => {
    renderInvoice(invoiceDetail({ mapping: { updated_at: AUG_19_NOON } }));
    expect(screen.getByText(/^Last synced 08\/(19|20)\/2026 \d{2}:\d{2} (AM|PM)$/)).toBeInTheDocument();
  });

  it("renders the receipt date month-first on the payment dialog", () => {
    render(
      <CustomerPaymentDetailDialog
        open
        loading={false}
        detail={{
          ok: true,
          mapping: { id: 1, external_payment_id: "550", sync_status: "SYNCED", updated_at: null },
          payment: {
            id: 100,
            payment_reference_no: "SYN-PAY-7",
            customer_uid: "SYN-CUST-9",
            customer_name: "Synthia Customer",
            received_time: MARCH_4_NOON,
            status: "Received",
          },
          totals: { amount: 500, applied_amount: 500, unapplied_amount: 0, allocation_count: 0 },
          allocations: [],
        } as unknown as CustomerPaymentDetailResponse}
        onClose={jest.fn()}
      />,
    );
    expect(screen.getByText(MARCH_4_US)).toBeInTheDocument();
    expect(screen.queryByText(MARCH_4_DAY_FIRST)).not.toBeInTheDocument();
  });

  it("never renders a month name — the workspace is numeric, not MMM DD, YYYY", () => {
    renderInvoice(invoiceDetail({ invoice: { issue_at: MARCH_4_NOON, due_date: AUG_19_NOON } }));
    expect(screen.queryByText(/\b(Mar|March|Aug|August)\b/)).not.toBeInTheDocument();
  });
});

// ── Regression: the payment dialog shares this module's primitives ───────────
describe("CustomerPaymentDetailDialog", () => {
  const paymentDetail = {
    ok: true,
    mapping: { id: 1, external_payment_id: "550", sync_status: "SYNCED", updated_at: null },
    payment: {
      id: 100,
      payment_reference_no: "SYN-PAY-7",
      customer_uid: "SYN-CUST-9",
      customer_name: "Synthia Customer",
      project_name: "Synthia Customer, Austin-Roof Replacement-1001",
      received_time: "2026-08-10T00:00:00Z",
      status: "Received",
    },
    totals: { amount: 2456, applied_amount: 2449, unapplied_amount: 7, allocation_count: 1 },
    allocations: [
      { id: 1, reference_no: "SYN-INV-1001", project_id: 12, project_name: "Synthia Customer, Austin-Roof Replacement-1001", payment_amount: 2449, fieldcraft_invoice_id: 71, external_invoice_id: "445", invoice_status: "Partially Paid" },
    ],
  } as unknown as CustomerPaymentDetailResponse;

  it("renders receipt totals, allocations and friendly labels without internal IDs", () => {
    render(<CustomerPaymentDetailDialog open loading={false} detail={paymentDetail} onClose={jest.fn()} />);
    expect(screen.getByText("SYN-PAY-7")).toBeInTheDocument();
    // Twice by design: on the hero card, and again as the figure the allocation table
    // reconciles to.
    expect(screen.getAllByText("$2,456.00")).toHaveLength(2);
    expect(screen.getByText("SYN-INV-1001")).toBeInTheDocument();
    // 2,449 shows as the applied total and again as the allocation row and its total.
    expect(screen.getAllByText("$2,449.00").length).toBeGreaterThan(1);
    // 7.00 shows on the unapplied card and again inside the caution note.
    expect(screen.getAllByText("$7.00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Synthia Customer").length).toBeGreaterThan(0);
    // The project shows on the receipt-information field and on the allocation row.
    expect(screen.getAllByText("Synthia Customer, Austin-Roof Replacement-1001").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("SYN-CUST-9")).not.toBeInTheDocument();
    expect(screen.queryByText(/FIELDCRAFT Invoice/)).not.toBeInTheDocument();
    expect(screen.getByText(/Invoice linked in QuickBooks/)).toBeInTheDocument();
  });

  // ── Applied vs unapplied is the whole story of this view ────────────────────
  it("reads a receipt with money left over as partly applied and explains the remainder", () => {
    render(<CustomerPaymentDetailDialog open loading={false} detail={paymentDetail} onClose={jest.fn()} />);
    expect(screen.getByText("Partly applied")).toBeInTheDocument();
    expect(screen.getByText("Waiting to Apply")).toBeInTheDocument();
    expect(screen.getByText("Not yet matched to an invoice")).toBeInTheDocument();
    expect(screen.getByText(/held in QuickBooks as unapplied credit/i)).toBeInTheDocument();
  });

  it("reads a fully allocated receipt as fully applied with no caution note", () => {
    const fullyApplied = {
      ...paymentDetail,
      totals: { amount: 2449, applied_amount: 2449, unapplied_amount: 0, allocation_count: 1 },
    } as unknown as CustomerPaymentDetailResponse;
    render(<CustomerPaymentDetailDialog open loading={false} detail={fullyApplied} onClose={jest.fn()} />);
    expect(screen.getByText("Fully applied")).toBeInTheDocument();
    expect(screen.getByText("Every dollar allocated")).toBeInTheDocument();
    expect(screen.queryByText(/held in QuickBooks as unapplied credit/i)).not.toBeInTheDocument();
  });

  it("shows a designed empty state when nothing has been applied yet", () => {
    const unapplied = {
      ...paymentDetail,
      totals: { amount: 2456, applied_amount: 0, unapplied_amount: 2456, allocation_count: 0 },
      allocations: [],
    } as unknown as CustomerPaymentDetailResponse;
    render(<CustomerPaymentDetailDialog open loading={false} detail={unapplied} onClose={jest.fn()} />);
    expect(screen.getByText("Not applied to any invoice yet")).toBeInTheDocument();
    expect(screen.getByText("No invoices yet")).toBeInTheDocument();
  });

  it("shows a designed empty state when the payment could not be loaded", () => {
    render(<CustomerPaymentDetailDialog open loading={false} detail={null} onClose={jest.fn()} />);
    expect(screen.getByText("Customer payment details are unavailable")).toBeInTheDocument();
  });

  it("flags an allocation whose invoice is not synced to QuickBooks", () => {
    const unsynced = {
      ...paymentDetail,
      allocations: [{ ...paymentDetail.allocations![0], external_invoice_id: null }],
    } as unknown as CustomerPaymentDetailResponse;
    render(<CustomerPaymentDetailDialog open loading={false} detail={unsynced} onClose={jest.fn()} />);
    expect(screen.getByText(/Invoice not synced to QuickBooks/)).toBeInTheDocument();
  });

  it("reconciles the allocation table to the amount received", () => {
    render(<CustomerPaymentDetailDialog open loading={false} detail={paymentDetail} onClose={jest.fn()} />);
    expect(screen.getByText("Applied across 1 invoice")).toBeInTheDocument();
    expect(screen.getByText("Unapplied")).toBeInTheDocument();
    expect(screen.getByText("Receipt total")).toBeInTheDocument();
    // applied + unapplied = received, so the table resolves to the hero figure.
    expect(screen.getAllByText("$2,456.00")).toHaveLength(2);
  });

  it("leaves the unapplied row out when every dollar is allocated", () => {
    const fullyApplied = {
      ...paymentDetail,
      totals: { amount: 2449, applied_amount: 2449, unapplied_amount: 0, allocation_count: 1 },
    } as unknown as CustomerPaymentDetailResponse;
    render(<CustomerPaymentDetailDialog open loading={false} detail={fullyApplied} onClose={jest.fn()} />);
    expect(document.querySelectorAll(".qb-fin-table-subtotal")).toHaveLength(1);
    expect(screen.getByText("Applied across 1 invoice")).toBeInTheDocument();
    expect(screen.getByText("Receipt total")).toBeInTheDocument();
  });

  it("keeps the money tier to the three receipt figures and demotes the rest to facts", () => {
    render(<CustomerPaymentDetailDialog open loading={false} detail={paymentDetail} onClose={jest.fn()} />);
    expect(document.querySelectorAll(".qb-fin-money > .qb-fin-card")).toHaveLength(3);
    expect(screen.getByText("Project").closest(".qb-fin-fact")).toBeInTheDocument();
    expect(screen.getByText("QuickBooks Status").closest(".qb-fin-fact")).toBeInTheDocument();
  });
});

/**
 * ── Layout regression guards ───────────────────────────────────────────────────
 * The Invoice and Customer Payment dialogs used to lay every summary value out in one
 * `flex-wrap` card strip. With six cards that wrapped into ragged rows of unequal
 * height and gave a project name the same visual weight as the invoice total, which is
 * what made the views read as congested. These tests pin the split so a later change
 * cannot quietly fold the facts back in among the money.
 *
 * The typeface fix is CSS-only and invisible to jsdom, so what is asserted here is the
 * hook it hangs off: the dialog Paper must keep `qb-dialog-paper`, which is the
 * selector that re-declares the workspace font family inside the MUI portal.
 */
describe("financial dialog layout", () => {
  it("puts only the three money figures in the money tier", () => {
    renderInvoice(invoiceDetail());
    const moneyCards = document.querySelectorAll(".qb-fin-money > .qb-fin-card");
    expect(moneyCards).toHaveLength(3);
    expect(screen.getByText("Invoice Total").closest(".qb-fin-money")).toBeInTheDocument();
    expect(screen.getByText("Amount Paid").closest(".qb-fin-money")).toBeInTheDocument();
    expect(screen.getByText("Balance Due").closest(".qb-fin-money")).toBeInTheDocument();
  });

  it("carries the supporting facts as label/value rows rather than as more cards", () => {
    renderInvoice(invoiceDetail({
      document: { id: 5, name: "SYN-invoice.pdf", mime_type: "application/pdf", size: 4096 },
    }));
    expect(document.querySelectorAll(".qb-fin-facts > .qb-fin-fact")).toHaveLength(3);
    for (const label of ["QuickBooks Status", "Project", "Attachment"]) {
      expect(screen.getByText(label).closest(".qb-fin-fact")).toBeInTheDocument();
      expect(screen.getByText(label).closest(".qb-fin-card")).toBeNull();
    }
  });

  it("keeps the class that restores the workspace font inside the MUI portal", () => {
    renderInvoice(invoiceDetail());
    expect(document.querySelector(".MuiDialog-paper")).toHaveClass("qb-dialog-paper");
  });
});

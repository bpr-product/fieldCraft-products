import React from "react";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Skeleton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import ReceiptLongRoundedIcon from "@mui/icons-material/ReceiptLongRounded";
import AccountBalanceWalletRoundedIcon from "@mui/icons-material/AccountBalanceWalletRounded";
import AttachFileRoundedIcon from "@mui/icons-material/AttachFileRounded";
import CheckCircleOutlineRoundedIcon from "@mui/icons-material/CheckCircleOutlineRounded";
import WarningAmberRoundedIcon from "@mui/icons-material/WarningAmberRounded";
import {
  CustomerPaymentDetailResponse,
  InvoiceDetailResponse,
} from "../../../../services/integrations/QuickBooksService";
import { formatCurrency, qbFormatDate, qbFormatDateTime } from "./QuickBooksUtils";
import { fromUtc } from "../../../../utils/utcDateUtil";

/**
 * Stored timestamps are UTC, so they go through the `fromUtc`-based QuickBooks
 * formatters rather than a raw `new Date(...)` parse — the viewer's browser timezone is
 * the only converting layer (see the UTC-first rule in the repo's CLAUDE.md).
 *
 * The workspace renders dates as US numeric `MM/DD/YYYY`; see `QuickBooksUtils`.
 */
const formatDate = (value?: string | null) => qbFormatDateTime(value) || "—";
const formatDay = (value?: string | null) => qbFormatDate(value) || "—";

const money = (value?: number | null) => formatCurrency(Number(value || 0));

const userFacingReason = (value?: string | null): string | null => {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value);
    if (parsed && typeof parsed === "object") {
      return String(parsed.message || parsed.reason || parsed.error || "").trim() || null;
    }
  } catch {
    // Plain-text reasons are already user-readable.
  }
  return value;
};

/** Tolerance for treating a balance as settled — avoids float dust reading as "due". */
const SETTLED_EPSILON = 0.005;

const numberOrNull = (value: any): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const DetailField: React.FC<{ label: string; value?: React.ReactNode }> = ({ label, value }) => (
  <Box className="qb-fin-detail-field">
    <Typography className="qb-fin-detail-label">{label}</Typography>
    <Box className="qb-fin-detail-value">{value ?? "—"}</Box>
  </Box>
);

const LoadingBody = () => (
  <Stack spacing={1.5} py={1}>
    <Skeleton variant="rounded" height={92} />
    <Skeleton variant="rounded" height={132} />
    <Skeleton variant="rounded" height={150} />
  </Stack>
);


/** Normalized invoice line, resolved across the backend's field-name variants. */
type InvoiceLine = {
  description: string;
  meta: string | null;
  amount: number | null;
};

const normalizeInvoiceLine = (item: Record<string, any>, index: number): InvoiceLine => ({
  description: String(
    item.lineItemDescription || item.description || item.name || item.costCode || `Charge ${index + 1}`,
  ),
  meta: [item.costCode, item.industryCode].filter(Boolean).join(" · ") || null,
  amount: numberOrNull(item.subTotal ?? item.sub_total ?? item.amount),
});

/**
 * One card in the summary strip, shared by the Invoice, Bill and Customer Payment
 * dialogs so all three read as one system.
 *
 * `variant` sets the weight — `hero` is the headline figure, `due`/`settled` carry the
 * amber/green money-state meaning, `plain` is neutral supporting data. `tone` only
 * colours a `hero` card, and matches the module's own accent (the same blue/amber/teal
 * used by each dialog's accent bar and workspace rail), so a card's colour never means
 * two different things across views.
 */
export const SummaryCard: React.FC<{
  label: string;
  variant?: "hero" | "due" | "settled" | "plain";
  tone?: "invoice" | "bill" | "payment";
  children: React.ReactNode;
}> = ({ label, variant = "plain", tone = "invoice", children }) => (
  <Box
    className={`qb-fin-card qb-fin-card--${variant}${variant === "hero" ? ` qb-fin-card--${tone}` : ""}`}
  >
    <Typography className="qb-fin-card-label">{label}</Typography>
    {children}
  </Box>
);

/** The strip that lays the cards out; wraps so a conditional card just reflows. */
export const SummaryCards: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <Box className="qb-fin-cards">{children}</Box>
);

/**
 * ── Two-tier summary ────────────────────────────────────────────────────────────
 * `MoneyRow` holds ONLY the figures the view is about; `FactsStrip` holds the
 * supporting context (sync state, project, attachment).
 *
 * Both used to be the same `SummaryCards` flex strip, which is why the Invoice and
 * Customer Payment dialogs read as congested: six equal-weight `flex: 1 1 158px`
 * cards wrapped into ragged rows of unequal height, and a project name carried the
 * same visual weight as the invoice total, so nothing led the eye. Splitting the two
 * gives the money a fixed, equal-column grid that can never go ragged, and demotes
 * the facts to compact label/value rows.
 */
export const MoneyRow: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <Box className="qb-fin-money">{children}</Box>
);

export const FactsStrip: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <Box className="qb-fin-facts">{children}</Box>
);

/** One supporting fact: a fixed label column and a value that may carry a sub-line. */
export const Fact: React.FC<{
  label: string;
  children: React.ReactNode;
}> = ({ label, children }) => (
  <Box className="qb-fin-fact">
    <Typography className="qb-fin-fact-label">{label}</Typography>
    <Box className="qb-fin-fact-value">{children}</Box>
  </Box>
);

/** Section heading with a count chip and a trailing rule. */
export const BlockHeading: React.FC<{ title: string; count?: string | null }> = ({ title, count }) => (
  <Box className="qb-fin-block-head">
    <Typography className="qb-fin-block-title">{title}</Typography>
    {count ? <Chip size="small" className="qb-fin-count-chip" label={count} /> : null}
    <Box className="qb-fin-block-rule" />
  </Box>
);

/**
 * A reconciling row under a line-item table.
 *
 * The tables used to stop at the line items, so an invoice whose charges came to
 * $3,613.18 sat under a $3,902.23 headline with nothing to explain the gap. These rows
 * carry the arithmetic — subtotal, tax, total — so the breakdown resolves to the figure
 * on the card instead of contradicting it.
 *
 * `sub` rows are quiet; the single `total` row is the emphasised one and takes the
 * dialog's own tone.
 */
export const TableFootRow: React.FC<{
  label: string;
  value: string;
  variant?: "sub" | "total";
  tone?: "invoice" | "bill" | "payment";
}> = ({ label, value, variant = "sub", tone = "invoice" }) => (
  variant === "total" ? (
    <Box className={`qb-fin-table-total${tone === "invoice" ? "" : ` qb-fin-table-total--${tone}`}`}>
      <Typography className="qb-fin-total-label">{label}</Typography>
      <Typography className="qb-fin-total-value">{value}</Typography>
    </Box>
  ) : (
    <Box className="qb-fin-table-subtotal">
      <Typography className="qb-fin-subtotal-label">{label}</Typography>
      <Typography className="qb-fin-subtotal-value">{value}</Typography>
    </Box>
  )
);

/** Designed empty state — an icon, a headline and one explanatory line. */
export const EmptyState: React.FC<{
  icon: React.ReactNode;
  title: string;
  detail?: string;
}> = ({ icon, title, detail }) => (
  <Box className="qb-fin-empty">
    <Box className="qb-fin-empty-icon-wrap">{icon}</Box>
    <Typography className="qb-fin-empty-title">{title}</Typography>
    {detail ? (
      <Typography className="qb-fin-empty-text">{detail}</Typography>
    ) : null}
  </Box>
);

/** Footnote strip for context or a caution below a table. */
export const NoteStrip: React.FC<{
  icon: React.ReactNode;
  tone?: "neutral" | "warn";
  children: React.ReactNode;
}> = ({ icon, tone = "neutral", children }) => (
  <Box className={`qb-fin-note${tone === "warn" ? " qb-fin-note--warn" : ""}`}>
    <Box className="qb-fin-note-icon-wrap">{icon}</Box>
    <Typography className="qb-fin-note-text">{children}</Typography>
  </Box>
);

/** The label for a mapping that has not reached QuickBooks yet. */
const unsyncedStateLabel = (syncStatus?: string | null): string => {
  const status = String(syncStatus || "").toUpperCase();
  if (status === "PENDING_PUSH") return "Pending sync";
  if (status === "SYNC_FAILED" || status === "FAILED") return "Sync failed";
  if (status === "NOT_READY") return "Not ready";
  return "Not synced";
};

/**
 * Shared QuickBooks-status card body. This is the single place the sync state is
 * reported — it replaced a separate summary block that repeated the same status, id and
 * timestamp a second time further down each dialog.
 *
 * Used by the Bill dialog, whose summary is still a single card strip.
 */
export const QuickBooksStatusBody: React.FC<{
  externalId?: string | null;
  syncStatus?: string | null;
  updatedAt?: string | null;
}> = ({ externalId, syncStatus, updatedAt }) => (
  externalId ? (
    <>
      <Box className="qb-fin-card-synced">
        <CheckCircleOutlineRoundedIcon className="qb-fin-synced-icon" />
        <Typography className="qb-fin-synced-text">Synced</Typography>
      </Box>
      <Typography className="qb-fin-synced-id qb-fin-mono">ID: {externalId}</Typography>
      {updatedAt ? (
        <Typography className="qb-fin-card-sub">Last synced {formatDate(updatedAt)}</Typography>
      ) : null}
    </>
  ) : (
    <>
      <Typography className="qb-fin-card-state">{unsyncedStateLabel(syncStatus)}</Typography>
      {updatedAt ? (
        <Typography className="qb-fin-card-sub">Last synced {formatDate(updatedAt)}</Typography>
      ) : null}
    </>
  )
);

/**
 * The same sync state as a fact row — one line instead of a card, because in the
 * Invoice and Customer Payment views the sync state is context, not the headline.
 */
export const QuickBooksStatusFact: React.FC<{
  externalId?: string | null;
  syncStatus?: string | null;
  updatedAt?: string | null;
}> = ({ externalId, syncStatus, updatedAt }) => (
  <Fact label="QuickBooks Status">
    {externalId ? (
      <Box className="qb-fin-fact-inline">
        <CheckCircleOutlineRoundedIcon className="qb-fin-synced-icon" />
        <Typography className="qb-fin-synced-text">Synced</Typography>
        <Typography className="qb-fin-synced-id qb-fin-mono">ID: {externalId}</Typography>
      </Box>
    ) : (
      <Typography className="qb-fin-fact-text">{unsyncedStateLabel(syncStatus)}</Typography>
    )}
    {updatedAt ? (
      <Typography className="qb-fin-fact-sub">Last synced {formatDate(updatedAt)}</Typography>
    ) : null}
  </Fact>
);

/** True when a mapping state is something the user may need to act on. */
const isActionableSyncState = (syncStatus?: string | null) =>
  ["SYNC_FAILED", "FAILED", "NOT_READY", "INVALID"].includes(String(syncStatus || "").toUpperCase());

/**
 * The mapping's reason line. Shown only when the state is actionable — on a healthy
 * synced record the reason just restates "exported successfully", which the status card
 * already conveys.
 */
export const SyncReasonNote: React.FC<{
  syncStatus?: string | null;
  reason?: string | null;
}> = ({ syncStatus, reason }) => {
  const message = userFacingReason(reason);
  if (!message || !isActionableSyncState(syncStatus)) return null;
  return (
    <NoteStrip icon={<WarningAmberRoundedIcon className="qb-fin-note-icon" />} tone="warn">
      {message}
    </NoteStrip>
  );
};

/** The shared dialog footer, so all three financial dialogs close the same way. */
export const DialogCloseAction: React.FC<{ onClose: () => void; id?: string }> = ({ onClose, id }) => (
  <DialogActions className="qb-fin-actions">
    <Button id={id} onClick={onClose} variant="outlined" className="qb-fin-close-btn">Close</Button>
  </DialogActions>
);

export const InvoiceDetailDialog: React.FC<{
  open: boolean;
  loading: boolean;
  detail: InvoiceDetailResponse | null;
  onClose: () => void;
  attachmentPreviewOpen?: boolean;
  attachmentLoading?: boolean;
  attachmentObjectUrl?: string | null;
  onAttachmentClick?: () => void;
}> = ({
  open,
  loading,
  detail,
  onClose,
  attachmentPreviewOpen = false,
  attachmentLoading = false,
  attachmentObjectUrl = null,
  onAttachmentClick,
}) => {
  const invoice = detail?.invoice;
  const mapping = detail?.mapping;
  const rawItems = Array.isArray(invoice?.items) ? invoice?.items ?? [] : [];
  const taxes = Array.isArray(invoice?.tax_info) ? invoice?.tax_info ?? [] : [];
  const lines = rawItems.map(normalizeInvoiceLine);
  const document = detail?.document;

  // ── Money model ────────────────────────────────────────────────────────────
  // `invoiced_amount` is the amount exported to QuickBooks and is the only value
  // presented as the final Invoice Total. `total_amount` is a legacy/base field
  // whose meaning varies across FIELDCRAFT rows, so it is intentionally not shown as a
  // second competing total. The pre-tax amount is derived from total − tax.
  const total = numberOrNull(invoice?.invoiced_amount) ?? numberOrNull(invoice?.total_amount);
  const tax = numberOrNull(invoice?.tax_amount) ?? 0;
  const amountBeforeTax = numberOrNull(invoice?.amount_before_tax)
    ?? (total !== null ? total - tax : null);
  // Payment state comes from reconciled payment_receives_entries, not invoice.balance.
  // Some FIELDCRAFT Invoice rows store the pre-tax amount in balance after payment, which made
  // tax look like a payment (total - balance = tax). The backend now returns the actual
  // allocated payment amount and a balance derived from that accounting relationship.
  const paid = numberOrNull(invoice?.paid_amount);
  const balance = numberOrNull(invoice?.balance_due);
  const isSettled = balance !== null && balance <= SETTLED_EPSILON;
  const nothingPaid = paid !== null && paid <= SETTLED_EPSILON;
  const dueDate = fromUtc(invoice?.due_date ?? null);
  const isOverdue = !isSettled
    && balance !== null
    && dueDate != null
    && dueDate.isBefore(fromUtc(new Date().toISOString()));
  const taxLabels = taxes
    .filter((entry: Record<string, any>) => !["deleted", "invalid", "inactive"].includes(String(entry.status || "").toLowerCase()))
    .map((entry: Record<string, any>) => {
      const name = String(entry.name || "Tax").trim();
      const percentage = entry.percentage != null && String(entry.percentage).trim() !== ""
        ? ` ${String(entry.percentage).trim()}%`
        : "";
      return `${name}${percentage}`.trim();
    })
    .filter(Boolean);
  const taxLabel = taxLabels.length > 0 ? taxLabels.join(", ") : "Tax";

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth PaperProps={{ className: "qb-dialog-paper" }}>
      <Box className="qb-dialog-accent-invoices" />

      <DialogTitle className="qb-fin-title">
        <Box className="qb-fin-title-row">
          <Box className="qb-fin-title-icon qb-fin-title-icon--invoice"><ReceiptLongRoundedIcon /></Box>
          <Box minWidth={0} flex={1}>
            <Typography className="qb-fin-title-text" noWrap>
              {invoice?.invoice_no || "Invoice Details"}
            </Typography>
            <Box className="qb-fin-title-meta">
              {invoice?.customer_name ? (
                <Chip label={invoice.customer_name} size="small" className="qb-fin-meta-chip" />
              ) : null}
              {invoice?.issue_at || invoice?.created_at ? (
                <Typography className="qb-fin-title-date">
                  {formatDay(invoice.issue_at || invoice.created_at)}
                </Typography>
              ) : null}
              {invoice?.status ? (
                <Chip label={invoice.status} size="small" className="qb-fin-meta-chip" />
              ) : null}
              {balance !== null ? (
                <Chip
                  label={isSettled ? "Paid" : "Open"}
                  size="small"
                  className={`qb-paid-chip ${isSettled ? "is-paid" : "is-unpaid"}`}
                />
              ) : null}
              {isOverdue ? (
                <Tooltip title={`Payment was due ${formatDay(invoice?.due_date)} and a balance is still outstanding.`} arrow>
                  <Chip
                    icon={<WarningAmberRoundedIcon />}
                    label="Overdue"
                    size="small"
                    className="qb-fin-overdue-chip"
                  />
                </Tooltip>
              ) : null}
            </Box>
          </Box>
        </Box>
      </DialogTitle>

      <DialogContent className="qb-fin-content">
        {loading ? <LoadingBody /> : !detail || !invoice ? (
          <EmptyState
            icon={<ReceiptLongRoundedIcon className="qb-fin-empty-icon" />}
            title="Invoice details are unavailable"
            detail="The invoice could not be loaded. Close this window and try again, or re-run the invoice sync."
          />
        ) : (
          <>
            {/* ── Money ──
                The three figures the view is about, in a fixed equal-column grid.
                Every figure appears exactly once: the pre-tax amount and tax live on
                their own card rather than being repeated as a sub-line here and again
                in a separate breakdown section below. */}
            <MoneyRow>
              <SummaryCard label="Invoice Total" variant="hero" tone="invoice">
                <Typography className="qb-fin-card-amount">{total !== null ? money(total) : "—"}</Typography>
                {tax > 0 ? (
                  <Typography className="qb-fin-card-sub">Includes {money(tax)} tax</Typography>
                ) : null}
              </SummaryCard>

              {/* Omitted rather than shown as a placeholder when the balance is not
                  tracked: what has been paid is derived from it, so there is nothing to
                  say that the Balance card does not already say. */}
              {paid !== null ? (
                <SummaryCard label="Amount Paid">
                  <Typography className="qb-fin-card-amount">{money(paid)}</Typography>
                  <Typography className="qb-fin-card-sub">
                    {isSettled ? "Paid in full"
                      : nothingPaid ? "No payments received yet"
                      : "Part payment received"}
                  </Typography>
                </SummaryCard>
              ) : null}

              <SummaryCard label={isSettled ? "Balance" : "Balance Due"} variant={isSettled ? "settled" : "due"}>
                <Typography className="qb-fin-card-amount">
                  {balance !== null ? money(balance) : "—"}
                </Typography>
                <Typography className="qb-fin-card-sub">
                  {balance === null ? "Not tracked"
                    : isSettled ? "Fully paid"
                    : `Due ${formatDay(invoice.due_date)}`}
                </Typography>
              </SummaryCard>
            </MoneyRow>

            {/* ── Supporting facts ──
                Context, not money — so compact label/value rows rather than cards that
                would compete with the figures above. */}
            <FactsStrip>
              <QuickBooksStatusFact
                externalId={mapping?.external_invoice_id}
                syncStatus={mapping?.sync_status}
                updatedAt={mapping?.updated_at}
              />

              <Fact label="Project">
                <Typography className="qb-fin-fact-text">
                  {invoice.project_name || "Project not available"}
                </Typography>
              </Fact>

              {document ? (
                <Fact label="Attachment">
                  <Box
                    className="qb-fin-fact-inline"
                    onClick={onAttachmentClick}
                    role={onAttachmentClick ? "button" : undefined}
                    tabIndex={onAttachmentClick ? 0 : undefined}
                    onKeyDown={(event) => {
                      if (onAttachmentClick && (event.key === "Enter" || event.key === " ")) {
                        event.preventDefault();
                        onAttachmentClick();
                      }
                    }}
                    sx={onAttachmentClick ? { cursor: "pointer" } : undefined}
                  >
                    {attachmentLoading
                      ? <CircularProgress size={14} className="qb-fin-attach-spinner" />
                      : <AttachFileRoundedIcon className="qb-fin-attach-icon" />}
                    <Typography className="qb-fin-fact-text" noWrap title={document.name || undefined}>
                      {attachmentPreviewOpen ? "Hide attachment" : (document.name || "View attachment")}
                    </Typography>
                  </Box>
                  <Typography className="qb-fin-fact-sub">
                    {[
                      document.mime_type || "File",
                      document.size ? `${Math.ceil(document.size / 1024)} KB` : null,
                      onAttachmentClick ? "Click to view" : null,
                    ].filter(Boolean).join(" · ")}
                  </Typography>
                </Fact>
              ) : null}
            </FactsStrip>

            {attachmentPreviewOpen && attachmentObjectUrl && document ? (
              <Box className="qb-sx-231">
                <Box className="qb-sx-232">
                  <Box className="qb-sx-233">
                    <AttachFileRoundedIcon className="qb-sx-234" />
                    <Typography fontSize={13} fontWeight={700} color="#1D4ED8" noWrap>
                      {document.name || "Invoice attachment"}
                    </Typography>
                    {document.mime_type ? (
                      <Chip
                        label={document.mime_type.includes("pdf") ? "PDF" : (document.mime_type.split("/")[1] || "FILE").toUpperCase()}
                        size="small"
                        className="qb-sx-235"
                      />
                    ) : null}
                    {document.size != null ? (
                      <Typography fontSize={11.5} color="text.secondary" className="qb-sx-236">
                        {(document.size / 1024).toFixed(1)} KB
                      </Typography>
                    ) : null}
                  </Box>
                  <Box className="qb-sx-237">
                    <Button
                      id="qb-btn-invoice-doc-download"
                      size="small"
                      variant="outlined"
                      component="a"
                      href={attachmentObjectUrl}
                      download={document.name || "invoice-document"}
                      className="qb-sx-238"
                    >
                      Download
                    </Button>
                    <Button
                      id="qb-btn-invoice-doc-close"
                      size="small"
                      variant="outlined"
                      onClick={onAttachmentClick}
                      className="qb-sx-239"
                    >
                      ✕
                    </Button>
                  </Box>
                </Box>
                {document.mime_type?.startsWith("image/") ? (
                  <Box className="qb-sx-240">
                    <Box component="img" src={attachmentObjectUrl} alt={document.name || "invoice attachment"} className="qb-sx-241" />
                  </Box>
                ) : document.mime_type === "application/pdf" ? (
                  <Box component="embed" src={attachmentObjectUrl} type="application/pdf" className="qb-sx-242" />
                ) : (
                  <Box component="iframe" src={attachmentObjectUrl} title={document.name || "invoice attachment"} className="qb-sx-242" />
                )}
              </Box>
            ) : null}

            {/* Only surfaced when the sync state needs attention. */}
            <SyncReasonNote syncStatus={mapping?.sync_status} reason={mapping?.reason} />

            {/* ── Charges ── */}
            <BlockHeading
              title="Charges"
              count={lines.length > 0 ? `${lines.length} charge${lines.length === 1 ? "" : "s"}` : null}
            />

            {lines.length === 0 ? (
              <EmptyState
                icon={<ReceiptLongRoundedIcon className="qb-fin-empty-icon" />}
                title="No charge details available"
                detail="This invoice is stored as a summarized amount. The total and tax breakdown are shown below."
              />
            ) : (
              <Box className="qb-fin-table qb-fin-table--invoice-charges">
                <Box className="qb-fin-table-head">
                  <Typography className="qb-line-header">Description</Typography>
                  <Typography className="qb-line-header is-right">Amount</Typography>
                </Box>

                {lines.map((line, index) => (
                  <Box className="qb-fin-table-row" key={`${line.description}-${index}`}>
                    <Box minWidth={0}>
                      <Typography className="qb-fin-row-title">{line.description}</Typography>
                      {line.meta ? (
                        <Typography className="qb-fin-row-sub">{line.meta}</Typography>
                      ) : null}
                    </Box>
                    <Typography className="qb-fin-row-amount">
                      {line.amount !== null ? money(line.amount) : "—"}
                    </Typography>
                  </Box>
                ))}

                {/* ── Reconciliation ──
                    The charge lines are pre-tax, so on a taxed invoice they can never sum
                    to the headline figure on their own. Subtotal and tax are stated here
                    so the breakdown resolves to the Invoice Total rather than appearing to
                    contradict it. Both come from the authoritative fields (the pre-tax
                    amount falls back to total − tax), so these three rows always add up.
                    With no tax the subtotal would merely restate the total, so it is
                    dropped and only the total row remains. */}
                {tax > 0 && amountBeforeTax !== null ? (
                  <TableFootRow label="Subtotal" value={money(amountBeforeTax)} />
                ) : null}
                {tax > 0 ? <TableFootRow label={taxLabel} value={money(tax)} /> : null}
                <TableFootRow
                  variant="total"
                  tone="invoice"
                  label="Total"
                  value={total !== null ? money(total) : "—"}
                />
              </Box>
            )}

            {/* ── Reference detail ──
                Deliberately thin: customer and status are on the title, the money is on
                the cards, and the dates are on the title and the balance card. Only facts
                that appear nowhere else above are repeated here, so the section is skipped
                entirely when there are none. */}
            {invoice.term || invoice.invoice_message ? (
              <Box className="qb-fin-section">
                <Typography className="qb-fin-section-title">Terms</Typography>
                <Box className="qb-fin-detail-grid">
                  {invoice.term ? <DetailField label="Payment term" value={invoice.term} /> : null}
                  {invoice.invoice_message ? (
                    <DetailField label="Customer message" value={invoice.invoice_message} />
                  ) : null}
                </Box>
              </Box>
            ) : null}

          </>
        )}
      </DialogContent>
      <DialogCloseAction onClose={onClose} id="qb-btn-invoice-dialog-close" />
    </Dialog>
  );
};

export const CustomerPaymentDetailDialog: React.FC<{
  open: boolean;
  loading: boolean;
  detail: CustomerPaymentDetailResponse | null;
  onClose: () => void;
}> = ({ open, loading, detail, onClose }) => {
  const payment = detail?.payment;
  const mapping = detail?.mapping;
  const allocations = detail?.allocations || [];
  const totals = detail?.totals;

  // ── Money model ────────────────────────────────────────────────────────────
  // A receipt is money in; how much of it has been matched to invoices is the whole
  // story of this view. Unapplied money is not an error — an advance payment is
  // legitimately unapplied — so it reads as amber "waiting to be applied" rather than
  // as a failure, and green only once every dollar is accounted for.
  const received = numberOrNull(totals?.amount);
  const applied = numberOrNull(totals?.applied_amount);
  const unapplied = numberOrNull(totals?.unapplied_amount);
  const fullyApplied = unapplied !== null && Math.abs(unapplied) <= SETTLED_EPSILON;
  const hasUnapplied = unapplied !== null && unapplied > SETTLED_EPSILON;
  const allocationTotal = allocations.reduce<number | null>((sum, entry) => {
    const amount = numberOrNull(entry.payment_amount);
    if (amount === null) return sum;
    return (sum ?? 0) + amount;
  }, null);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth PaperProps={{ className: "qb-dialog-paper" }}>
      <Box className="qb-dialog-accent-customer-payments" />

      <DialogTitle className="qb-fin-title">
        <Box className="qb-fin-title-row">
          <Box className="qb-fin-title-icon qb-fin-title-icon--payment"><AccountBalanceWalletRoundedIcon /></Box>
          <Box minWidth={0} flex={1}>
            <Typography className="qb-fin-title-text" noWrap>
              {payment?.payment_reference_no || payment?.fieldcraft_transaction_id || "Customer Payment"}
            </Typography>
            <Box className="qb-fin-title-meta">
              {payment?.customer_name ? (
                <Chip label={payment.customer_name} size="small" className="qb-fin-meta-chip" />
              ) : null}
              {payment?.received_time ? (
                <Typography className="qb-fin-title-date">{formatDay(payment.received_time)}</Typography>
              ) : null}
              {payment?.status ? (
                <Chip label={payment.status} size="small" className="qb-fin-meta-chip" />
              ) : null}
              {unapplied !== null ? (
                <Chip
                  label={fullyApplied ? "Fully applied" : "Partly applied"}
                  size="small"
                  className={`qb-paid-chip ${fullyApplied ? "is-paid" : "is-unpaid"}`}
                />
              ) : null}
            </Box>
          </Box>
        </Box>
      </DialogTitle>

      <DialogContent className="qb-fin-content">
        {loading ? <LoadingBody /> : !detail || !payment ? (
          <EmptyState
            icon={<AccountBalanceWalletRoundedIcon className="qb-fin-empty-icon" />}
            title="Customer payment details are unavailable"
            detail="This payment could not be loaded. Close this window and try again, or re-run the payment sync."
          />
        ) : (
          <>
            {/* ── Money ── received, matched, and what is still floating. */}
            <MoneyRow>
              <SummaryCard label="Amount Received" variant="hero" tone="payment">
                <Typography className="qb-fin-card-amount">{received !== null ? money(received) : "—"}</Typography>
                {payment.pay_method ? (
                  <Typography className="qb-fin-card-sub">via {payment.pay_method}</Typography>
                ) : null}
              </SummaryCard>

              <SummaryCard label="Applied to Invoices">
                <Typography className="qb-fin-card-amount">{applied !== null ? money(applied) : "—"}</Typography>
                <Typography className="qb-fin-card-sub">
                  {allocations.length === 0
                    ? "No invoices yet"
                    : `Across ${allocations.length} invoice${allocations.length === 1 ? "" : "s"}`}
                </Typography>
              </SummaryCard>

              <SummaryCard
                label={hasUnapplied ? "Waiting to Apply" : "Unapplied"}
                variant={hasUnapplied ? "due" : fullyApplied ? "settled" : "plain"}
              >
                <Typography className="qb-fin-card-amount">{unapplied !== null ? money(unapplied) : "—"}</Typography>
                <Typography className="qb-fin-card-sub">
                  {unapplied === null ? "Not tracked"
                    : fullyApplied ? "Every dollar allocated"
                    : "Not yet matched to an invoice"}
                </Typography>
              </SummaryCard>
            </MoneyRow>

            {/* ── Supporting facts ── */}
            <FactsStrip>
              <QuickBooksStatusFact
                externalId={mapping?.external_payment_id}
                syncStatus={mapping?.sync_status}
                updatedAt={mapping?.updated_at}
              />

              {payment.project_name ? (
                <Fact label="Project">
                  <Typography className="qb-fin-fact-text">{payment.project_name}</Typography>
                </Fact>
              ) : null}
            </FactsStrip>

            {/* Only surfaced when the sync state needs attention. */}
            <SyncReasonNote syncStatus={mapping?.sync_status} reason={mapping?.reason} />

            {/* ── Allocations ── */}
            <BlockHeading
              title="Applied to"
              count={allocations.length > 0
                ? `${allocations.length} invoice${allocations.length === 1 ? "" : "s"}`
                : null}
            />

            {allocations.length === 0 ? (
              <EmptyState
                icon={<ReceiptLongRoundedIcon className="qb-fin-empty-icon" />}
                title="Not applied to any invoice yet"
                detail="The full amount is held as unapplied credit. Reconcile it against an invoice in FIELDCRAFT and the allocation will appear here after the next sync."
              />
            ) : (
              <Box className="qb-fin-table qb-fin-table--allocations">
                <Box className="qb-fin-table-head">
                  <Typography className="qb-line-header">Invoice</Typography>
                  <Typography className="qb-line-header is-right">Applied</Typography>
                </Box>

                {/* Each allocation carries its project name directly under the Invoice reference.
                    This keeps split receipts understandable when one customer payment is applied
                    across invoices from different projects. */}
                {allocations.map((entry) => (
                  <Box className="qb-fin-table-row" key={entry.id}>
                    <Box minWidth={0}>
                      <Typography className="qb-fin-row-title">
                        {entry.reference_no || "Unreferenced allocation"}
                      </Typography>
                      {entry.project_name ? (
                        <Typography className="qb-fin-row-sub">{entry.project_name}</Typography>
                      ) : null}
                      <Typography className="qb-fin-row-sub">
                        {[
                          entry.invoice_status || null,
                          entry.external_invoice_id ? "Invoice linked in QuickBooks" : "Invoice not synced to QuickBooks",
                        ].filter(Boolean).join(" · ")}
                      </Typography>
                    </Box>
                    <Typography className="qb-fin-row-amount">{money(entry.payment_amount)}</Typography>
                  </Box>
                ))}

                {/* Same reconciliation as the invoice's charges: applied + unapplied =
                    received, so the table resolves to the receipt's headline figure
                    instead of stopping at a partial sum. */}
                <TableFootRow
                  label={`Applied across ${allocations.length} invoice${allocations.length === 1 ? "" : "s"}`}
                  value={allocationTotal !== null ? money(allocationTotal) : "—"}
                />
                {hasUnapplied ? (
                  <TableFootRow label="Unapplied" value={money(unapplied)} />
                ) : null}
                <TableFootRow
                  variant="total"
                  tone="payment"
                  label="Receipt total"
                  value={received !== null ? money(received) : "—"}
                />
              </Box>
            )}

            {/* The figure is on the card above — this line explains what happens to it,
                which is the part the card cannot say. */}
            {hasUnapplied ? (
              <NoteStrip icon={<WarningAmberRoundedIcon className="qb-fin-note-icon" />} tone="warn">
                The remainder is held in QuickBooks as unapplied credit against this customer
                until it is reconciled against an invoice in FIELDCRAFT.
              </NoteStrip>
            ) : null}

            {/* ── Reference detail ──
                Customer, status and received date are on the title; the method is on the
                hero card; the project is on the facts strip. Only the free-text description
                is left, so the section is skipped when there isn't one. */}
            {payment.description ? (
              <Box className="qb-fin-section">
                <Typography className="qb-fin-section-title">Notes</Typography>
                <Box className="qb-fin-detail-grid">
                  <DetailField label="Description" value={payment.description} />
                </Box>
              </Box>
            ) : null}
          </>
        )}
      </DialogContent>
      <DialogCloseAction onClose={onClose} id="qb-btn-payment-dialog-close" />
    </Dialog>
  );
};

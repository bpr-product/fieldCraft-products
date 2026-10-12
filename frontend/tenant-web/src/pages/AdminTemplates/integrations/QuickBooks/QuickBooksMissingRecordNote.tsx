/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * "Why is a record missing?" hint for the master-data modules (Customers,
 * Vendors, Employees).
 *
 * These lists are mapping-driven: a record only appears once it has an
 * external-map row. A record created before the integration was connected, or
 * missed by a partial sync, is therefore absent with nothing on screen
 * explaining why. This gives the user the way back — refresh or update the
 * record, then sync again.
 *
 * Presented as an icon beside the module title rather than a banner above the
 * table: the answer is only wanted when someone is already looking for a record
 * they cannot find, so it stays out of the way until asked for. Disclosure
 * semantics (aria-expanded + aria-controls), not a dialog — there is nothing to
 * interact with inside, so it needs no focus trap.
 *
 * The three modules share one sentence with the record name interpolated, so
 * there is a single set of `quickbooks.missingRecordNote.*` keys rather than one
 * per module. The name is itself a key, not a prop the caller writes in English,
 * so no user-facing word escapes en.json.
 */
import React, { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";

/** The master-data modules this hint is offered on. */
export type QuickBooksMissingRecordNoun = "customer" | "vendor" | "employee";

/**
 * Key identifiers, not display strings — spelled out rather than built from a
 * template literal so every key an extraction tool (or a grep) needs to find is
 * present verbatim in the source.
 */
const NOUN_KEYS: Record<QuickBooksMissingRecordNoun, string> = {
  customer: "quickbooks.missingRecordNote.noun.customer",
  vendor: "quickbooks.missingRecordNote.noun.vendor",
  employee: "quickbooks.missingRecordNote.noun.employee",
};

export interface QuickBooksMissingRecordNoteProps {
  /** Which module's record this hint is about. */
  noun: QuickBooksMissingRecordNoun;
}

export const QuickBooksMissingRecordNote: React.FC<QuickBooksMissingRecordNoteProps> = ({ noun }) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onDocMouseDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocMouseDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocMouseDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const recordName = t(NOUN_KEYS[noun]);

  return (
    <span className="qbw-hint-wrap" ref={wrapRef}>
      <button
        type="button"
        className="qbw-hint-btn"
        onClick={() => setOpen((o) => !o)}
        aria-label={t("quickbooks.missingRecordNote.ariaLabel", { noun: recordName })}
        aria-expanded={open}
        aria-controls={panelId}
      >
        <InfoOutlinedIcon />
      </button>

      {open && (
        <span className="qbw-hintpop" id={panelId}>
          <span className="qbw-hintpop-title">
            {t("quickbooks.missingRecordNote.title", { noun: recordName })}
          </span>
          <span className="qbw-hintpop-detail">
            {t("quickbooks.missingRecordNote.detail", { noun: recordName })}
          </span>
        </span>
      )}
    </span>
  );
};

export default QuickBooksMissingRecordNote;

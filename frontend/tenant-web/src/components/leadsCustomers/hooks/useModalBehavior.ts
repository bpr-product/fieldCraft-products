/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 08-Jul-2026
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : Leads & Customers — shared modal behaviour hook (body scroll lock + Escape-to-close) per UI_GUIDELINES §5.5, §9.5
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE         |   AUTHOR          |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * CRM-Portal     |  08-Jul-2026   |   FIELDCRAFT Web         | New file — useModalBehavior (scroll lock + Escape)
 */

import { useEffect } from 'react';

/**
 * Locks background page scroll while a modal is mounted and closes it on Escape.
 * Restores the previous body overflow on unmount so nested/stacked modals behave.
 *
 * @param open Defaults to `true` -- every existing caller mounts its modal component only while
 *   it should be shown (`{condition && <Modal .../>}`), so the hook itself being unconditional
 *   was never wrong for them. A modal built to stay mounted continuously and toggle its OWN
 *   visibility via an `open` prop (e.g. OnboardingIncompleteWarning, for its MUI Backdrop fade
 *   and focus-restore-on-close logic) needs to pass its `open` through here too, or this hook
 *   locks page scroll and hijacks Escape sitewide from the moment that component first renders
 *   -- not just while it is actually showing (PR 8716 review).
 */
export const useModalBehavior = (onClose: () => void, open: boolean = true): void => {
  useEffect(() => {
    if (!open) {
      return;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [onClose, open]);
};

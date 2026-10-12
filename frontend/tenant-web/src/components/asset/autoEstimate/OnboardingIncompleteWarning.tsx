/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Purpose : Blocking "Onboarding Incomplete" warning shown when an auto estimate is requested
*           for a customer still in Invited status -- per 'Onboarding Incomplete Warning (3a)
*           Spec.md'. Built the same way as UploadingModal (Backdrop + Box, not MUI's <Dialog>):
*           the spec's animated icon/status-pill layout does not fit Dialog's default chrome, and
*           the spec explicitly requires the backdrop click to be a no-op (unlike the other
*           HARD-block dialogs on this screen, which do close on backdrop click) -- Dialog's
*           `disableEscapeKeyDown`/`onClose` shape does not separate "backdrop" from "Escape"
*           cleanly enough for that, so this is a bespoke overlay instead. Styling lives in
*           OnboardingIncompleteWarning.scss, matching this directory's existing SCSS convention
*           (Asset.scss/ListAsset.scss/CreateAsset_v2.scss) rather than sx props.
*
*           Rendered through a Portal into document.body -- required, not decorative.
*           `position: fixed` is positioned relative to the nearest ancestor that creates a new
*           CSS containing block (any `filter`/`backdrop-filter`/`transform`/`perspective`
*           ancestor), not necessarily the viewport. ListAsset.scss's `.headerCard` and several
*           asset-card panels use `backdrop-filter: blur(...)` for their glass effect, so
*           rendering this warning inline (as AutoEstimateButton's child) confined it to that
*           card's box instead of covering the page -- found live via a screen recording. A
*           Portal is what MUI's own Modal/Dialog use to sidestep exactly this; this bespoke
*           Backdrop never had one until now.
*/
import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Backdrop, Box } from "@mui/material";
import { useModalBehavior } from "../../leadsCustomers/hooks/useModalBehavior";
import "./OnboardingIncompleteWarning.scss";

export interface OnboardingIncompleteWarningProps {
    open: boolean;
    onClose: () => void;
}

/** True when the browser/OS asks for reduced motion -- turns off every animation, per spec. */
const useReducedMotion = (): boolean => {
    const [reduced, setReduced] = useState(false);
    useEffect(() => {
        if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
            return;
        }
        const query = window.matchMedia("(prefers-reduced-motion: reduce)");
        setReduced(query.matches);
        const handler = (e: MediaQueryListEvent) => setReduced(e.matches);
        query.addEventListener("change", handler);
        return () => query.removeEventListener("change", handler);
    }, []);
    return reduced;
};

const OnboardingIncompleteWarning: React.FC<OnboardingIncompleteWarningProps> = ({ open, onClose }) => {
    const { t } = useTranslation();
    const reducedMotion = useReducedMotion();
    const closeButtonRef = useRef<HTMLButtonElement>(null);
    const previouslyFocusedRef = useRef<HTMLElement | null>(null);

    // Esc closes it, backdrop click does not -- per spec. useModalBehavior also locks page
    // scroll while this is open, matching every other full-screen modal on this app. This
    // component stays mounted continuously (Backdrop's own `open` toggles its visibility, not
    // conditional mounting), so `open` must be threaded through -- otherwise the hook would lock
    // scroll and hijack Escape sitewide from first render, not just while actually showing.
    useModalBehavior(onClose, open);

    useEffect(() => {
        if (open) {
            previouslyFocusedRef.current = document.activeElement as HTMLElement | null;
            closeButtonRef.current?.focus();
        } else if (previouslyFocusedRef.current?.isConnected) {
            // isConnected guards the case where the triggering element was removed from the DOM
            // while this dialog was open (a re-render, or its own parent unmounting) -- calling
            // .focus() on a detached element is a silent no-op, same as skipping it here. No
            // explicit fallback beyond that: when a focused element is removed, the browser
            // itself moves focus to <body> automatically, which is already the correct resting
            // state -- document.body.focus() would not add anything, since <body> has no
            // tabindex and is not natively focusable.
            previouslyFocusedRef.current.focus();
        }
    }, [open]);

    return createPortal(
        <Backdrop open={open} className="onboardingWarningBackdrop" sx={{ zIndex: (theme) => theme.zIndex.modal + 1 }}>
            <Box
                role="alertdialog"
                aria-modal="true"
                aria-labelledby="onboarding-incomplete-title"
                aria-describedby="onboarding-incomplete-message"
                className="onboardingWarningCard"
            >
                <Box
                    aria-hidden="true"
                    className={`onboardingWarningIcon${reducedMotion ? " onboardingWarningIcon--reducedMotion" : ""}`}
                >
                    <Box className="onboardingWarningIcon__ring" />
                    <Box className="onboardingWarningIcon__circle">
                        <Box className="onboardingWarningIcon__inner">
                            <svg width="34" height="34" viewBox="0 0 24 24" fill="none">
                                <path
                                    className="onboardingWarningIcon__mark"
                                    d="M12 5v9"
                                    stroke="#FFFFFF"
                                    strokeWidth="3"
                                    strokeLinecap="round"
                                    strokeDasharray={10}
                                    strokeDashoffset={10}
                                />
                                <circle
                                    className="onboardingWarningIcon__dot"
                                    cx="12"
                                    cy="19"
                                    r="1.8"
                                    fill="#FFFFFF"
                                />
                            </svg>
                        </Box>
                    </Box>
                </Box>

                <Box id="onboarding-incomplete-title" className="onboardingWarningTitle">
                    {t("asset.autoEstimate.customerInvitedTitle")}
                </Box>

                <Box id="onboarding-incomplete-message" className="onboardingWarningMessage">
                    {t("asset.autoEstimate.customerInvitedBody")}
                </Box>

                <Box className="onboardingWarningPathBox">
                    <Box className="onboardingWarningPathBox__breadcrumb">
                        <span>{t("asset.autoEstimate.customerInvitedPathAdmin")}</span>
                        <Box component="span" className="onboardingWarningPathBox__separator">›</Box>
                        <span>{t("asset.autoEstimate.customerInvitedPathCustomer")}</span>
                        <Box component="span" className="onboardingWarningPathBox__separator">›</Box>
                        <span>{t("common.edit")}</span>
                    </Box>
                    <Box className="onboardingWarningPathBox__hint">
                        {t("asset.autoEstimate.customerInvitedPathHint")}
                    </Box>
                </Box>

                <button
                    ref={closeButtonRef}
                    type="button"
                    onClick={onClose}
                    className="onboardingWarningCloseBtn"
                >
                    {t("dialog.close")}
                </button>
            </Box>
        </Backdrop>,
        document.body
    );
};

export default OnboardingIncompleteWarning;

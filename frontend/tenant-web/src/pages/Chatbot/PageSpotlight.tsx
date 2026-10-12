/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Purpose      : EDEN's on-page spotlight. When the user asks "where is X" about
*                something on the page they're already on, the backend returns a
*                `highlight` label + `highlightTargets` (text to find in the DOM).
*                This component locates that live element, scrolls it into view,
*                and draws an animated glowing ring + caption over it — so EDEN
*                physically points at the thing instead of just describing it.
*
*                Renders through a portal to <body> so it can overlay any element
*                anywhere on the page, independent of the chat card's DOM subtree.
*/
import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { createPortal } from "react-dom";
import { FEATURE_FLAGS } from "../../constants/FeatureFlag";

export interface SpotlightTarget {
  label: string;        // caption, e.g. "Export Invoices button"
  targets: string[];    // ordered DOM text candidates to locate the element
  hint?: string;        // one-line explanation shown under the caption
  // True only for a genuine action request ("I need to add a new material")
  // that EDEN's backend decided to navigate straight to, not a "where is X"
  // locational question or a data-answer highlight (see NavigationResponse.
  // autoClick's own docstring, fieldcraft-ai-services). When set, the element
  // is clicked once located, not just ringed.
  autoClick?: boolean;
  // Ordered text candidates for a SECOND element to ring once autoClick's
  // click has actually opened/revealed it -- e.g. clicking "Material
  // Fields" opens a column-picker menu; "Selling Price" (a checkbox
  // inside it) doesn't exist in the DOM until then, so it can't be found
  // by the FIRST search. Ignored unless autoClick is also set (see
  // NavigationResponse.postClickHighlightTargets, fieldcraft-ai-services).
  postClickTargets?: string[];
}

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const AUTO_DISMISS_MS = 7000;
const RING_PADDING = 6;

/** Normalize whitespace + case for tolerant text comparison. */
const norm = (s: string) => (s || "").toLowerCase().replace(/\s+/g, " ").trim();

/**
 * Activate an element the way a real user would.
 *
 * Real bug, fixed: `el.click()` dispatches ONLY a click event, and MUI's
 * Select opens the menu from `onMouseDown` (SelectInput.js's
 * handleMouseDown, which also bails unless `event.button === 0`) -- it has
 * no onClick at all. So auto-clicking a dropdown highlighted the control
 * and did nothing visible. Dispatching the full press sequence covers both
 * kinds of listener: mousedown-driven (Select, menus) and click-driven
 * (buttons, tabs, checkboxes). Components listening to only one of them
 * still fire exactly once.
 */
function activate(el: HTMLElement): void {
  const opts: MouseEventInit = {
    bubbles: true,
    cancelable: true,
    view: window,
    button: 0,
  };
  try {
    // Some MUI/pointer-aware widgets listen for pointerdown before mouse
    // events; jsdom doesn't always implement PointerEvent, hence the guard.
    if (typeof PointerEvent === "function") {
      el.dispatchEvent(new PointerEvent("pointerdown", opts));
    }
  } catch {
    /* pointer events unsupported here -- mouse events below are enough */
  }
  el.dispatchEvent(new MouseEvent("mousedown", opts));
  el.dispatchEvent(new MouseEvent("mouseup", opts));
  el.click();
}

/** Is the element actually visible and laid out on screen? */
function isVisible(el: HTMLElement): boolean {
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return false;
  const style = window.getComputedStyle(el);
  return style.visibility !== "hidden" && style.display !== "none" && style.opacity !== "0";
}

/**
 * Find the best live DOM element matching any of the target strings. Earlier
 * targets win; within a target, the most specific (shortest matching text)
 * element wins so we ring the button itself, not its whole container.
 */
function findElement(targets: string[]): HTMLElement | null {
  const selector = [
    "button", '[role="button"]', "a[href]", "input", "textarea", "select",
    ".MuiChip-root", '[role="tab"]', ".MuiTab-root", ".MuiSelect-select",
    '[role="combobox"]', "label", "th",
    // Widened so EDEN can also point at plain DATA values, not just
    // controls: table body cells (an invoice number / amount lives in a
    // <td>, not a <th>) and MUI Typography text (a project's Site address,
    // date pills, summary-tile figures all render as <Typography>, which
    // the control-only list above never matched -- so those highlights
    // silently found nothing before).
    "td", ".MuiTypography-root",
    // Dropdown/menu OPTIONS. Needed for post-click targets: MUI renders a
    // <MenuItem> as <li role="option">, and only items built with
    // <ListItemText> get an inner .MuiTypography-root. The Material Fields
    // picker happened to use ListItemText, but Trade/Grade render their
    // value as a bare text child (<MenuItem>{trade}</MenuItem>), so their
    // options matched nothing at all until these were added.
    '[role="option"]', '[role="menuitem"]', ".MuiMenuItem-root",
  ].join(",");

  const candidates = Array.from(
    document.querySelectorAll<HTMLElement>(selector)
  ).filter(
    (el) =>
      isVisible(el) &&
      // Never ring text inside EDEN's own chat panel -- the answer string
      // (e.g. an invoice number) also appears in the chat bubble, and now
      // that Typography is searchable it would otherwise win over the real
      // on-page element. The spotlight is meant to point at the PAGE.
      !el.closest(".chatPopup")
  );

  for (const target of targets) {
    const nt = norm(target);
    if (!nt) continue;
    let best: HTMLElement | null = null;
    let bestLen = Infinity;
    let exact: HTMLElement | null = null;
    for (const el of candidates) {
      const haystack = norm(
        [
          el.textContent,
          el.getAttribute("aria-label"),
          el.getAttribute("placeholder"),
          el.getAttribute("title"),
          (el as HTMLInputElement).value,
        ]
          .filter(Boolean)
          .join(" ")
      );
      // An EXACT text match always beats a substring one. A dropdown option
      // can be a single character (Grade's "a"/"b"/"c"), and `includes("a")`
      // matches almost anything on the page -- without this, ringing a short
      // option would land on whatever unrelated element happened to have the
      // shortest text containing that letter.
      if (!exact && haystack === nt) {
        exact = el;
      }
      if (haystack.includes(nt) && haystack.length < bestLen) {
        best = el;
        bestLen = haystack.length;
      }
    }
    best = exact ?? best;
    if (best) {
      // Real bug, fixed: a matched <label> (MUI's InputLabel, e.g.
      // "Material Fields"/"Trade"/"Grade" filter labels) is text-only --
      // it has no `for`/click handler of its own, so autoClick-ing it did
      // nothing. MUI wires the label to its actual control via
      // aria-labelledby, not `for` -- resolve to that control (the real
      // clickable Select/combobox) so a click/ring lands on something
      // that actually opens.
      if (best.tagName === "LABEL" && best.id) {
        const control = document.querySelector<HTMLElement>(
          `[aria-labelledby~="${best.id}"]`
        );
        if (control && isVisible(control)) return control;
      }
      return best;
    }
  }
  return null;
}

const PageSpotlight: React.FC<{
  target: SpotlightTarget | null;
  onDismiss: () => void;
}> = ({ target, onDismiss }) => {
  const { t } = useTranslation();
  const [rect, setRect] = useState<Rect | null>(null);
  const [notFound, setNotFound] = useState(false);
  const elRef = useRef<HTMLElement | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (!target) {
      setRect(null);
      setNotFound(false);
      elRef.current = null;
      return;
    }

    // The page may still be settling (route just changed, data loading) — retry
    // the DOM search a few times before giving up.
    let attempts = 0;
    let dismissTimer: ReturnType<typeof setTimeout>;
    let clickTimer: ReturnType<typeof setTimeout>;

    const tryLocate = () => {
      const el = findElement(target.targets);
      if (el) {
        elRef.current = el;
        setNotFound(false);
        el.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
        track();
        if (target.autoClick) {
          // Per explicit user direction: an action request ("add a new
          // material") ends with the button already clicked, not just
          // circled. Small delay, not immediate -- gives the ring a moment
          // to actually render before whatever dialog the click opens
          // covers it, so the spotlight doesn't feel like it never
          // happened.
          clickTimer = setTimeout(() => {
            activate(el);
            if (target.postClickTargets && target.postClickTargets.length > 0) {
              // The click just opened a menu/dropdown -- the SECOND
              // target (e.g. "Selling Price", a checkbox inside it)
              // doesn't exist in the DOM until this render happens, so
              // it needs its own retry loop, not the outer one. Staying
              // on the first element (the dropdown itself) if this never
              // finds anything is still correct -- the click DID work.
              let postAttempts = 0;
              const tryLocatePostClick = () => {
                const postEl = findElement(target.postClickTargets!);
                if (postEl) {
                  elRef.current = postEl;
                  postEl.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
                  clearTimeout(dismissTimer);
                  dismissTimer = setTimeout(onDismiss, AUTO_DISMISS_MS);
                } else if (postAttempts < 6) {
                  postAttempts += 1;
                  setTimeout(tryLocatePostClick, 250);
                }
              };
              setTimeout(tryLocatePostClick, 250);
            }
          }, 400);
        }
        dismissTimer = setTimeout(onDismiss, AUTO_DISMISS_MS);
      } else if (attempts < 6) {
        attempts += 1;
        setTimeout(tryLocate, 300);
      } else {
        setNotFound(true);
        dismissTimer = setTimeout(onDismiss, 3500);
      }
    };

    // Follow the element every frame so the ring stays glued to it through
    // scrolling, layout shifts, and the smooth-scroll animation.
    const track = () => {
      const el = elRef.current;
      if (el) {
        const r = el.getBoundingClientRect();
        setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
      }
      rafRef.current = requestAnimationFrame(track);
    };

    tryLocate();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onDismiss();
    };
    window.addEventListener("keydown", onKey);

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      clearTimeout(dismissTimer);
      clearTimeout(clickTimer);
      window.removeEventListener("keydown", onKey);
    };
  }, [target, onDismiss]);

  if (!target) return null;

  // Element genuinely isn't on this page (e.g. hidden by permissions) —
  // show a small toast instead of a ring pointing at nothing.
  if (notFound) {
    return createPortal(
      <div style={toastStyle} role="status">
        <strong>{target.label}</strong>
        <div style={{ opacity: 0.85, marginTop: 4 }}>
          {target.hint || t("chatbot.spotlight.fallbackHint")}
        </div>
      </div>,
      document.body
    );
  }

  if (!rect) return null;

  const captionBelow = rect.top < 90;
  const captionTop = captionBelow
    ? rect.top + rect.height + RING_PADDING + 10
    : rect.top - RING_PADDING - 12;

  // Aurora restyle (README §10) is gated on the flag rather than unconditional --
  // PageSpotlight is the SAME component instance in both the legacy and Aurora
  // Chat.tsx render branches, so an unconditional recolor would leak the new
  // ring colour into the legacy panel even with the flag off.
  //
  // Colour changed teal -> navy blue, 2026-09-10, direct request: the original
  // Aurora handoff spec called for the teal accent (#0E9EA8) here, but that read
  // as off-brand next to the rest of the app's navy chrome (theme.palette.primary
  // / #112D4E, the same navy the boarding pass card and NavigationChip use) --
  // switched to match those instead.
  const auroraUI = FEATURE_FLAGS.ENABLE_EDEN_AURORA_UI;

  return createPortal(
    <>
      <style>{auroraUI ? spotlightKeyframesAurora : spotlightKeyframes}</style>

      {/* The animated ring hugging the element (non-blocking). */}
      <div
        style={
          auroraUI
            ? {
                position: "fixed",
                top: rect.top - RING_PADDING,
                left: rect.left - RING_PADDING,
                width: rect.width + RING_PADDING * 2,
                height: rect.height + RING_PADDING * 2,
                borderRadius: 10,
                border: "none",
                boxShadow: "0 0 0 2px #112d4e, 0 0 0 7px rgba(17,45,78,.18)",
                animation: "eden-spot-glow 1.8s ease-in-out infinite",
                pointerEvents: "none",
                zIndex: 2000,
              }
            : {
                position: "fixed",
                top: rect.top - RING_PADDING,
                left: rect.left - RING_PADDING,
                width: rect.width + RING_PADDING * 2,
                height: rect.height + RING_PADDING * 2,
                borderRadius: 10,
                border: "2px solid #1e88e5",
                boxShadow: "0 0 0 4px rgba(30,136,229,0.25)",
                animation: "eden-spot-pulse 1.4s ease-in-out infinite",
                pointerEvents: "none",
                zIndex: 2000,
              }
        }
      />

      {/* Caption bubble pointing at it. */}
      <div
        style={
          auroraUI
            ? {
                position: "fixed",
                top: captionTop,
                left: rect.left - RING_PADDING,
                transform: captionBelow ? "none" : "translateY(-100%)",
                maxWidth: 300,
                background: "linear-gradient(135deg, #0B3552, #112D4E 65%)",
                color: "#fff",
                padding: "8px 12px",
                borderRadius: 9,
                fontSize: "10.5px",
                fontWeight: 600,
                fontFamily: "Poppins, system-ui, sans-serif",
                lineHeight: 1.35,
                boxShadow: "0 8px 22px rgba(17,45,78,.4)",
                zIndex: 2001,
                pointerEvents: "auto",
                // Animates opacity only (not transform) -- this element's own
                // `transform` already carries captionBelow's translateY(-100%)
                // flip, and a keyframe `to { transform: none }` would stomp
                // that positioning the moment the entrance animation ends.
                animation: "edenSpotlightFadeIn 500ms cubic-bezier(.34,1.56,.64,1) both",
              }
            : {
                position: "fixed",
                top: captionTop,
                left: rect.left - RING_PADDING,
                transform: captionBelow ? "none" : "translateY(-100%)",
                maxWidth: 300,
                background: "#1e2a3a",
                color: "#fff",
                padding: "8px 12px",
                borderRadius: 8,
                fontSize: 13,
                lineHeight: 1.35,
                boxShadow: "0 6px 20px rgba(0,0,0,0.35)",
                zIndex: 2001,
                pointerEvents: "auto",
              }
        }
        role="status"
      >
        <div style={{ fontWeight: 700, display: "flex", alignItems: "center", gap: 6 }}>
          <span aria-hidden>📍</span> {target.label}
        </div>
        {target.hint && (
          <div style={{ opacity: 0.9, marginTop: 3 }}>{target.hint}</div>
        )}
        <button
          onClick={onDismiss}
          style={auroraUI ? dismissBtnStyleAurora : dismissBtnStyle}
          aria-label={t("chatbot.spotlight.dismissAria")}
        >
          {t("common.gotIt")}
        </button>
      </div>
    </>,
    document.body
  );
};

const spotlightKeyframes = `
@keyframes eden-spot-pulse {
  0%   { box-shadow: 0 0 0 4px rgba(30,136,229,0.35); }
  50%  { box-shadow: 0 0 0 10px rgba(30,136,229,0.08); }
  100% { box-shadow: 0 0 0 4px rgba(30,136,229,0.35); }
}`;

// Named distinctly from Chat.scss's `edenSpotlightGlow`/`edenBubbleIn` (this
// component self-contains its keyframes via an injected <style> tag, same
// pattern as the legacy `eden-spot-pulse` above, rather than depending on
// Chat.scss having already been parsed).
const spotlightKeyframesAurora = `
@keyframes eden-spot-glow {
  0%, 100% { box-shadow: 0 0 0 2px #112d4e, 0 0 0 7px rgba(17,45,78,.18); }
  50%      { box-shadow: 0 0 0 2px #112d4e, 0 0 0 13px rgba(17,45,78,.06); }
}
@keyframes edenSpotlightFadeIn {
  from { opacity: 0; }
  to   { opacity: 1; }
}`;

const dismissBtnStyle: React.CSSProperties = {
  marginTop: 8,
  background: "rgba(255,255,255,0.15)",
  color: "#fff",
  border: "none",
  borderRadius: 6,
  padding: "3px 10px",
  fontSize: 12,
  cursor: "pointer",
};

const dismissBtnStyleAurora: React.CSSProperties = {
  marginTop: 8,
  // Light-on-dark now that the pill itself is navy (was navy-on-teal, which
  // is why this used to be a dark button on a light ground).
  background: "rgba(255,255,255,0.14)",
  color: "#fff",
  border: "none",
  borderRadius: 6,
  padding: "3px 10px",
  fontSize: 12,
  fontFamily: "Poppins, system-ui, sans-serif",
  cursor: "pointer",
};

const toastStyle: React.CSSProperties = {
  position: "fixed",
  bottom: 24,
  left: "50%",
  transform: "translateX(-50%)",
  background: "#1e2a3a",
  color: "#fff",
  padding: "10px 16px",
  borderRadius: 8,
  fontSize: 13,
  maxWidth: 360,
  boxShadow: "0 6px 20px rgba(0,0,0,0.35)",
  zIndex: 2001,
};

export default PageSpotlight;

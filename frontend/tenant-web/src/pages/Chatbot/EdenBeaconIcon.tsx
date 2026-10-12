/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : The EDEN brand mark for places nothing already provides a
*                background -- the FAB glyph, an app tile, a favicon export. Same
*                gradient orb used everywhere in Aurora (EdenAvatarOrb/
*                EDEN_GRADIENTS.orb) plus EdenRobotFace's glyph (antenna, rounded
*                head, two dot eyes, an "E"-shaped mouth), so a minimized/closed
*                Eden still reads as "AI assistant" at a glance.
*
*                Revision 2026-09-10: replaces the earlier "beacon" concept (a
*                compass-notch orb) and the plain bold "E" Typography glyph used in
*                FloatingDock's FAB -- direct product feedback was that a bare
*                letter didn't communicate "AI chatbot" on its own.
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | Replaced the beacon/notch mark with
*                                              EdenRobotFace's robot-head glyph.
*/
import React from "react";
import EdenRobotFace from "./EdenRobotFace";

interface EdenBeaconIconProps {
  /** Width/height in px (square). */
  size?: number;
  /** Extra class, e.g. for a CSS-driven hover/pulse on the wrapping element. */
  className?: string;
}

// Stable per-instance id so two icons on the same page never collide on the
// gradient's <defs> id (SVG ids are document-global, not scoped to the element).
let instanceCount = 0;

const EdenBeaconIcon: React.FC<EdenBeaconIconProps> = ({ size = 24, className }) => {
  const gradientId = React.useMemo(() => `eden-beacon-grad-${instanceCount++}`, []);

  return (
    <div
      className={className}
      style={{ position: "relative", width: size, height: size }}
      role="img"
      aria-label="Eden -- AI assistant"
    >
      <svg width={size} height={size} viewBox="0 0 128 128" style={{ position: "absolute", inset: 0 }} aria-hidden="true">
        <defs>
          <radialGradient id={gradientId} cx="32%" cy="26%" r="80%">
            <stop offset="0%" stopColor="#5FE0E8" />
            <stop offset="58%" stopColor="#0E9EA8" />
            <stop offset="100%" stopColor="#0B3552" />
          </radialGradient>
        </defs>
        <circle cx="64" cy="64" r="58" fill={`url(#${gradientId})`} />
        <circle cx="64" cy="64" r="58" fill="none" stroke="rgba(255,255,255,.14)" strokeWidth="1" />
      </svg>
      <div style={{ position: "absolute", inset: 0 }}>
        <EdenRobotFace size={size} />
      </div>
    </div>
  );
};

export default EdenBeaconIcon;

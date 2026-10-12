/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
*
* Created on   : 10-Sep-2026
* @author      : FIELDCRAFT Web
* @since       : 0.0.1
* Requirement# : EDEN Aurora UI
* Purpose      : The robot-face glyph ALONE -- antenna, rounded head outline, two dot
*                eyes, and a mouth drawn as a capital "E" (one spine, three bars, at
*                speaker-grille proportions). No background circle of its own, so it
*                can be drawn directly on top of an orb a caller already owns
*                (EdenAvatarOrb's gradient Box) without stacking two redundant
*                gradient circles. EdenBeaconIcon is this same glyph WITH its own
*                background circle, for places nothing already provides one (the
*                FAB, an app tile, a favicon export).
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE       |   AUTHOR   |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* EDEN Aurora UI | 10-Sep-2026  | FIELDCRAFT Web    | New file.
*/
import React from "react";

interface EdenRobotFaceProps {
  /** Width/height in px (square). */
  size?: number;
  /** Glyph color. Defaults to the near-white used elsewhere on the orb gradient. */
  color?: string;
}

const EdenRobotFace: React.FC<EdenRobotFaceProps> = ({ size = 24, color = "rgba(255,255,255,.92)" }) => (
  <svg width={size} height={size} viewBox="0 0 128 128" role="img" aria-hidden="true">
    {/* Antenna. */}
    <line x1="64" y1="19" x2="64" y2="29" stroke={color} strokeWidth="3" strokeLinecap="round" />
    <circle cx="64" cy="17" r="3.5" fill={color} />

    {/* Head outline -- unfilled, so whatever orb this sits on shows through. */}
    <rect x="36" y="32" width="56" height="50" rx="16" fill="none" stroke={color} strokeWidth="3" />

    {/* Eyes. */}
    <circle cx="50" cy="54" r="4.5" fill={color} />
    <circle cx="78" cy="54" r="4.5" fill={color} />

    {/* Mouth == a capital "E" (one spine, three bars) -- robot grille at a
        glance, EDEN's initial on a second look. */}
    <path
      d="M48 62 L48 76 M48 62 L64 62 M48 69 L62 69 M48 76 L64 76"
      fill="none" stroke={color} strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round"
    />
  </svg>
);

export default EdenRobotFace;

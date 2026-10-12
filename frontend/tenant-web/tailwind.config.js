/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 02-July-2026
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Requirement# : 21843
 * Purpose      : Tailwind config for the Leads & Customers island in fieldcraft-tenant-web —
 *                src/components/leadsCustomers/** (includes the Automation Plans tab, which
 *                lives under this same subtree). Preflight is disabled: no Tailwind base
 *                reset leaks into MUI screens elsewhere in the app. Leads tokens map to
 *                --fieldcraft-* CSS variables from components/leadsCustomers/colors_and_type.css.
 *
 *                The Project Hub Notes/Chat island (src/components/projectHub/hub/**) is a
 *                SEPARATE Tailwind config — see tailwind.config.hub.js. The two islands are
 *                compiled by two independent Tailwind instances (wired per-file in
 *                config-overrides.js), not this one, specifically so Hub's `important:
 *                '[data-ph-root]'` scoping can never leak into Leads/Automation Plans utility
 *                generation. Do not merge the two configs back into one — an earlier attempt
 *                at that (see AB#25449) put `important: '[data-ph-root]'` in this shared
 *                config, which silently zeroed out every Leads & Customers / Automation Plans
 *                utility class because their elements are never wrapped in [data-ph-root].
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE      |  AUTHOR | DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 * AB#25449       | 01-Aug-2026 | FIELDCRAFT Web | Split back into two independently-configured,
 *                                          independently-compiled islands (this file +
 *                                          tailwind.config.hub.js) after a merge reintroduced
 *                                          the shared-`important` bug this file's history once
 *                                          warned about. Shared color/font/shadow tokens moved
 *                                          to tailwind.theme.js so both configs stay in sync.
 */

// Absolute, FORWARD-SLASH base dir — never build the glob with path.join() on Windows
// (backslashes are escape chars to fast-glob, silently matching zero files). See the
// same note in tailwind.config.hub.js.
const ROOT = __dirname.replace(/\\/g, '/');

/** @type {import('tailwindcss').Config} */
module.exports = {
  // Scope: ONLY these subtrees generate utilities for this config. Widening this glob
  // requires explicit sign-off (migration constraint #2).
  // bom/** added for the BOM screen redesign (Inventory Consumption During Jobs follow-up) --
  // no `important` requirement, so it's safe to share this config/instance rather than
  // needing its own island like Hub.
  content: [
    `${ROOT}/src/components/leadsCustomers/**/*.{ts,tsx}`,
    `${ROOT}/src/components/bom/**/*.{ts,tsx}`,
  ],

  corePlugins: {
    // Preflight is Tailwind's global CSS reset — it would restyle every MUI
    // screen in the app. Disabled; the Leads island keeps its own scoped reset.
    preflight: false,
  },

  // No `important` here — see file header. Hub's [data-ph-root] importance scoping
  // lives ONLY in tailwind.config.hub.js so it can never prefix a Leads/Automation
  // Plans utility class.

  darkMode: 'class',

  theme: {
    extend: require('./tailwind.theme.js'),
  },
  plugins: [],
};

/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 01-Aug-2026
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Requirement# : 25449
 * Purpose      : Tailwind config for the Project Hub Notes/Chat island —
 *                src/components/projectHub/hub/**. Compiled as a SEPARATE Tailwind instance
 *                from tailwind.config.js (wired per-file in config-overrides.js) so this
 *                config's `important: '[data-ph-root]'` scoping can never leak into the
 *                Leads & Customers / Automation Plans island, which is never wrapped in
 *                [data-ph-root]. See tailwind.config.js's header for the incident this
 *                split fixes (AB#25449).
 */

// Absolute, FORWARD-SLASH base dir. Tailwind resolves relative content globs against
// process.cwd(), which can differ from this file's directory — but path.join() must NOT
// be used to build the glob on Windows: it emits backslashes, and fast-glob (Tailwind's
// matcher) treats "\" as an escape character, so such a glob silently matches ZERO files
// and the config emits no utilities at all.
const ROOT = __dirname.replace(/\\/g, '/');

/** @type {import('tailwindcss').Config} */
module.exports = {
  // Scope: ONLY this subtree generates utilities for this config.
  content: [
    `${ROOT}/src/components/projectHub/hub/**/*.{ts,tsx}`,
  ],

  corePlugins: {
    // Preflight is Tailwind's global CSS reset — it would restyle every MUI
    // screen in the app. Disabled; the scoped mini-reset in hub-tailwind.css
    // under [data-ph-root] takes its place.
    preflight: false,
  },

  // Every utility is emitted as [data-ph-root] .class — utilities cannot leak
  // outside the Hub subtree and win specificity ties against MUI emotion styles.
  // Scoped to THIS config only — the Leads & Customers config does not set this.
  important: '[data-ph-root]',

  darkMode: 'class',

  theme: {
    extend: require('./tailwind.theme.js'),
  },
  plugins: [],
};

/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 10-Jul-2026
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Purpose      : react-app-rewired override — injects tailwindcss + autoprefixer into CRA's
 *                postcss-loader. react-scripts 5 hardcodes `postcssOptions.config: false`
 *                (see node_modules/react-scripts/config/webpack.config.js), so a root
 *                postcss.config.js is silently ignored without this override.
 * Revision History
 * -----------------------------------------------------------------------------
 * CRM-Dev-OOM    | 14-Jul-2026   | FIELDCRAFT Web | Raises ForkTsCheckerWebpackPlugin's memoryLimit
 *                                            (default 2048 MB) — the project has grown past
 *                                            what that default can type-check in one pass,
 *                                            crashing the forked checker process with
 *                                            "JavaScript heap out of memory" on `npm start`.
 * AB#25449       | 01-Aug-2026   | FIELDCRAFT Web | Tailwind is now TWO independent instances — one
 *                                            per tailwind.config.js/tailwind.config.hub.js —
 *                                            selected per-file below, instead of one shared
 *                                            instance/config for both islands. A shared config
 *                                            let Hub's `important: '[data-ph-root]'` (global to
 *                                            the config, not just Hub's content glob) silently
 *                                            zero out every Leads & Customers / Automation
 *                                            Plans utility class, since that subtree is never
 *                                            wrapped in [data-ph-root]. Keeping the instances
 *                                            (and configs) separate makes that impossible.
 */

const tailwindcss = require('tailwindcss');
const autoprefixer = require('autoprefixer');

// ONE Tailwind plugin, as before. Which *config* a stylesheet compiles against is declared
// by the stylesheet itself via Tailwind's `@config` directive — see the top of
// src/components/projectHub/hub/hub-tailwind.css, which selects tailwind.config.hub.js so
// its `important: '[data-ph-root]'` scoping stays confined to the Hub island. Every other
// stylesheet (including the Leads & Customers island) uses the default tailwind.config.js.
const injectTailwind = rule => {
  if (!rule) return;
  if (Array.isArray(rule.oneOf)) { rule.oneOf.forEach(injectTailwind); return; }
  if (Array.isArray(rule.rules)) { rule.rules.forEach(injectTailwind); }
  if (Array.isArray(rule.use)) {
    rule.use.forEach(u => {
      const loaderPath = typeof u === 'string' ? u : u.loader;
      if (loaderPath && loaderPath.includes('postcss-loader') && u.options && u.options.postcssOptions) {
        u.options.postcssOptions.plugins = [
          ...(u.options.postcssOptions.plugins || []),
          tailwindcss,
          autoprefixer,
        ];
      }
    });
  }
};

// The forked type-checker's own memory ceiling (separate from the main webpack process'
// heap) — raising it here means the check itself gets more room, on top of whatever
// NODE_OPTIONS/--max-old-space-size the `start`/`build` scripts are launched with.
const raiseTsCheckerMemoryLimit = plugin => {
  if (plugin && plugin.constructor && plugin.constructor.name === 'ForkTsCheckerWebpackPlugin' && plugin.options) {
    plugin.options.typescript = { ...(plugin.options.typescript || {}), memoryLimit: 8192 };
    if (plugin.options.eslint) {
      plugin.options.eslint = { ...plugin.options.eslint, memoryLimit: 8192 };
    }
  }
};

module.exports = function override(config) {
  config.module.rules.forEach(injectTailwind);
  (config.plugins || []).forEach(raiseTsCheckerMemoryLimit);
  return config;
};

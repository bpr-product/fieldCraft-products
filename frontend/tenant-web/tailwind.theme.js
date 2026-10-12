/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 01-Aug-2026
 * @author      : FIELDCRAFT Web
 * @since       : 0.0.1
 * Requirement# : 25449
 * Purpose      : Shared theme.extend for both Tailwind islands (tailwind.config.js /
 *                tailwind.config.hub.js), factored out so the two configs can diverge on
 *                `important` scoping without the color/shadow/font tokens drifting apart.
 */

module.exports = {
  colors: {
    // --- Leads & Customers island (var()-backed, single source of truth) ---
    fieldcraft: {
      primary: 'var(--fieldcraft-primary)',
      'primary-hover': 'var(--fieldcraft-primary-hover)',
      'primary-pressed': 'var(--fieldcraft-primary-pressed)',
      'primary-tint': 'var(--fieldcraft-primary-tint)',
      'blue-300': 'var(--fieldcraft-blue-300)',
      success: 'var(--fieldcraft-success)',
      'success-dark': 'var(--fieldcraft-success-dark)',
      'success-light': 'var(--fieldcraft-success-light)',
      warning: 'var(--fieldcraft-warning)',
      'warning-dark': 'var(--fieldcraft-warning-dark)',
      'warning-light': 'var(--fieldcraft-warning-light)',
      danger: 'var(--fieldcraft-danger)',
      'danger-dark': 'var(--fieldcraft-danger-dark)',
      'danger-light': 'var(--fieldcraft-danger-light)',
      info: 'var(--fieldcraft-info)',
      // Every other tone here carries -dark/-light; info was the only one without, so the
      // info-tone step tile in AutomationPlanHelpers had no utilities to resolve against.
      // Both variables are defined in colors_and_type.css AND portal-colors.css, because the
      // Plan Builder renders under both the .leads-customers-tab and .customer-portal-root scopes.
      'info-dark': 'var(--fieldcraft-info-dark)',
      'info-light': 'var(--fieldcraft-info-light)',
      'surface-1': 'var(--fieldcraft-surface-1)',
      'surface-2': 'var(--fieldcraft-surface-2)',
      'surface-3': 'var(--fieldcraft-surface-3)',
      border: 'var(--fieldcraft-border)',
      'border-strong': 'var(--fieldcraft-border-strong)',
      'fg-1': 'var(--fieldcraft-fg-1)',
      'fg-2': 'var(--fieldcraft-fg-2)',
      'fg-3': 'var(--fieldcraft-fg-3)',
    },

    // --- Project Hub island (design handoff palette) ---
    brand: {
      50: '#e7eef5', 100: '#c5d4e6', 200: '#92aece', 300: '#5d85b3',
      400: '#326699', 500: '#13507f', 600: '#0e4067', 700: '#0a3252',
      800: '#082640', 900: '#06192c',
    },
    gold: {
      50: '#fdf3df', 100: '#fbe5b3', 200: '#f6cf78', 300: '#f0b62f',
      400: '#e0a01c', 500: '#c28518', 600: '#9b6a13', 700: '#7a530e',
    },
    urgent: { 50: '#fff1e6', 100: '#ffdcc2', 500: '#e8772b', 700: '#a8541a' },
    warn:   { 50: '#fff6e0', 100: '#ffe7a8', 500: '#c08a1e', 700: '#7e5a14' },
    info:   { 50: '#e8f0fb', 100: '#cfdef5', 500: '#3c75c0', 700: '#2b568d' },
    ok:     { 50: '#e8f4ec', 100: '#c8e3d1', 500: '#3f8459', 700: '#2d6040' },
    danger: { 50: '#fbe9e7', 500: '#b9381f' },
  },
  backgroundColor: {
    'fieldcraft-row-hover': 'var(--fieldcraft-primary-alpha-08)',
    'fieldcraft-icon-hover': 'var(--fieldcraft-primary-alpha-10)',
  },
  backgroundImage: {
    'fieldcraft-hero': 'var(--fieldcraft-grad-hero)',
  },
  fontFamily: {
    // var()-backed (not literal font stacks) so font-display/font-body resolve
    // per-scope: Satoshi/Inter in .leads-customers-tab, Poppins in .customer-portal-root.
    display: ['var(--fieldcraft-font-display)'],
    body: ['var(--fieldcraft-font-body)'],
    // The Hub scope does not define --fieldcraft-*; the var() fallbacks carry the handoff
    // stacks there, while the Leads scope keeps resolving through its own variables.
    sans: ['var(--fieldcraft-font-body, Inter)', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
    mono: ['var(--fieldcraft-font-mono, "JetBrains Mono")', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
  },
  boxShadow: {
    'fieldcraft-low': 'var(--fieldcraft-shadow-low)',
    'fieldcraft-med': 'var(--fieldcraft-shadow-med)',
    'fieldcraft-high': 'var(--fieldcraft-shadow-high)',
    'fieldcraft-focus': 'var(--fieldcraft-shadow-focus)',
    bubble: '0 12px 32px -8px rgb(0 0 0 / 0.25), 0 2px 6px -1px rgb(0 0 0 / 0.10)',
    rail: '0 1px 0 rgb(0 0 0 / 0.04)',
  },
  transitionTimingFunction: {
    'fieldcraft-out': 'var(--fieldcraft-ease-out, cubic-bezier(0.16, 1, 0.3, 1))',
    spring: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
  },
  borderRadius: {
    fieldcraft: '12px',
  },
};

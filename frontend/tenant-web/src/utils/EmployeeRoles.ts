import i18next from 'i18next';

export const getRolesDisplayName = (roles: string | null | undefined): string =>
    roles ? i18next.t(`role.${roles}`, { defaultValue: roles }) : '';

/**
 * Builds the role suffix for the browser tab title. `userSession.role` is an
 * array of raw role tokens (e.g. ["admin","sales","foreman","labour"]); route
 * each through getRolesDisplayName so the tab shows US-English display names
 * ("Labor", not "labour") instead of raw tokens. Accepts an array, a single
 * token, or null/undefined and returns "" when there is nothing to show.
 * AB#28141 — labor i18n migration.
 */
export const getRolesTitle = (role: unknown): string => {
  const tokens = Array.isArray(role) ? role : role ? [role] : [];
  return tokens
    .map((token) => getRolesDisplayName(String(token)))
    .filter(Boolean)
    .join(', ');
};

/* ================================================================
   Avatar Helper Functions
   Used for generating initials and colors for employee avatars
   ================================================================ */
 
export const AVATAR_COLORS = [
  '#3b82f6', '#10b981', '#8b5cf6', '#ef4444', '#f59e0b',
  '#06b6d4', '#ec4899', '#6366f1', '#14b8a6', '#f97316',
];
 
export const getAvatarColor = (name: string): string => {
  if (!name) return AVATAR_COLORS[0];
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
};
 
export const getInitials = (firstName: string, lastName: string): string => {
  const f = (firstName || '').trim()[0] || '';
  const l = (lastName || '').trim()[0] || '';
  return (f + l).toUpperCase() || '?';
};
 
export const getStatusClass = (status: string): string => {
  const s = (status || '').toLowerCase();
  if (s === 'active') return 'active';
  if (s === 'inactive') return 'inactive';
  if (s === 'invited') return 'invited';
  return 'inactive';
};
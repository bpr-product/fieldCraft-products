// notesUtils — parsing/serialization for the Task Notes feature.
//
// Notes are persisted in the first-class EAV `notes` task attribute as a
// serialized JSON array of note objects (see docs/gantt/06-task-notes-*). The
// backend surfaces that raw string on the task GET (`TaskDTO.notes`) and the
// add-note endpoint returns the updated array. These helpers convert to/from the
// UI shape, parsing defensively so a legacy plain-string value never crashes.

export interface TaskNote {
  /** Client- or server-generated unique id (React key / de-dupe handle). */
  id: string;
  /** Note content (trimmed). */
  text: string;
  /** Display name of the author, or null when unknown (legacy notes). */
  authorName: string | null;
  /** Author's Azure AD `oid` — used for stable avatar coloring. */
  authorId: string | null;
  /** ISO 8601 UTC timestamp, or null for legacy notes with no timestamp. */
  createdAt: string | null;
}

let idCounter = 0;

/** Best-effort unique id for a client-side (optimistic) note. */
export const generateNoteId = (): string => {
  const cryptoObj = typeof globalThis !== "undefined" ? (globalThis as any).crypto : undefined;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    return cryptoObj.randomUUID();
  }
  idCounter += 1;
  return `note-${Date.now()}-${idCounter}`;
};

const normalizeNote = (raw: any, index: number): TaskNote => {
  const obj = raw && typeof raw === "object" ? raw : {};
  const id =
    obj.id != null && String(obj.id).length > 0 ? String(obj.id) : `note-${index}-${generateNoteId()}`;
  return {
    id,
    text: obj.text != null ? String(obj.text) : "",
    authorName: obj.authorName != null && String(obj.authorName).length > 0 ? String(obj.authorName) : null,
    authorId: obj.authorId != null && String(obj.authorId).length > 0 ? String(obj.authorId) : null,
    createdAt: obj.createdAt != null && String(obj.createdAt).length > 0 ? String(obj.createdAt) : null,
  };
};

/** Wrap a non-array legacy value (plain string) as a single note with unknown author. */
const legacyNote = (text: string): TaskNote[] => {
  if (!text || !text.trim()) return [];
  return [{ id: generateNoteId(), text, authorName: null, authorId: null, createdAt: null }];
};

/**
 * Sort notes newest → oldest by `createdAt`. Notes without a timestamp
 * (legacy) sort to the end. Ties (identical/missing timestamps) keep
 * later-appended notes first, so freshly-added notes never trail behind
 * same-instant siblings.
 */
export const sortNotesByNewest = (list: TaskNote[]): TaskNote[] =>
  list
    .map((note, index) => ({ note, index }))
    .sort((a, b) => {
      const aTime = a.note.createdAt ? new Date(a.note.createdAt).getTime() : -Infinity;
      const bTime = b.note.createdAt ? new Date(b.note.createdAt).getTime() : -Infinity;
      return aTime !== bTime ? bTime - aTime : b.index - a.index;
    })
    .map(({ note }) => note);

const parseNotesUnsorted = (raw: string | any[] | null | undefined): TaskNote[] => {
  if (raw == null) return [];
  if (Array.isArray(raw)) return raw.map(normalizeNote);
  if (typeof raw !== "string") return [];
  const trimmed = raw.trim();
  if (!trimmed) return [];
  try {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed)) return parsed.map(normalizeNote);
    // Parsed to something that isn't an array — fall back to legacy handling
    // using the ORIGINAL string so quoted JSON strings render as their content.
    return legacyNote(typeof parsed === "string" ? parsed : trimmed);
  } catch {
    return legacyNote(trimmed);
  }
};

/**
 * Parse a stored notes value (string from `TaskDTO.notes`, an already-parsed
 * array, or null) into a structured note list, newest note first.
 *
 * - JSON array  → structured notes
 * - non-array string → one legacy note (text = the string)
 * - empty / null / unparseable → empty list
 */
export const parseNotes = (raw: string | any[] | null | undefined): TaskNote[] =>
  sortNotesByNewest(parseNotesUnsorted(raw));

/** Build an optimistic note for immediate display before the server confirms. */
export const buildLocalNote = (
  text: string,
  author: { name?: string | null; oid?: string | null }
): TaskNote => ({
  id: generateNoteId(),
  text: text.trim(),
  authorName: author.name != null && author.name.length > 0 ? author.name : null,
  authorId: author.oid ?? null,
  createdAt: new Date().toISOString(),
});

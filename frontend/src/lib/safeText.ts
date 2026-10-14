/**
 * Defensive last line of defence for text shown to a person. The backend already sends fixed, safe
 * sentences; this only catches anything unexpected: file paths, exception names, stack traces, key-like strings.
 */
export const GENERIC_ERROR = 'Something went wrong. Details are in the server log.'

// Two or more directory components after a leading "/", "~/" or a drive letter. Spaces inside folder names are allowed.
const PATH = /(?:[A-Za-z]:\\|~?\/)(?:[^\\/:*?"<>|\n]+[\\/]){2,}[^\s\\/:*?"<>|]*/g
const EXCEPTION = /\bTraceback\b|\b[A-Z][A-Za-z0-9_]*(?:Error|Exception)\b|\bat\s+\S+\s+\([^)]*:\d+:\d+\)/
const SECRET = /\b(?:sk|pk|rk)-[A-Za-z0-9_-]{8,}|\bbearer\s+\S{8,}/i

export function safeText(text: string, fallback: string = GENERIC_ERROR): string {
  if (EXCEPTION.test(text) || SECRET.test(text)) return fallback
  return text.replace(PATH, '[path hidden]')
}

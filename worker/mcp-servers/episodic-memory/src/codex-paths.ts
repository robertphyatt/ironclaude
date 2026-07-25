/**
 * Munge an absolute cwd into the same project-directory key Claude Code uses under
 * ~/.claude/projects/<key>/. Grounded against real keys (2026-07-21):
 *   /Users/roberthyatt/Code/ironclaude -> -Users-roberthyatt-Code-ironclaude
 *   /Users/roberthyatt/.ironclaude     -> -Users-roberthyatt--ironclaude
 * Claude replaces each '/' and '.' with '-' per character (separators are NOT
 * collapsed, so '/.' becomes '--'). Verified against `ls ~/.claude/projects`.
 */
export function mungeCwd(cwd: string): string {
  return cwd.replace(/[/.]/g, '-');
}

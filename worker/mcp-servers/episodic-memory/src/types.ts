export interface ToolCall {
  id: string;
  exchangeId: string;
  toolName: string;
  toolInput?: any;
  toolResult?: string;
  isError: boolean;
  timestamp: string;
}

export interface ConversationExchange {
  id: string;
  project: string;
  timestamp: string;
  userMessage: string;
  assistantMessage: string;
  archivePath: string;
  lineStart: number;
  lineEnd: number;

  // Conversation structure
  parentUuid?: string;
  isSidechain?: boolean;

  // Session context
  sessionId?: string;
  cwd?: string;
  gitBranch?: string;
  claudeVersion?: string;

  // Thinking metadata
  thinkingLevel?: string;
  thinkingDisabled?: boolean;
  thinkingTriggers?: string; // JSON array

  // Provenance (L2): Claude `entrypoint` (cli, sdk-py, sdk-cli) or Codex
  // session_meta.payload.originator (Codex Desktop, codex_exec, ironclaude).
  entrypoint?: string;
  // operator | brain | worker | machine | archive-only | unknown (settings.ts classifySession)
  sessionClass?: string;
  // True when userMessage holds AskUserQuestion answers ("Q: … / A: …").
  isAnswer?: boolean;

  // Tool calls (populated separately)
  toolCalls?: ToolCall[];
}

export interface SearchResult {
  exchange: ConversationExchange;
  similarity: number;
  snippet: string;
}

export interface MultiConceptResult {
  exchange: ConversationExchange;
  snippet: string;
  conceptSimilarities: number[];
  averageSimilarity: number;
}

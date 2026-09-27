/**
 * Typed API contracts shared by the web app (SPECIFICATIONS.md §33–§35).
 *
 * Hand-maintained to match the DocuLens OpenAPI document until generation is wired.
 * Answer text, citation quotes and document metadata remain untrusted and must be
 * rendered as text, never as HTML (§53).
 */

export interface ApiErrorDetail {
  /** Dotted path to the offending field, e.g. `query.limit`. */
  location: string;
  message: string;
  type: string;
}

export interface ApiErrorBody {
  /** Stable, machine-readable error code, e.g. `DOCUMENT_NOT_READY`. */
  code: string;
  /** Safe, user-facing message. Never contains internal exception details. */
  message: string;
  /** Correlation identifier echoed from the request-ID response header (§36). */
  request_id: string;
  /** Present for validation failures and selected domain conflicts (e.g. duplicate upload). */
  details?: ApiErrorDetail[];
}

export interface ApiErrorResponse {
  error: ApiErrorBody;
}

export type UserStatus = "ACTIVE" | "SUSPENDED" | "DELETED";

export interface User {
  id: string;
  email: string;
  status: UserStatus;
  created_at: string;
  last_login_at: string | null;
}

export interface CredentialsRequest {
  email: string;
  password: string;
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "Bearer";
  expires_in: number;
}

/** Document processing pipeline (§7.3, ADR-019). */
export type ProcessingStatus =
  | "UPLOADED"
  | "VALIDATING"
  | "EXTRACTING"
  | "CHUNKING"
  | "EMBEDDING"
  | "INDEXING"
  | "READY"
  | "FAILED"
  | "DELETING"
  | "DELETED";

export interface Document {
  id: string;
  collection_id: string | null;
  filename: string;
  mime_type: string;
  file_size: number;
  page_count: number | null;
  processing_status: ProcessingStatus;
  processing_error: string | null;
  chunk_count: number;
  created_at: string;
  updated_at: string;
  indexed_at: string | null;
  /** Opaque PDF/extraction metadata; treat values as untrusted text (§53). */
  metadata: Record<string, unknown>;
}

export interface UpdateDocumentRequest {
  filename?: string;
  /** Omit to leave unchanged; `null` detaches from the current collection. */
  collection_id?: string | null;
}

export interface ListDocumentsParams {
  collection_id?: string;
  /** Literal case-insensitive filename substring. */
  q?: string;
}

export interface Collection {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateCollectionRequest {
  name: string;
  description?: string | null;
}

export interface UpdateCollectionRequest {
  name?: string;
  /** Omit to leave unchanged; `null` clears the description. */
  description?: string | null;
}

export type MessageRole = "USER" | "ASSISTANT" | "SYSTEM";

export type AnswerOutcome = "answered" | "insufficient_evidence" | "blocked";

export interface Conversation {
  id: string;
  collection_id: string | null;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface CreateConversationRequest {
  title: string;
  collection_id?: string | null;
}

export interface UpdateConversationRequest {
  title?: string;
  /** Omit to leave unchanged; `null` detaches from the collection. */
  collection_id?: string | null;
}

/**
 * Citation evidence returned with assistant messages (§23).
 * Filename is not on the wire — resolve via `document_id` against the documents API.
 */
export interface Citation {
  id: string;
  document_id: string;
  page_number: number;
  chunk_id: string | null;
  quoted_text: string;
  retrieval_score: number;
  reranking_score: number | null;
  citation_order: number;
}

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  created_at: string;
  citations: Citation[];
}

export interface AskRequest {
  question: string;
  /** When set, narrows retrieval to these documents for this question only. */
  document_ids?: string[];
}

export interface RetrievalMetadata {
  query: string;
  rewritten: boolean;
  retriever: string;
  documents_in_scope: number;
  hits: number;
  evidence: number;
  context_items: number;
  reranking: string;
  model: string | null;
  prompt_version: string | null;
  invalid_references: number;
  truncated: boolean;
  uncited: boolean;
}

export interface UsageMetadata {
  embedding_requests: number;
  embedding_tokens: number | null;
  llm_requests: number;
  input_tokens: number | null;
  output_tokens: number | null;
}

export interface TimingMetadata {
  rewrite_ms: number;
  retrieval_ms: number;
  generation_ms: number;
  persistence_ms: number;
  total_ms: number;
}

export interface AnswerResponse {
  conversation_id: string;
  outcome: AnswerOutcome;
  user_message: Message;
  assistant_message: Message;
  retrieval: RetrievalMetadata;
  usage: UsageMetadata;
  timing: TimingMetadata;
}

/** One SSE payload from `POST .../messages/stream` (§44). */
export type AnswerStreamEvent =
  | { type: "delta"; text: string }
  | { type: "final"; answer: AnswerResponse }
  | { type: "error"; error: ApiErrorBody };

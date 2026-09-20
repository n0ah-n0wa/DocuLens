# ADR-003 — Chunking strategy

**Status:** Accepted (2026-09-20) · **Resolves provisionally:** OQ-16 (tokenizer) · **Refs:** §7.5,
§14, §20, §23, §32, §49.

## Context

Chunks must be configurable (`CHUNK_SIZE`, `CHUNK_OVERLAP`, `MIN_CHUNK_SIZE`), keep document id,
page number, chunk index, source text and token count, carry enough metadata to rebuild a
citation (§7.5, §23), prefer semantic boundaries over blind fixed-size cuts (§14), and be stable
enough that re-indexing upserts instead of duplicating vectors (§32, §49). Token counts are
model-specific and the embedding provider is not yet chosen (OQ-16, OQ-17).

## Decision

| Concern        | Decision                                                                                                                                                                                                                                                                                                            |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Unit of work   | `doculens.domain.chunking.chunk_document`: pure, deterministic, over the persisted `DocumentPage` rows; run by the processor's `CHUNKING` stage, after which a document rests in `EMBEDDING`                                                                                                                        |
| Page scope     | A chunk never spans pages: every chunk has one `page_number` and one `page_id`, so a citation always resolves to a page (§23)                                                                                                                                                                                       |
| Structure      | Blank lines delimit paragraphs; a short single line that is numbered, upper-case or title-case (no sentence punctuation) is a heading and starts a section; a section smaller than `MIN_CHUNK_SIZE` (a lone heading) folds into the next one                                                                        |
| Splitting      | Whole paragraphs are packed until `CHUNK_SIZE` tokens; a paragraph over budget is split at sentence ends; only a sentence over budget is cut into fixed token windows; a trailing piece below `MIN_CHUNK_SIZE` joins its predecessor when that stays within `CHUNK_SIZE + MIN_CHUNK_SIZE`; content is never dropped |
| Budget         | `CHUNK_SIZE` is a hard maximum per chunk that includes the overlap borrowed from the previous chunk; a tail below `MIN_CHUNK_SIZE` merges into its predecessor only when the result still fits                                                                                                                      |
| Overlap        | `CHUNK_OVERLAP` tokens of the previous chunk are prepended to the next one within the same section (capped at half the previous chunk); never across sections or pages; recorded as `overlap_tokens`                                                                                                                |
| Source text    | `chunk.text == page.extracted_text[char_start:char_end]` verbatim; offsets, `section_title`, `split` (`paragraph` / `sentence` / `window`), `overlap_tokens`, `content_hash` and the chunking configuration are the chunk metadata                                                                                  |
| Identity       | `chunk.id = uuid5(namespace, f"{document_id}:{chunk_index}")` with document-wide indexes in page order; re-chunking upserts by id and deletes ids outside the new set (`replace_chunks`), so citations pointing at unchanged chunks survive and vectors can be upserted (§32)                                       |
| Tokens (OQ-16) | Provisional `RegexTokenizer` (`regex-v1`): word runs, single punctuation marks and single ideographic / kana / hangul characters; dependency-free and platform-independent. The embedding provider's tokenizer can replace it behind the `Tokenizer` port; the tokenizer name travels with every chunk              |
| Staleness      | `documents.metadata.chunking` records the configuration, tokenizer and chunk count used; a mismatch with the current settings marks the document as needing re-indexing (§32)                                                                                                                                       |
| Configuration  | `CHUNK_SIZE=512`, `CHUNK_OVERLAP=64`, `MIN_CHUNK_SIZE=64` by default; validated together at start-up                                                                                                                                                                                                                |

## Alternatives considered

- **Fixed-size character windows.** Simplest and what §14 explicitly warns against; loses
  paragraph and sentence boundaries that matter for retrieval quality and citation readability.
- **A model tokenizer now (tiktoken or a provider SDK).** Ties the domain to a provider before
  OQ-17 is decided and adds a dependency to the core; the port keeps that swap local.
- **Cross-page chunks for continuity.** Better context at page breaks but a chunk would need a
  page range, which §7.5 and §23 do not model; overlap within a page covers most of the loss.
- **Random or content-hash chunk ids.** Content hashes change on every re-chunk and would break
  citations and duplicate vectors; index-derived ids keep both stable when content is unchanged.

## Consequences

- Chunk sizes are approximate with respect to any given embedding model until its tokenizer is
  plugged in; the configured budget should leave headroom below the model's context limit.
- Re-chunking with a different configuration keeps ids for indexes that still exist and removes
  the rest; citations attached to removed chunks lose their `chunk_id` (already the OQ-7 rule).
- Embedding (ADR-004) consumes the persisted chunks; a document in `EMBEDDING` is its input.

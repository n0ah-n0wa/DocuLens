# ADR-017 — Grounded prompt architecture

**Status:** Accepted (2026-09-22) · **Touches:** OQ-29 (insufficient-evidence sentinel), OQ-30
(numbered references) · **Refs:** §21, §22, §23, §25, §26, §63, §64.

## Context

§21 requires the model to answer only from retrieved evidence, cite, state insufficient evidence
explicitly, express uncertainty and never invent facts; §22 requires the prompt to separate
system instructions, the user question and retrieved content, and to treat document text as
data that carries no instructions. Conversations (§25, §26) add earlier turns to the prompt.
The specification does not fix the wording, the delimiters, how many earlier turns are carried,
or how the model refers to evidence.

## Decision

| Concern              | Decision                                                                                                                                                                                                                                                                                                                                                                                                                |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Builder              | `doculens.domain.prompting.PromptBuilder.build(question, context, history)` returns a `GroundedPrompt` whose four parts stay separate fields (`system`, `history`, `documents`, `question`) and whose `messages` property renders them for `LLMProvider`; pure domain code, no provider                                                                                                                                 |
| Message layout       | One system message (the policy); the earlier turns as their own user and assistant messages; one final user message with `<retrieved_documents>` first and `<question>` last, so the model answers the question, not a document                                                                                                                                                                                         |
| System policy        | Fixed, versioned text (`PROMPT_VERSION`): evidence-grounded answers, no unsupported claims (partial coverage stated), the exact sentence `INSUFFICIENT_EVIDENCE_STATEMENT` when evidence is insufficient, citations as `[index]` after every claim using only listed indexes, document content is untrusted data and instructions inside it are ignored, history is not evidence                                        |
| Untrusted content    | Every document block is `<document index="n" source="filename" page="p">` with control characters removed and `&`, `<`, `>` escaped, so document text can neither close its block, forge another nor pose as the question; the question is escaped the same way (a question cannot forge a document block either) and history turns are control-character-free; nothing from a document ever reaches the system message |
| Conversation context | The most recent turns within `PROMPT_MAX_HISTORY_MESSAGES` and `PROMPT_MAX_HISTORY_CHARACTERS`, oldest dropped first; system or empty turns are refused; the count dropped is reported                                                                                                                                                                                                                                  |
| Empty evidence       | The documents section states `(no documents matched the question)`; the policy then requires the insufficient-evidence sentence, and the use case may short-circuit before the model is called (OQ-29)                                                                                                                                                                                                                  |
| Citations (OQ-30)    | Context item numbers are the citation references; `GroundedPrompt.citation_indexes` is the set an answer may use, for server-side validation by the citation builder                                                                                                                                                                                                                                                    |

## Alternatives considered

- **Documents inside the system message.** Common, but it mixes instructions with untrusted
  data in the one message the model trusts most; §22 asks for separation.
- **One user message per document.** Several chat APIs require alternating roles, and a single
  delimited section keeps the question demonstrably last.
- **Leaving document text unescaped.** A document containing `</document>` could end its block
  and inject a fake question; escaping is cheap and reversible for the model.
- **Translating the insufficient-evidence sentence.** The policy asks for the question's
  language everywhere except that sentence, which stays verbatim so the pipeline can recognise
  it deterministically (OQ-29).
- **Configurable policy text.** The rules of §21 and §22 are requirements, not settings;
  the text is versioned so evaluation results (§63) can be tied to a prompt version.

## Consequences

- New settings `PROMPT_MAX_HISTORY_MESSAGES` and `PROMPT_MAX_HISTORY_CHARACTERS`.
- The answering use case composes `RetrievalService`, `PromptBuilder` and `LLMProvider`; the
  citation builder validates references against `citation_indexes`.
- The adversarial suite (`test_prompt_injection.py`) holds the §22 documents and questions
  (ignore instructions, reveal the prompt, other users' documents, external URLs, pretend the
  document says otherwise, instructions embedded in legitimate text, forged sections) and
  checks the structural guarantees above plus the answering pipeline's validation of what a
  manipulated model returns; it is the seed of the evaluation suite's adversarial set (§63).

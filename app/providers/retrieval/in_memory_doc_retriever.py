"""In-memory document retriever using keyword matching.

Scores ``SchemaDocument`` and ``ExampleDocument`` items from a
``DocumentCorpus`` against the user query using simple keyword overlap,
mirroring the existing ``InMemoryRetriever`` approach for the structured
metadata layer.

Scoring
=======

Exact match (token appears in normalized field text):

* Schema docs:  title +8, content +3, tag +5, table_name +10.
* Examples:     question +6, tag +5, table +10, explanation +2.

Substring fallback (query token >= 4 chars, any field word is contained
in the query token — handles Turkish agglutinative suffixes):

* Schema docs:  title +3, content +1, table_name +4, tag +2.
* Examples:     question +3, tag +2, table +4, explanation +1.
"""

from __future__ import annotations

import re

from app.providers.documents.models import (
    DocumentCorpus,
    ExampleDocument,
    SchemaDocument,
)
from app.providers.retrieval.base import DocumentRetriever, DocumentRetrievalResult
from app.utils.turkish import casefold_tr

# Pre-compiled pattern for punctuation removal during tokenization.
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

# ---------------------------------------------------------------------------
# Exact match scores
# ---------------------------------------------------------------------------

_EXACT_TABLE_SCORE: int = 10
_EXACT_TITLE_SCORE: int = 8
_EXACT_TAG_SCORE: int = 5
_EXACT_CONTENT_SCORE: int = 3
_EXACT_QUESTION_SCORE: int = 6
_EXACT_EXPLANATION_SCORE: int = 2

# ---------------------------------------------------------------------------
# Substring fallback scores (lower than exact)
# ---------------------------------------------------------------------------

_SUBSTR_TABLE_SCORE: int = 4
_SUBSTR_TITLE_SCORE: int = 3
_SUBSTR_TAG_SCORE: int = 2
_SUBSTR_CONTENT_SCORE: int = 1
_SUBSTR_QUESTION_SCORE: int = 3
_SUBSTR_EXPLANATION_SCORE: int = 1

# Minimum lengths for substring fallback
_MIN_SUBSTR_TOKEN_LEN: int = 4
_MIN_SUBSTR_WORD_LEN: int = 3


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split and drop empty tokens."""
    folded = casefold_tr(text)
    cleaned = _PUNCT_RE.sub(" ", folded)
    return [t for t in cleaned.split() if t]


def _contains_token(token: str, folded_text: str) -> bool:
    """Check if *token* appears as a substring in already-folded *text*."""
    return bool(folded_text) and token in folded_text


def _contains_substring(token: str, folded_text: str) -> bool:
    """Controlled substring fallback for Turkish agglutinative forms.

    For query tokens >= ``_MIN_SUBSTR_TOKEN_LEN``, checks if any word
    from *folded_text* (length >= ``_MIN_SUBSTR_WORD_LEN``) appears as
    a substring of *token*.  E.g. query token ``çalışanları`` matches
    field word ``çalışan``.
    """
    if len(token) < _MIN_SUBSTR_TOKEN_LEN or not folded_text:
        return False
    for word in _PUNCT_RE.sub(" ", folded_text).split():
        if len(word) >= _MIN_SUBSTR_WORD_LEN and word != token and word in token:
            return True
    return False


class InMemoryDocumentRetriever(DocumentRetriever):
    """Keyword-based retriever over a ``DocumentCorpus``."""

    def __init__(self, corpus: DocumentCorpus) -> None:
        self._corpus = corpus

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        user_query: str,
        *,
        top_k_docs: int = 5,
        top_k_examples: int = 3,
    ) -> DocumentRetrievalResult:
        tokens = _tokenize(user_query)
        if not tokens:
            return DocumentRetrievalResult()

        # Score schema docs
        scored_docs = [
            (self._score_schema_doc(doc, tokens), doc)
            for doc in self._corpus.schema_docs
        ]
        scored_docs.sort(key=lambda p: p[0], reverse=True)
        selected_docs = [d for s, d in scored_docs[:top_k_docs] if s > 0]

        # Score examples
        scored_examples = [
            (self._score_example(ex, tokens), ex)
            for ex in self._corpus.examples
        ]
        scored_examples.sort(key=lambda p: p[0], reverse=True)
        selected_examples = [e for s, e in scored_examples[:top_k_examples] if s > 0]

        return DocumentRetrievalResult(
            schema_docs=selected_docs,
            examples=selected_examples,
        )

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _score_schema_doc(doc: SchemaDocument, tokens: list[str]) -> int:
        score = 0
        folded_title = casefold_tr(doc.title) if doc.title else ""
        folded_content = casefold_tr(doc.content) if doc.content else ""
        folded_table = casefold_tr(doc.table_name) if doc.table_name else ""
        folded_tags = [casefold_tr(t) for t in doc.tags]

        for token in tokens:
            # table_name: exact +10, substring fallback +4
            if folded_table:
                if _contains_token(token, folded_table):
                    score += _EXACT_TABLE_SCORE
                elif _contains_substring(token, folded_table):
                    score += _SUBSTR_TABLE_SCORE

            # title: exact +8, substring fallback +3
            if _contains_token(token, folded_title):
                score += _EXACT_TITLE_SCORE
            elif _contains_substring(token, folded_title):
                score += _SUBSTR_TITLE_SCORE

            # tags: exact +5, substring fallback +2
            for tag in folded_tags:
                if _contains_token(token, tag):
                    score += _EXACT_TAG_SCORE
                elif _contains_substring(token, tag):
                    score += _SUBSTR_TAG_SCORE

            # content: exact +3, substring fallback +1
            if _contains_token(token, folded_content):
                score += _EXACT_CONTENT_SCORE
            elif _contains_substring(token, folded_content):
                score += _SUBSTR_CONTENT_SCORE

        return score

    @staticmethod
    def _score_example(example: ExampleDocument, tokens: list[str]) -> int:
        score = 0
        folded_question = casefold_tr(example.question) if example.question else ""
        folded_explanation = (
            casefold_tr(example.explanation) if example.explanation else ""
        )
        folded_tables = [casefold_tr(t) for t in example.tables]
        folded_tags = [casefold_tr(t) for t in example.tags]

        for token in tokens:
            # tables: exact +10, substring fallback +4
            for table in folded_tables:
                if _contains_token(token, table):
                    score += _EXACT_TABLE_SCORE
                elif _contains_substring(token, table):
                    score += _SUBSTR_TABLE_SCORE

            # question: exact +6, substring fallback +3
            if _contains_token(token, folded_question):
                score += _EXACT_QUESTION_SCORE
            elif _contains_substring(token, folded_question):
                score += _SUBSTR_QUESTION_SCORE

            # tags: exact +5, substring fallback +2
            for tag in folded_tags:
                if _contains_token(token, tag):
                    score += _EXACT_TAG_SCORE
                elif _contains_substring(token, tag):
                    score += _SUBSTR_TAG_SCORE

            # explanation: exact +2, substring fallback +1
            if _contains_token(token, folded_explanation):
                score += _EXACT_EXPLANATION_SCORE
            elif _contains_substring(token, folded_explanation):
                score += _SUBSTR_EXPLANATION_SCORE

        return score

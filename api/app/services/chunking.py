from __future__ import annotations

import re
from typing import NamedTuple


class ChunkItem(NamedTuple):
    content: str
    token_estimate: int
    start_char: int
    end_char: int


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate: roughly 4 characters per token."""
    return max(1, len(text.strip()) // 4)


def split_into_chunks(
    text: str,
    target_tokens: int = 700,
    overlap_tokens: int = 100,
) -> list[ChunkItem]:
    """Split text into semantically coherent chunks of ~700 tokens with ~100 token overlap.

    Returns a list of ChunkItem instances with content, token_estimate, start_char, and end_char.
    """
    clean_text = text.strip()
    if not clean_text:
        return []

    target_chars = target_tokens * 4
    overlap_chars = overlap_tokens * 4

    # If the entire text fits comfortably in one chunk
    if len(clean_text) <= target_chars:
        return [
            ChunkItem(
                content=clean_text,
                token_estimate=estimate_tokens(clean_text),
                start_char=0,
                end_char=len(clean_text),
            )
        ]

    # Split into paragraphs/sections
    paragraphs = re.split(r"\n\s*\n", clean_text)
    raw_chunks: list[str] = []
    current_chunk = ""

    for para in paragraphs:
        para_clean = para.strip()
        if not para_clean:
            continue

        # If a single paragraph exceeds target_chars, split it by sentences
        if len(para_clean) > target_chars:
            sentences = re.split(r"(?<=[.!?])\s+", para_clean)
            for sentence in sentences:
                sentence_clean = sentence.strip()
                if not sentence_clean:
                    continue

                if not current_chunk:
                    current_chunk = sentence_clean
                elif len(current_chunk) + 1 + len(sentence_clean) <= target_chars:
                    current_chunk += " " + sentence_clean
                else:
                    raw_chunks.append(current_chunk)
                    overlap_point = max(0, len(current_chunk) - overlap_chars)
                    overlap_text = current_chunk[overlap_point:].strip()
                    current_chunk = (overlap_text + " " + sentence_clean).strip()
            continue

        if not current_chunk:
            current_chunk = para_clean
        elif len(current_chunk) + 2 + len(para_clean) <= target_chars:
            current_chunk += "\n\n" + para_clean
        else:
            raw_chunks.append(current_chunk)
            overlap_point = max(0, len(current_chunk) - overlap_chars)
            overlap_text = current_chunk[overlap_point:].strip()
            current_chunk = (overlap_text + "\n\n" + para_clean).strip()

    if current_chunk.strip():
        raw_chunks.append(current_chunk.strip())

    # Map raw chunks to ChunkItems with safe character offsets
    items: list[ChunkItem] = []
    search_cursor = 0
    for chunk_text in raw_chunks:
        # Find position of beginning of chunk in text
        match_pos = clean_text.find(chunk_text[:min(60, len(chunk_text))], search_cursor)
        if match_pos != -1:
            start_char = match_pos
            end_char = min(len(clean_text), start_char + len(chunk_text))
            search_cursor = start_char + 1
        else:
            start_char = search_cursor
            end_char = min(len(clean_text), start_char + len(chunk_text))

        items.append(
            ChunkItem(
                content=chunk_text,
                token_estimate=estimate_tokens(chunk_text),
                start_char=start_char,
                end_char=end_char,
            )
        )

    return items

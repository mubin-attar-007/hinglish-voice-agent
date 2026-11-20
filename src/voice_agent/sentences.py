"""Split streamed model text into sentences, so the gate can check each one before TTS.

A sentence ends at '.', '?', '!' or '।' followed by a space, or at a newline. Not inside a
number ("4.30") or after a known abbreviation ("p.m."). The same splitter runs on text mode's
streamed deltas and, in M2, inside the voice pipeline.
"""

from __future__ import annotations

_ENDINGS = ".?!।"
_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "rs", "approx", "etc", "vs"}
# "6 p.m. tak" goes on; "6 p.m. Which day?" starts a new sentence.
_TIME_ABBREVIATIONS = {"a.m", "p.m"}


class SentenceSplitter:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> list[str]:
        """Add streamed text; return the sentences it completed."""
        self._buffer += chunk
        sentences: list[str] = []
        start = 0
        i = 0
        text = self._buffer
        while i < len(text):
            char = text[i]
            if char == "\n" or char == "।":
                end = i + 1
            elif char in _ENDINGS:
                j = i
                while j + 1 < len(text) and text[j + 1] in _ENDINGS:
                    j += 1
                if j + 1 >= len(text):
                    break  # wait: more text may follow ("4." could become "4.30")
                if not text[j + 1].isspace():
                    i = j + 1
                    continue
                if char == ".":
                    word = self._word_before(text, i)
                    if word in _ABBREVIATIONS:
                        i = j + 1
                        continue
                    if word in _TIME_ABBREVIATIONS:
                        k = j + 1
                        while k < len(text) and text[k].isspace():
                            k += 1
                        if k >= len(text):
                            break  # wait: the next word decides
                        if not text[k].isupper():
                            i = j + 1
                            continue
                end = j + 1
            else:
                i += 1
                continue
            sentence = text[start:end].strip()
            if sentence:
                sentences.append(sentence)
            start = end
            i = end
        self._buffer = text[start:]
        return sentences

    @property
    def pending(self) -> str:
        """Text fed but not yet part of a complete sentence."""
        return self._buffer.strip()

    def flush(self) -> list[str]:
        """The rest of the text, at the end of the model's response."""
        rest, self._buffer = self._buffer.strip(), ""
        return [rest] if rest else []

    @staticmethod
    def _word_before(text: str, dot: int) -> str:
        word_start = max(text.rfind(" ", 0, dot), text.rfind("\n", 0, dot)) + 1
        return text[word_start:dot].lower()


def split_sentences(text: str) -> list[str]:
    splitter = SentenceSplitter()
    return splitter.feed(text) + splitter.flush()

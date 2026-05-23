import os
import tiktoken
from dotenv import load_dotenv

load_dotenv()


class Chunker:

    def __init__(self):
        encoding = os.getenv("TIKTOKEN_ENCODING", "cl100k_base")
        self.encoder = tiktoken.get_encoding(encoding)
        self.max_tokens = int(os.getenv("MAX_CHUNK_TOKENS", "128000"))
        self.overlap_sentences = int(os.getenv("CHUNK_OVERLAP_SENTENCES", "2"))

    def chunk_section(self, section: dict) -> list:
        text = section["text"]
        section_tag = section["section"]

        tokens = self.encoder.encode(text)

        if len(tokens) <= self.max_tokens:
            return [{
                "text": text,
                "section": section_tag,
                "chunk_index": 0,
                "total_chunks": 1
            }]

        return self._split_with_overlap(text, section_tag)

    def _split_with_overlap(self, text: str, section_tag: str) -> list:
        sentences = text.split(". ")
        chunks = []
        current_sentences = []
        current_tokens = 0
        chunk_index = 0

        for sentence in sentences:
            sentence_tokens = len(self.encoder.encode(sentence))

            if current_tokens + sentence_tokens > self.max_tokens:
                if current_sentences:
                    chunks.append({
                        "text": ". ".join(current_sentences) + ".",
                        "section": section_tag,
                        "chunk_index": chunk_index,
                        "total_chunks": -1
                    })
                    chunk_index += 1
                    current_sentences = current_sentences[-self.overlap_sentences:]
                    current_tokens = sum(
                        len(self.encoder.encode(s))
                        for s in current_sentences
                    )

            current_sentences.append(sentence)
            current_tokens += sentence_tokens

        if current_sentences:
            chunks.append({
                "text": ". ".join(current_sentences) + ".",
                "section": section_tag,
                "chunk_index": chunk_index,
                "total_chunks": -1
            })

        for chunk in chunks:
            chunk["total_chunks"] = len(chunks)

        return chunks

    def chunk_document(self, sections: list) -> list:
        all_chunks = []
        for section in sections:
            chunks = self.chunk_section(section)
            all_chunks.extend(chunks)
        return all_chunks
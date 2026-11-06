import re
from pathlib import PurePosixPath

from .models import Chunk, Document, digest

LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
}


def symbol_ranges(text: str, path: str) -> list[tuple[int, int]]:
    lines = text.splitlines(keepends=True)
    boundaries = {0, len(lines)}
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {".md", ".mdx", ".rst"}:
        in_fence = False
        for i, line in enumerate(lines):
            if line.lstrip().startswith(("```", "~~~")):
                in_fence = not in_fence
            if not in_fence and re.match(r"^#{1,6}\s", line):
                boundaries.add(i)
    elif suffix in LANGUAGES:
        from tree_sitter_language_pack import get_parser

        try:
            parser = get_parser(LANGUAGES[suffix])
            tree = parser.parse(text.encode())
            root = tree.root_node
            for node in root.named_children:
                if any(x in node.type for x in ("function", "class", "method", "declaration")):
                    boundaries.add(node.start_point.row)
                    boundaries.add(min(node.end_point.row + 1, len(lines)))
        except (LookupError, RuntimeError):
            # Unknown grammars retain line boundaries; token limits still apply.
            pass
    points = sorted(boundaries)
    return list(zip(points, points[1:]))


class Chunker:
    def __init__(self, tokenizer, model: str, tokens=384, overlap=64):
        self.tokenizer, self.model = tokenizer, model
        self.tokens, self.overlap = tokens, overlap

    def count(self, text: str) -> int:
        # Disable tokenizer truncation before measuring; actual embeddings add special tokens.
        return len(self.tokenizer.encode(text, add_special_tokens=True).ids)

    def split(self, doc: Document, ttl: int) -> list[Chunk]:
        lines = doc.text.splitlines(keepends=True)
        result = []
        for first, last in symbol_ranges(doc.text, doc.path):
            segment = "".join(lines[first:last])
            # Character offsets retain exact source text even for a single oversized line.
            encoding = self.tokenizer.encode(segment, add_special_tokens=False)
            if not encoding.ids:
                continue
            offsets = encoding.offsets
            width = self.tokens - 2
            cursor = 0
            while cursor < len(offsets):
                stop = min(cursor + width, len(offsets))
                a, b = offsets[cursor][0], offsets[stop - 1][1]
                part = segment[a:b]
                # Some tokenizers normalize text; re-encoding must still respect the limit.
                while self.count(part) > self.tokens and stop > cursor + 1:
                    stop -= 1
                    b = offsets[stop - 1][1]
                    part = segment[a:b]
                if self.count(part) > self.tokens:
                    raise RuntimeError("Tokenizer cannot produce a bounded chunk.")
                start = first + segment[:a].count("\n") + 1
                end = start + part.rstrip("\n").count("\n")
                content_hash = digest([self.model, doc.path, part])
                cid = digest([doc.source, content_hash, start, end, doc.commit])
                url = doc.url + (f"#L{start}-L{end}" if doc.repo else "")
                result.append(
                    Chunk(
                        id=cid,
                        source=doc.source,
                        text=part,
                        url=url,
                        path=doc.path,
                        start=start,
                        end=end,
                        fetched_at=doc.fetched_at,
                        expires_at=doc.fetched_at + ttl,
                        repo=doc.repo,
                        commit=doc.commit,
                        license=doc.license,
                        role=doc.role,
                        content_hash=content_hash,
                    )
                )
                if stop == len(offsets):
                    break
                cursor = max(cursor + 1, stop - self.overlap)
        return result

"""Turns a raw scraped docs chunk into readable Markdown (v1.1).

Why this exists: `scraper.chunk_html` serializes a Sphinx page with
`get_text("\\n", strip=True)`, which gives one line per text node -- no
blank lines, no fences, no table structure. Dropped verbatim into the
answer, that renders as one giant paragraph in which a `$ openstack ...`
command and its ASCII-art output table are just more words. This module
rebuilds the structure the chat renderer needs, purely from the chunk's own
text (so it works on chunks that are *already* in Qdrant -- no re-ingest):

- mojibake repair ("Â¶", "â\\x80\\x98" ... -- the corpus was ingested with the
  wrong response encoding, see `scraper.fetch_page`) and removal of the
  Sphinx permalink pilcrow;
- `$ command` lines -> fenced ```bash blocks (command plus any obvious
  `key : value` output that directly follows it);
- ASCII grid tables (`+---+` / `| a | b |`) -> real GFM tables;
- prose lines -> paragraphs (split after a sentence/colon once a paragraph
  gets long, and always around a command/table block);
- table-of-contents / index chunks (a wall of short link titles) are
  detected by `looks_like_index` so callers can drop them instead of
  showing navigation as if it were documentation.

Pure and deterministic on purpose: this sits between retrieval and the user
in an agent whose design rule (adr-0008 #5) is that operational text is
shown verbatim, never LLM-rewritten. Nothing here paraphrases -- it only
re-lays-out what the docs already said.
"""
import re

# ---------------------------------------------------------------- mojibake --

# cp1252 bytes 0x80-0x9f decode to printable characters (e.g. 0x80 -> "€").
# A UTF-8 text mis-decoded as cp1252 therefore shows "â€˜" where a text
# mis-decoded as latin-1 shows "â\x80\x98" -- map the former back onto the
# latter so one repair path handles both.
_CP1252_TO_LATIN1: dict[str, str] = {}
for _b in range(0x80, 0xA0):
    try:
        _CP1252_TO_LATIN1[bytes([_b]).decode("cp1252")] = chr(_b)
    except UnicodeDecodeError:
        pass  # 0x81/0x8d/0x8f/0x90/0x9d are undefined in cp1252

_CP1252_CLASS = "".join(re.escape(c) for c in _CP1252_TO_LATIN1)
# A UTF-8 lead byte (as a latin-1 char) followed by one or more continuation
# bytes (as latin-1 chars, or their cp1252 look-alikes).
_MOJIBAKE_RUN = re.compile(rf"[\u00c2-\u00f4][\u0080-\u00bf{_CP1252_CLASS}]+")


def _repair_run(match: re.Match) -> str:
    raw = match.group(0)
    latin1 = "".join(_CP1252_TO_LATIN1.get(ch, ch) for ch in raw)
    try:
        return latin1.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw  # a genuine "Ã©"-looking sequence that isn't mojibake


def fix_mojibake(text: str) -> str:
    """Repairs UTF-8 text that was decoded as latin-1/cp1252, run by run, so
    a string that mixes real non-ASCII characters with mojibake isn't
    wholesale rejected."""
    if not text:
        return text
    return _MOJIBAKE_RUN.sub(_repair_run, text)


def clean_inline(text: str) -> str:
    """For titles/headings: repair encoding and drop the Sphinx permalink
    marker, collapse whitespace."""
    text = fix_mojibake(text or "").replace("\u00b6", "")
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------ index chunks --

_INDEX_MIN_LINES = 20
_INDEX_MAX_AVG_WORDS = 4.5
_INDEX_MAX_SENTENCE_LINE_RATIO = 0.12


def looks_like_index(text: str) -> bool:
    """True for a table-of-contents / navigation chunk: many lines, each a
    handful of words, almost none ending like a sentence. Such a chunk can
    be semantically "close" to any question about the project (it names
    every subsystem) while containing no answer -- showing it is worse than
    showing nothing, so callers drop it."""
    lines = [ln.strip() for ln in fix_mojibake(text).splitlines() if ln.strip()]
    lines = [ln for ln in lines if not _is_table_line(ln)]
    if len(lines) < _INDEX_MIN_LINES:
        # A whitespace-flattened index (one enormous line) is still an
        # index: long, and with almost no sentence punctuation.
        flat = " ".join(lines)
        return len(flat) > 1500 and len(re.findall(r"[.!?:]\s", flat)) / max(len(flat.split()), 1) < 0.01
    avg_words = sum(len(ln.split()) for ln in lines) / len(lines)
    sentence_lines = sum(1 for ln in lines if re.search(r"[.!?:]$", ln))
    return avg_words <= _INDEX_MAX_AVG_WORDS and sentence_lines / len(lines) <= _INDEX_MAX_SENTENCE_LINE_RATIO


# ------------------------------------------------------------------ blocks --

_TABLE_LINE = re.compile(r"^(\+[-=+]+\+|\|.*\|)$")
_TABLE_RULE = re.compile(r"^\+[-=+]+\+$")
_COMMAND_LINE = re.compile(r"^\$(\s+\S.*)?$")
# `key : value` output that directly follows a command (ovn-sbctl list, ...).
_OUTPUT_LINE = re.compile(r"^[\w.\-]+\s*:\s+\S.*$")

_PARAGRAPH_SOFT_LIMIT = 280  # chars before a sentence end is allowed to break
_SENTENCE_END = re.compile(r"[.!?:]$")


_MAX_COMMAND_FRAGMENTS = 14
_QUOTES = re.compile("[\u201c\u201d\u2018\u2019]")


def _is_prose_line(line: str) -> bool:
    """A sentence rather than a command fragment: it ends the command."""
    words = line.split()
    if _QUOTES.search(line) or len(words) >= 9:
        return True
    if len(words) >= 3 and re.match(r"^[A-Z][a-z]+\s+[a-z]+", line):
        return True
    return len(words) >= 3 and line[0].isupper() and bool(_SENTENCE_END.search(line))


def _is_table_line(line: str) -> bool:
    return bool(_TABLE_LINE.match(line.strip()))


def _escape_cell(cell: str) -> str:
    return cell.replace("|", "\\|")


def _render_table(lines: list[str]) -> str:
    """ASCII grid table -> GFM table. Falls back to a fenced block when the
    grid can't be parsed into a rectangular header+rows shape, so nothing is
    ever silently dropped."""
    rows: list[list[str]] = []
    header_after = None  # index in `rows` where a `+===+` rule closed the header
    for ln in lines:
        s = ln.strip()
        if _TABLE_RULE.match(s):
            if "=" in s and rows and header_after is None:
                header_after = len(rows)
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        rows.append(cells)

    if not rows:
        return "```\n" + "\n".join(lines) + "\n```"

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = rows[0]
    body = rows[header_after:] if header_after and header_after > 1 else rows[1:]
    if header_after and header_after > 1:
        # Multi-line header cells: fold the header rows into one.
        header = [" ".join(filter(None, col)) for col in zip(*rows[:header_after])]

    out = [
        "| " + " | ".join(_escape_cell(c) for c in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    out += ["| " + " | ".join(_escape_cell(c) for c in r) + " |" for r in body]
    return "\n".join(out)


def _render_command(lines: list[str]) -> str:
    return "```bash\n" + "\n".join(lines) + "\n```"


def _split_blocks(text: str, heading: str | None) -> list[str]:
    lines = [ln.rstrip() for ln in text.splitlines()]

    # The chunk's first line is the section heading (scraper.chunk_html) --
    # the caller renders it as an actual heading, so don't repeat it as prose.
    if heading:
        want = clean_inline(heading)
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and clean_inline(lines[0]) == want:
            lines.pop(0)

    blocks: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(" ".join(para).strip())
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            flush()
            i += 1
            continue

        if _is_table_line(line):
            flush()
            grid = []
            while i < len(lines) and _is_table_line(lines[i].strip()):
                grid.append(lines[i].strip())
                i += 1
            blocks.append(_render_table(grid))
            continue

        if _COMMAND_LINE.match(line):
            flush()
            block: list[str] = []      # finished command / output lines
            frags: list[str] = []      # fragments of the command being built
            in_output = False
            while i < len(lines):
                cur = lines[i].strip()
                if not cur or _is_table_line(cur):
                    break
                if _COMMAND_LINE.match(cur):
                    if frags:
                        block.append(" ".join(frags))
                    frags, in_output = [cur], False
                elif in_output or (frags and _OUTPUT_LINE.match(cur) and " : " in cur):
                    if frags:
                        block.append(" ".join(frags))
                        frags = []
                    if not _OUTPUT_LINE.match(cur):
                        break
                    block.append(cur)
                    in_output = True
                elif frags and not _is_prose_line(cur) and len(frags) < _MAX_COMMAND_FRAGMENTS:
                    # Sphinx/Pygments put every token of a command in its own
                    # span, so the scraper stored "$", "openstack",
                    # "network agent list -c ID" as separate lines.
                    frags.append(cur)
                else:
                    break
                i += 1
            if frags:
                block.append(" ".join(frags))
            blocks.append(_render_command(block))
            continue

        para.append(line)
        i += 1
        # Break after a sentence/colon once the paragraph is long, or right
        # after a colon that introduces a command/table.
        joined_len = sum(len(p) for p in para)
        nxt = lines[i].strip() if i < len(lines) else ""
        introduces_block = line.endswith(":") and (_COMMAND_LINE.match(nxt) or _is_table_line(nxt))
        if introduces_block or (_SENTENCE_END.search(line) and joined_len >= _PARAGRAPH_SOFT_LIMIT):
            flush()

    flush()
    return [b for b in blocks if b]


def doc_text_to_markdown(text: str, heading: str | None = None, max_chars: int = 4000) -> str:
    """Readable Markdown for one docs chunk, capped at ~`max_chars` on a
    block boundary (never mid-table or mid-fence)."""
    cleaned = fix_mojibake(text).replace("\u00b6", "")
    blocks = _split_blocks(cleaned, heading)

    out: list[str] = []
    used = 0
    for block in blocks:
        if out and used + len(block) > max_chars:
            out.append("_…excerpt truncated — open the source page for the rest._")
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out)

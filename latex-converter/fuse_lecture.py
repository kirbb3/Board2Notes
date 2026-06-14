#!/usr/bin/env python3
"""Audio-visual fusion stage (Phase 4/5).

Merges the transcript-engine JSON (spoken explanations, timestamps, ★ exam
flags, soft emphasis) with the latex-converter board fragments (chalkboard
math transcribed to LaTeX, one fragment per board snapshot) into a single
textbook-quality LaTeX document:

- board math is the spine of the document, in chronological order
- the professor's spoken words become brief connective prose between the
  math blocks (rewritten textbook-style, not quoted verbatim)
- ★-flagged moments become highly visible "EXAM PRIORITY" callout boxes
- the model fixes ASR errors using context (e.g. "minimum wage spanning
  tree" → "minimum weight spanning tree")

The document is built SECTION BY SECTION rather than in one giant model
call: the lecture timeline is split into chunks of a few boards each (plus
the speech around them), and the model writes the LaTeX body for one chunk
at a time. This keeps every call small enough for a local model to handle
faithfully — a single whole-lecture call makes small models dump the raw
transcript, truncate, and loop. The preamble is fixed by us (not model
generated), so the document structure is always correct.

Backends are shared with convert_lecture.py (ollama / claude CLI).

Usage:
    python3 fuse_lecture.py <transcript.json> <fragments.json> \
        -o output/lecture --backend ollama --model gemma3:12b \
        --ollama-host http://<desktop-ip>:11434

`--dry-run` writes the chunk plan (how boards/paragraphs are grouped and the
exact slice text sent to the model) to <out>.plan.txt and exits without
calling any model.
"""

import argparse
import json
import os
import sys
import time

from convert_lecture import (
    ClaudeBackend,
    OllamaBackend,
    TS_RE,
    compile_tex,
    strip_fences,
    FIX_PROMPT,
)

# Fixed document head — WE control this, the model only writes body sections.
PREAMBLE = r"""\documentclass[11pt]{{article}}
\usepackage[margin=1in]{{geometry}}
\usepackage{{amsmath,amssymb,amsthm}}
\usepackage{{tikz}}
\usetikzlibrary{{positioning}}
\newtheorem{{theorem}}{{Theorem}}
\theoremstyle{{definition}}
\newtheorem*{{definition*}}{{Definition}}
\newenvironment{{exambox}}
  {{\par\medskip\noindent\begin{{center}}\begin{{minipage}}{{0.92\textwidth}}%
   \hrule height 1pt \vspace{{4pt}}\noindent\textbf{{$\bigstar$ EXAM PRIORITY.}} }}
  {{\vspace{{4pt}}\hrule height 1pt \end{{minipage}}\end{{center}}\medskip}}
\title{{{title}}}
\date{{{date}}}
\author{{}}
\begin{{document}}
\maketitle
"""

DOC_TAIL = "\n\\end{document}\n"

CHUNK_PROMPT = """You are writing ONE part of a textbook chapter \
reconstructed from a university lecture. Below is a slice of the lecture \
timeline, in chronological order:

- [BOARD ...] blocks: LaTeX transcriptions of chalkboard snapshots — the
  mathematical spine (definitions, theorems, proofs, derivations, diagrams).
  Consecutive boards in this slice OVERLAP HEAVILY: the same panel
  re-photographed as it was being written. Merge them — keep each distinct
  piece of math ONCE, at its fullest/most-complete state, in order.
- [SPOKEN ...] blocks: cleaned speech-to-text of what the professor said.
  ★ marks an explicit exam reference; (emphasis) marks softer emphasis.

Write the LaTeX BODY for this part of the chapter:

- The board math is the spine. Keep all distinct mathematical content, but
  deduplicate the overlapping board snapshots — do not repeat the same
  definition or theorem several times.
- Turn the speech into brief connective textbook prose (motivation,
  intuition, transitions). NEVER quote the professor verbatim, NEVER include
  the [SPOKEN]/[BOARD] markers or the timestamps, and drop administrative
  chatter (homework logistics, jokes, attendance, evaluations).
- Fix obvious speech-recognition errors from context — e.g. "a cyclic" ->
  "acyclic", "minimum wage spanning tree" -> "minimum weight spanning tree".
- For a ★ block, wrap the relevant point in the exambox environment with a
  one-line summary of what to know for the exam.
- Use \\section*{{...}} only when this slice clearly starts a new topic. Use
  the amsthm environments theorem / definition* / proof where the content is
  structured that way. Keep tikzpicture diagrams (deduplicated). If a board
  fragment's TikZ is clearly broken or trivial, draw a correct small diagram
  for the concept instead.
- Output ONLY the LaTeX body for this slice. NO \\documentclass, NO preamble,
  NO \\begin{{document}} or \\end{{document}}, NO markdown fences. It will be
  concatenated into a document that already loads amsmath, amssymb, amsthm,
  tikz (positioning) and defines the theorem, definition*, and exambox
  environments.

The timeline slice:

{slice}"""


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def fragment_time(name: str) -> int:
    """Timestamp in seconds encoded in a snapshot filename, e.g.
    board-07_0h25m55s.png → 1555."""
    m = TS_RE.search(name)
    if not m:
        return 0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def build_events(paragraphs: list[dict], fragments: dict[str, str]) -> list:
    """Interleave spoken paragraphs and board fragments chronologically.

    A board snapshot is taken right BEFORE a panel is erased, so its content
    was written over the minutes leading up to its timestamp. Sorting both
    sources by time keeps each snapshot after the speech that produced it.
    The secondary key puts a board just before speech at the same instant.
    """
    events = []
    for p in paragraphs:
        events.append((p["start"], 0, "speech", p))
    for name, latex in fragments.items():
        if latex.strip() == "EMPTY":
            continue
        events.append((fragment_time(name), -1, "board", (name, latex)))
    events.sort(key=lambda e: (e[0], e[1]))
    return events


def chunk_events(events: list, boards_per_chunk: int) -> list:
    """Group events into chunks of at most `boards_per_chunk` boards.

    Speech is carried along with the boards that follow it; a chunk closes
    after its Nth board, so each chunk holds a few overlapping board states
    plus the speech around them — small enough for one faithful model call,
    and scoped so the model can dedup the overlap within the chunk.
    """
    chunks, cur, boards = [], [], 0
    for ev in events:
        cur.append(ev)
        if ev[2] == "board":
            boards += 1
            if boards >= boards_per_chunk:
                chunks.append(cur)
                cur, boards = [], 0
    if cur:
        chunks.append(cur)
    return chunks


def render_slice(events: list) -> str:
    """Render a list of events to the [SPOKEN]/[BOARD] text the model sees."""
    lines = []
    for t, _, kind, payload in events:
        if kind == "speech":
            p = payload
            marks = ""
            if p.get("stars"):
                marks += " ★ (" + ", ".join(p["stars"]) + ")"
            if p.get("emphasis"):
                marks += " (emphasis)"
            lines.append(
                f"[SPOKEN {fmt_ts(p['start'])}–{fmt_ts(p['end'])}{marks}]"
            )
            lines.append(p["text"])
        else:
            name, latex = payload
            lines.append(f"[BOARD {name} — erased around {fmt_ts(t)}]")
            lines.append(latex)
        lines.append("")
    return "\n".join(lines)


def strip_body(text: str) -> str:
    """Salvage just the body from a model reply, even if it wrongly wrapped
    the output in a full document or fences."""
    text = strip_fences(text).strip()
    if "\\begin{document}" in text:
        text = text.split("\\begin{document}", 1)[1]
    if "\\end{document}" in text:
        text = text.split("\\end{document}", 1)[0]
    preamble_starts = (
        "\\documentclass", "\\usepackage", "\\usetikzlibrary",
        "\\newtheorem", "\\theoremstyle", "\\newenvironment",
        "\\title", "\\author", "\\date",
    )
    keep = [
        ln for ln in text.splitlines()
        if not (ln.strip().startswith(preamble_starts)
                or ln.strip() == "\\maketitle")
    ]
    return "\n".join(keep).strip()


def generate_body(backend, slice_text: str, retries: int = 4) -> str:
    """Call the model for one chunk, retrying transient failures (e.g. an
    Ollama HTTP 500 when the GPU is briefly out of memory)."""
    delay = 4
    for attempt in range(retries):
        try:
            return strip_body(
                backend.generate(CHUNK_PROMPT.format(slice=slice_text))
            )
        except Exception as e:  # noqa: BLE001 — surface after retries
            if attempt == retries - 1:
                raise
            print(f"    model call failed ({e}); retrying in {delay}s …",
                  flush=True)
            time.sleep(delay)
            delay *= 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("transcript", help="transcript-engine .json output")
    ap.add_argument("fragments", help="latex-converter .fragments.json")
    ap.add_argument("-o", "--out", default="output/fused",
                    help="output basename (writes <out>.tex and <out>.pdf)")
    ap.add_argument("--title", default=None,
                    help="document title (default: transcript title)")
    ap.add_argument("--date", default="",
                    help="document date line (default: empty)")
    ap.add_argument("--backend", choices=["ollama", "claude"],
                    default="ollama")
    ap.add_argument("--model", default="gemma3:12b",
                    help="model name (ollama tag, or claude model alias)")
    ap.add_argument("--ollama-host", default="http://localhost:11434")
    ap.add_argument("--num-ctx", type=int, default=8192,
                    help="ollama context window per chunk")
    ap.add_argument("--boards-per-chunk", type=int, default=4,
                    help="how many board snapshots per model call")
    ap.add_argument("--max-fixes", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true",
                    help="write <out>.plan.txt and exit (no model calls)")
    args = ap.parse_args()

    with open(args.transcript, encoding="utf-8") as f:
        transcript = json.load(f)
    with open(args.fragments, encoding="utf-8") as f:
        fragments = json.load(f)

    title = args.title or transcript.get("title", "Lecture Notes")
    events = build_events(transcript["paragraphs"], fragments)
    chunks = chunk_events(events, args.boards_per_chunk)

    n_board = sum(1 for _, _, k, _ in events if k == "board")
    n_star = sum(1 for p in transcript["paragraphs"] if p.get("stars"))
    print(f"{len(transcript['paragraphs'])} spoken paragraphs, "
          f"{n_board} board fragment(s), {n_star} ★ flag(s) "
          f"→ {len(chunks)} chunk(s) of ≤{args.boards_per_chunk} boards")

    out_base = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_base) or ".", exist_ok=True)

    if args.dry_run:
        path = out_base + ".plan.txt"
        with open(path, "w", encoding="utf-8") as f:
            for i, ch in enumerate(chunks, 1):
                nb = sum(1 for _, _, k, _ in ch if k == "board")
                ns = sum(1 for _, _, k, _ in ch if k == "speech")
                f.write(f"{'='*70}\nCHUNK {i}/{len(chunks)} — "
                        f"{nb} board(s), {ns} paragraph(s)\n{'='*70}\n")
                f.write(render_slice(ch) + "\n")
        print(f"dry run: wrote {path}")
        return 0

    if args.backend == "ollama":
        backend = OllamaBackend(args.model, args.ollama_host, args.num_ctx)
    else:
        backend = ClaudeBackend(None if args.model == "gemma3:12b"
                                else args.model)

    # Per-chunk body cache (resumable — a crash resumes, doesn't restart).
    bodies_path = out_base + ".bodies.json"
    bodies: dict[str, str] = {}
    if os.path.exists(bodies_path):
        with open(bodies_path, encoding="utf-8") as f:
            bodies = json.load(f)
        print(f"resuming: {len(bodies)} chunk(s) already fused")

    for i, ch in enumerate(chunks, 1):
        if str(i) in bodies:
            continue
        nb = sum(1 for _, _, k, _ in ch if k == "board")
        print(f"[{i}/{len(chunks)}] fusing chunk ({nb} board(s)) …",
              flush=True)
        bodies[str(i)] = generate_body(backend, render_slice(ch))
        with open(bodies_path, "w", encoding="utf-8") as f:
            json.dump(bodies, f, indent=1)

    ordered = [bodies[str(i)] for i in range(1, len(chunks) + 1)]
    tex = (PREAMBLE.format(title=title, date=args.date)
           + "\n\n".join(ordered) + DOC_TAIL)
    tex_path = out_base + ".tex"
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex)
    print(f"wrote {tex_path}")

    for attempt in range(args.max_fixes + 1):
        print(f"compiling (attempt {attempt + 1}) …", flush=True)
        comp = compile_tex(tex_path)
        if comp.returncode == 0:
            print(f"done: {out_base}.pdf")
            return 0
        if attempt == args.max_fixes:
            print("compile still failing; giving up. Last error:",
                  file=sys.stderr)
            print(comp.stderr[-2000:], file=sys.stderr)
            return 1
        print("compile failed — asking the model to fix …", flush=True)
        err_tail = (comp.stderr or comp.stdout)[-3000:]
        with open(tex_path, encoding="utf-8") as f:
            current = f.read()
        tex = strip_fences(backend.generate(
            FIX_PROMPT.format(error=err_tail, tex=current)
        ))
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex)

    return 1


if __name__ == "__main__":
    sys.exit(main())

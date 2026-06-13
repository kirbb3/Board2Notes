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
- soft-emphasis paragraphs are judged by the model in context (callout only
  if they clearly point at assessable material)
- the model fixes ASR errors using context (e.g. "minimum wage spanning
  tree" → "minimum weight spanning tree")

Backends are shared with convert_lecture.py (ollama / claude CLI).

Usage:
    python3 fuse_lecture.py <transcript.json> <fragments.json> \
        -o output/lecture --backend ollama --model gemma3:27b \
        --ollama-host http://<desktop-ip>:11434

`--dry-run` writes the interleaved timeline (the model's input) to
<out>.timeline.txt and exits without calling any model — useful to check
that board snapshots land between the right paragraphs.
"""

import argparse
import json
import os
import re
import sys

from convert_lecture import (
    ClaudeBackend,
    OllamaBackend,
    TS_RE,
    compile_tex,
    strip_fences,
    FIX_PROMPT,
)

FUSE_PROMPT = """You are reconstructing a university lecture as a textbook \
chapter. Below is a timeline that interleaves two sources, in chronological \
order:

- [BOARD ...] blocks: LaTeX transcriptions of chalkboard snapshots. This is
  the mathematical spine of the lecture: definitions, theorems, proofs,
  derivations, examples, and TikZ diagrams.
- [SPOKEN ...] blocks: what the professor said (cleaned ASR captions).
  Markers on spoken blocks: ★ means the professor explicitly referenced an
  exam ("on the final", "you should definitely know", ...); (emphasis) means
  softer emphasis wording was detected.

Write ONE complete, self-contained LaTeX document that reads like a textbook
chapter:

- The board math is the spine. Keep ALL distinct mathematical content from
  the boards: every definition, theorem, proof, derivation, example, and
  TikZ diagram, at its fullest state, each appearing once, in lecture order.
  Do not summarize or shorten the math. Consecutive board snapshots overlap
  heavily (the same panel at different stages); deduplicate, and use later
  duplicates to fill %OCCLUDED gaps in earlier ones.
- Use the SPOKEN blocks to write brief connective prose between the math:
  motivation, intuition, transitions, and any worked reasoning that never
  made it onto the board. Rewrite it as polished textbook prose — never
  quote the professor verbatim, and drop administrative chatter (homework
  logistics, jokes, attendance, course evaluations).
- The captions contain speech-recognition errors. Fix them from context
  (e.g. "minimum wage spanning tree" must become "minimum weight spanning
  tree"). Never let an obvious ASR error into the document.
- For every ★ SPOKEN block, wrap the related material in the exambox
  environment (defined in the preamble below) with a one-line summary of
  what the professor said to know for the exam. Place the box next to the
  math it refers to. For (emphasis) blocks, use your judgement: add an
  exambox only if the professor is clearly pointing at assessable material;
  otherwise just let the emphasis inform the prose.
- Structure the document with \\section* headings that follow the lecture's
  actual topics. Use amsthm environments (theorem, definition, proof) where
  the content is structured that way.
- Start the document EXACTLY with this preamble, then continue after
  \\maketitle:

\\documentclass[11pt]{{article}}
\\usepackage[margin=1in]{{geometry}}
\\usepackage{{amsmath,amssymb,amsthm}}
\\usepackage{{tikz}}
\\usetikzlibrary{{positioning}}
\\newtheorem{{theorem}}{{Theorem}}
\\theoremstyle{{definition}}
\\newtheorem*{{definition*}}{{Definition}}
\\newenvironment{{exambox}}
  {{\\par\\medskip\\noindent\\begin{{center}}\\begin{{minipage}}{{0.92\\textwidth}}%
   \\hrule height 1pt \\vspace{{4pt}}\\noindent\\textbf{{$\\bigstar$ EXAM PRIORITY.}} }}
  {{\\vspace{{4pt}}\\hrule height 1pt \\end{{minipage}}\\end{{center}}\\medskip}}
\\title{{{title}}}
\\date{{{date}}}
\\author{{}}

- Use no packages beyond those. The document must compile with pdflatex on
  the first try.
- Output ONLY the complete .tex source, starting with \\documentclass. No
  explanations, no markdown fences.

The timeline:

{timeline}"""


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


def build_timeline(paragraphs: list[dict], fragments: dict[str, str]) -> str:
    """Interleave spoken paragraphs and board fragments chronologically.

    A board snapshot is taken right BEFORE a panel is erased, so its content
    was written over the minutes leading up to its timestamp. Sorting both
    sources by time keeps each snapshot after the speech that produced it.
    """
    events = []
    for p in paragraphs:
        events.append((p["start"], 0, "speech", p))
    for name, latex in fragments.items():
        if latex.strip() == "EMPTY":
            continue
        # Tiebreak 1: a board state precedes speech at the same instant.
        events.append((fragment_time(name), -1, "board", (name, latex)))
    events.sort(key=lambda e: (e[0], e[1]))

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
    ap.add_argument("--model", default="gemma3:27b",
                    help="model name (ollama tag, or claude model alias)")
    ap.add_argument("--ollama-host", default="http://localhost:11434")
    ap.add_argument("--num-ctx", type=int, default=32768,
                    help="ollama context window for the fuse step")
    ap.add_argument("--max-fixes", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true",
                    help="write <out>.timeline.txt and exit (no model calls)")
    args = ap.parse_args()

    with open(args.transcript, encoding="utf-8") as f:
        transcript = json.load(f)
    with open(args.fragments, encoding="utf-8") as f:
        fragments = json.load(f)

    title = args.title or transcript.get("title", "Lecture Notes")
    timeline = build_timeline(transcript["paragraphs"], fragments)

    out_base = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_base) or ".", exist_ok=True)

    n_board = sum(1 for v in fragments.values() if v.strip() != "EMPTY")
    n_star = sum(1 for p in transcript["paragraphs"] if p.get("stars"))
    print(f"timeline: {len(transcript['paragraphs'])} spoken paragraphs, "
          f"{n_board} board fragment(s), {n_star} ★ flag(s)")

    if args.dry_run:
        path = out_base + ".timeline.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(timeline)
        print(f"dry run: wrote {path}")
        return 0

    if args.backend == "ollama":
        backend = OllamaBackend(args.model, args.ollama_host, args.num_ctx)
    else:
        backend = ClaudeBackend(None if args.model == "gemma3:27b"
                                else args.model)

    # LaTeX braces in the preamble are doubled for str.format; only the
    # three placeholders are single-braced.
    prompt = FUSE_PROMPT.format(title=title, date=args.date,
                                timeline=timeline)
    print("fusing transcript + boards …", flush=True)
    tex = strip_fences(backend.generate(prompt))
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

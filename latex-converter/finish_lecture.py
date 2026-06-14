#!/usr/bin/env python3
"""Claude 'finisher' stage.

Takes the rough, section-by-section draft produced by fuse_lecture.py (free,
local) and makes ONE Claude CLI pass to turn it into a clean, deduplicated,
compilable textbook chapter: merge the repeated theorems/definitions, fix the
broken LaTeX so it compiles, and replace trivial/broken TikZ diagrams with
sensible ones — while keeping all the (now correct) mathematics. Then compile
with tectonic, with up to a couple of Claude fix-up passes if needed.

This is the ONLY step that uses Claude; the heavy board vision + fusion stay
free on local Ollama. It drives the `claude` CLI in headless mode against
your Claude subscription, feeding the draft on stdin (so document size is not
limited by the command line).

Usage:
    python3 finish_lecture.py <fused.bodies.json | fused.tex> -o output/final \
        --title "My Lecture"
"""

import argparse
import json
import os
import subprocess
import sys

from convert_lecture import compile_tex, strip_fences
from fuse_lecture import PREAMBLE, DOC_TAIL

FINISH_INSTRUCTION = (
    "The text piped to you on stdin is a ROUGH, auto-generated LaTeX study "
    "guide reconstructed from a university discrete-math lecture (trees and "
    "spanning trees). The mathematics is mostly correct, but the draft has "
    "three problems you must fix:\n"
    "1. HEAVY REPETITION — the same theorem/definition is restated many "
    "times (it appeared on overlapping chalkboard photos). Merge duplicates "
    "so each distinct definition, theorem, proof, and example appears ONCE, "
    "in a logical order.\n"
    "2. BROKEN LATEX — e.g. 'exambox' is an ENVIRONMENT "
    "(\\begin{exambox}...\\end{exambox}), never a command \\exambox{...}; "
    "there are mismatched align/align* environments; etc. Fix everything so "
    "the document compiles cleanly with tectonic/pdflatex.\n"
    "3. TRIVIAL OR WRONG DIAGRAMS — many tikzpicture blocks are just straight "
    "lines of nodes, or use directed arrows on undirected graphs. Replace "
    "them with small, correct diagrams that actually illustrate the concept "
    "(a forest, a tree, a graph with a highlighted spanning tree, etc.).\n\n"
    "Requirements:\n"
    "- KEEP ALL distinct mathematical content. Do not drop theorems, proofs, "
    "or derivations. Do not shorten the math.\n"
    "- Produce a polished, textbook-quality chapter: clear \\section* "
    "headings, amsthm theorem/definition*/proof environments, connective "
    "prose between results.\n"
    "- Keep the ★ exam callouts as exambox environments.\n"
    "- Keep the existing preamble (documentclass, packages, and the theorem, "
    "definition*, and exambox definitions). Use ONLY those packages: "
    "geometry, amsmath, amssymb, amsthm, tikz (with the positioning library).\n"
    "- The document MUST compile with tectonic on the first try.\n"
    "- Output ONLY the complete .tex source, from \\documentclass to "
    "\\end{document}. No explanations, no commentary, no markdown fences."
)

FIX_INSTRUCTION = (
    "The text on stdin is a LaTeX document that FAILED to compile, preceded "
    "by the tectonic error log. Fix the LaTeX so it compiles cleanly, WITHOUT "
    "removing or shortening any mathematical content. Use only the packages "
    "geometry, amsmath, amssymb, amsthm, tikz (positioning). Output ONLY the "
    "corrected complete .tex source, from \\documentclass to \\end{document}. "
    "No explanations, no markdown fences."
)


def run_claude(instruction: str, stdin_text: str, model: str | None,
               timeout: int = 1200) -> str:
    """Invoke the Claude CLI in headless mode, feeding text on stdin."""
    cmd = ["claude", "-p", instruction]
    if model:
        cmd += ["--model", model]
    r = subprocess.run(cmd, input=stdin_text, capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {(r.stderr or r.stdout)[-600:]}")
    return r.stdout


def extract_tex(text: str) -> str:
    """Pull the complete document out of the model reply, tolerating fences
    or stray commentary around it."""
    text = strip_fences(text)
    i = text.find("\\documentclass")
    j = text.rfind("\\end{document}")
    if i != -1 and j != -1:
        return text[i:j + len("\\end{document}")]
    return text.strip()


def assemble_draft(path: str, title: str, date: str) -> str:
    """Build the full draft from a fuse_lecture .bodies.json (preferred — it
    holds the complete, un-truncated chunk bodies) or read an existing .tex."""
    if path.endswith(".bodies.json"):
        with open(path, encoding="utf-8") as f:
            bodies = json.load(f)
        ordered = [bodies[k] for k in sorted(bodies, key=int)]
        return (PREAMBLE.format(title=title, date=date)
                + "\n\n".join(ordered) + DOC_TAIL)
    with open(path, encoding="utf-8") as f:
        return f.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("draft",
                    help="fuse_lecture .bodies.json (preferred) or a .tex")
    ap.add_argument("-o", "--out", default="output/final",
                    help="output basename (writes <out>.tex and <out>.pdf)")
    ap.add_argument("--title", default="Lecture Notes")
    ap.add_argument("--date", default="")
    ap.add_argument("--model", default=None,
                    help="claude model alias (default: the CLI's default)")
    ap.add_argument("--max-fixes", type=int, default=2)
    args = ap.parse_args()

    draft = assemble_draft(args.draft, args.title, args.date)
    out_base = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_base) or ".", exist_ok=True)
    tex_path = out_base + ".tex"

    print(f"polishing {len(draft)} chars with claude (one pass) …",
          flush=True)
    tex = extract_tex(run_claude(FINISH_INSTRUCTION, draft, args.model))
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
        print("compile failed — asking claude to fix …", flush=True)
        err = (comp.stderr or comp.stdout)[-3000:]
        with open(tex_path, encoding="utf-8") as f:
            current = f.read()
        payload = f"COMPILER ERROR:\n{err}\n\nDOCUMENT:\n{current}"
        tex = extract_tex(run_claude(FIX_INSTRUCTION, payload, args.model))
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex)

    return 1


if __name__ == "__main__":
    sys.exit(main())

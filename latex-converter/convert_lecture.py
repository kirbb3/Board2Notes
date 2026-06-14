#!/usr/bin/env python3
"""Lecture snapshot → LaTeX converter (Phase 3, step 2).

Takes a folder of board snapshots (from board-extractor) and produces a
compiled, textbook-quality lecture PDF in three stages:

1. TRANSCRIBE — each snapshot is sent to a vision model, which transcribes
   the board into a LaTeX fragment (math + TikZ for diagrams).
2. MERGE — all fragments (text only) go to the model once more, which
   dedupes the overlapping board states and writes one coherent document.
3. COMPILE — tectonic builds the PDF; compile errors are sent back to the
   model to fix (up to --max-fixes rounds).

Backends (swappable with --backend):
  ollama  (default) — local model via the Ollama server, free and private.
                      Requires `ollama serve` running and a vision model
                      pulled, e.g. `ollama pull gemma3:12b`.
  claude            — the `claude` CLI in headless mode (runs on your
                      Claude subscription).

Usage:
    python3 convert_lecture.py <snapshot-folder> -o output/lecture \
        --backend ollama --model gemma3:12b
"""

import argparse
import base64
import glob
import json
import os
import re
import subprocess
import sys
import urllib.request

TS_RE = re.compile(r"_(\d+)h(\d+)m(\d+)s")

TRANSCRIBE_PROMPT = """This is a photo of a university chalkboard \
(discrete mathematics / calculus). Transcribe ALL board content into LaTeX.

Rules:
- Transcribe faithfully: definitions, theorems, proofs, derivations,
  examples. Keep the professor's notation. Use \\section*/\\textbf headers
  only where the board clearly has headers.
- Math goes in proper LaTeX math mode. Multi-line derivations use align*.
- Redraw any diagram (graph, tree, plot) as a tikzpicture environment.
- The board has several panels; transcribe them left to right.
- If a person blocks part of the board, transcribe what is visible and put
  the marker %OCCLUDED where content is hidden.
- If the board shows no meaningful content (e.g. people posing), reply with
  exactly: EMPTY
- Output ONLY the LaTeX fragment (no preamble, no \\begin{document}, no
  explanations, no markdown fences)."""

MERGE_PROMPT = """Below are LaTeX transcriptions of chalkboard snapshots \
from ONE lecture, in chronological order. Consecutive snapshots overlap \
heavily: the same panel appears repeatedly at different stages of being \
written, and erased panels get new content.

Write ONE complete, self-contained LaTeX document that reconstructs the
lecture:
- Each piece of content appears ONCE, at its fullest state, in the order it
  was first written. Use later duplicates to fill %OCCLUDED gaps in earlier
  ones.
- Keep ALL distinct mathematical content: every definition, theorem, proof,
  derivation, example, and diagram. Do not summarize or shorten the math.
- Use amsthm environments (theorem, definition, proof) where the content is
  structured that way. Keep tikzpicture diagrams (deduplicated).
- Title: {title}
- Use only standard packages: geometry, amsmath, amssymb, amsthm, tikz.
- The document must compile with pdflatex on the first try.
- Output ONLY the complete .tex source, starting with \\documentclass.
  No explanations, no markdown fences.

The transcriptions:

{fragments}"""

FIX_PROMPT = """This LaTeX document fails to compile. Fix the LaTeX errors \
without changing any content. Output ONLY the corrected complete .tex \
source, no explanations, no markdown fences.

Compiler error:
{error}

Document:
{tex}"""


# ---------------------------------------------------------------- backends

class OllamaBackend:
    def __init__(self, model: str, host: str, num_ctx: int):
        self.model = model
        self.host = host.rstrip("/")
        self.num_ctx = num_ctx

    def generate(self, prompt: str, image_path: str | None = None,
                 timeout: int = 1800) -> str:
        msg = {"role": "user", "content": prompt}
        if image_path:
            with open(image_path, "rb") as f:
                msg["images"] = [base64.b64encode(f.read()).decode()]
        body = json.dumps({
            "model": self.model,
            "messages": [msg],
            "stream": False,
            "options": {"num_ctx": self.num_ctx, "temperature": 0.1},
        }).encode()
        req = urllib.request.Request(
            f"{self.host}/api/chat", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return data["message"]["content"]


class ClaudeBackend:
    def __init__(self, model: str | None):
        self.model = model  # None → CLI default

    def generate(self, prompt: str, image_path: str | None = None,
                 timeout: int = 1800) -> str:
        if image_path:
            prompt = f"Read the image file {image_path}\n\n{prompt}"
        cmd = ["claude", "-p", prompt, "--allowedTools", "Read"]
        if self.model:
            cmd += ["--model", self.model]
        if image_path:
            cmd += ["--add-dir", os.path.dirname(image_path)]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {r.stderr[-500:]}")
        return r.stdout


# ----------------------------------------------------------------- helpers

def find_snapshots(folder: str) -> list[str]:
    # board-region.png is a debug image (the detected board with a red box
    # drawn on it), not a real snapshot — skip it.
    paths = [
        p for p in glob.glob(os.path.join(folder, "board-*.png"))
        if os.path.basename(p) != "board-region.png"
    ]

    def ts(p: str) -> int:
        m = TS_RE.search(os.path.basename(p))
        if not m:
            return 0
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))

    return sorted(paths, key=ts)


def strip_fences(text: str) -> str:
    """Remove markdown code fences if the model added them anyway."""
    text = text.strip()
    m = re.match(r"^```(?:latex|tex)?\s*\n(.*)\n```$", text, re.S)
    return m.group(1) if m else text


def compile_tex(tex_path: str) -> subprocess.CompletedProcess:
    return subprocess.run(["tectonic", tex_path],
                          capture_output=True, text=True, timeout=600)


# -------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshots", help="folder of board-*.png snapshots")
    ap.add_argument("-o", "--out", default="output/lecture",
                    help="output basename (writes <out>.tex and <out>.pdf)")
    ap.add_argument("--title", default="Lecture Notes")
    ap.add_argument("--backend", choices=["ollama", "claude"],
                    default="ollama")
    ap.add_argument("--model", default="qwen2.5vl:7b",
                    help="model name (ollama tag, or claude model alias). "
                         "qwen2.5vl:7b reads handwritten board math far "
                         "better than gemma3 — use a vision model here.")
    ap.add_argument("--ollama-host", default="http://localhost:11434")
    ap.add_argument("--num-ctx", type=int, default=32768,
                    help="ollama context window for the merge step")
    ap.add_argument("--max-fixes", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0,
                    help="only process the first N snapshots (for testing)")
    ap.add_argument("--transcribe-only", action="store_true",
                    help="stop after writing .fragments.json (skip the "
                         "boards-only merge/compile — the real pipeline "
                         "fuses the fragments next)")
    args = ap.parse_args()

    snap_dir = os.path.abspath(args.snapshots)
    snapshots = find_snapshots(snap_dir)
    if args.limit:
        snapshots = snapshots[: args.limit]
    if not snapshots:
        print(f"error: no board-*.png files in {snap_dir}", file=sys.stderr)
        return 1

    if args.backend == "ollama":
        backend = OllamaBackend(args.model, args.ollama_host, args.num_ctx)
    else:
        backend = ClaudeBackend(None if args.model == "qwen2.5vl:7b"
                                else args.model)

    out_base = os.path.abspath(args.out)
    out_dir = os.path.dirname(out_base) or "."
    os.makedirs(out_dir, exist_ok=True)
    tex_path = out_base + ".tex"
    frag_path = out_base + ".fragments.json"

    # Stage 1: transcribe each snapshot (resumable — fragments are cached).
    fragments: dict[str, str] = {}
    if os.path.exists(frag_path):
        with open(frag_path, encoding="utf-8") as f:
            fragments = json.load(f)
        print(f"resuming: {len(fragments)} fragment(s) already cached")

    for i, snap in enumerate(snapshots):
        name = os.path.basename(snap)
        if name in fragments:
            continue
        print(f"[{i + 1}/{len(snapshots)}] transcribing {name} …",
              flush=True)
        text = strip_fences(backend.generate(TRANSCRIBE_PROMPT, snap))
        fragments[name] = text
        with open(frag_path, "w", encoding="utf-8") as f:
            json.dump(fragments, f, indent=1)

    if args.transcribe_only:
        n = sum(1 for v in fragments.values() if v.strip() != "EMPTY")
        print(f"transcribe-only: wrote {frag_path} ({n} non-empty "
              f"fragment(s)). Skipping merge/compile.")
        return 0

    # Stage 2: merge into one document.
    ordered = [
        f"--- snapshot {os.path.basename(p)} ---\n"
        f"{fragments[os.path.basename(p)]}"
        for p in snapshots
        if fragments.get(os.path.basename(p), "EMPTY").strip() != "EMPTY"
    ]
    print(f"merging {len(ordered)} non-empty fragment(s) …", flush=True)
    tex = strip_fences(backend.generate(
        MERGE_PROMPT.format(title=args.title, fragments="\n\n".join(ordered))
    ))
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex)
    print(f"wrote {tex_path}")

    # Stage 3: compile, with fix rounds.
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

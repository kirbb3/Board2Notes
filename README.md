# Lecture-to-Notes Synthesizer

Turn a recorded university lecture (Panopto video + captions) into a
**textbook-quality LaTeX study guide PDF** — with the board math typed as
real LaTeX, proofs and definitions in theorem environments, native TikZ
diagrams, and ★ exam-priority callouts.

It runs almost entirely **free and local**: a vision model on your own GPU
reads the chalkboard, a local text model drafts the chapter, and Claude does
a single polishing pass at the end.



## Pipeline

```
 Panopto lecture                                          study guide PDF
       │                                                         ▲
       ▼                                                         │
 chrome-extension ─► board-extractor ─► convert_lecture ─► fuse_lecture ─► finish_lecture
   video + .srt        board PNGs        boards → LaTeX     draft chapter    polished PDF
                                          (qwen2.5vl)        (gemma3:12b)      (claude CLI)
                     transcript-engine ──────────────────────►
                       .srt → JSON (★ flags, timestamps)
```

| Stage | Tool | Engine | Cost |
|---|---|---|---|
| Grab video + transcript | `chrome-extension/` | — | free |
| Extract board snapshots | `board-extractor/` | OpenCV | free |
| Clean transcript + ★ flags | `transcript-engine/` | deterministic | free |
| Boards → LaTeX fragments | `latex-converter/convert_lecture.py` | `qwen2.5vl:7b` (Ollama) | free, local GPU |
| Fuse into rough draft | `latex-converter/fuse_lecture.py` | `gemma3:12b` (Ollama) | free, local GPU |
| Polish into final PDF | `latex-converter/finish_lecture.py` | `claude` CLI | one pass, subscription |

## Quickstart

Prereqs (on the machine with the GPU): [Ollama](https://ollama.com) with
`ollama pull qwen2.5vl:7b` and `ollama pull gemma3:12b`; `tectonic`; Python
3.10+ with OpenCV (`pip install opencv-python numpy`) for board extraction;
the [Claude CLI](https://code.claude.com/docs/en/setup) authenticated against
your subscription. (On Windows use `py -3.12` and run from the repo root.)

### One command

`run_pipeline.py` chains all five stages — video + captions in, finished PDF
out:

```bash
python3 run_pipeline.py --name mylecture --title "My Lecture" \
    --video lecture.mp4 --captions lecture.srt
# -> output/mylecture-final.pdf
```

If you already have the board snapshots, pass `--boards boards/mylecture`
instead of `--video` to skip extraction. Every stage is resumable, so a
re-run continues where it left off. Override models with `--board-model`,
`--fuse-model`, `--finish-model`.

### Stage by stage

To run (or re-run) the stages individually:

```bash
# 1. board snapshots from the lecture video
board-extractor/.venv/bin/python board-extractor/extract_boards.py lecture.mp4 -o boards/mylecture

# 2. transcript → structured JSON (no model)
python3 transcript-engine/process_transcript.py captions.srt -o output/mylecture --title "My Lecture"

# 3a. boards → LaTeX fragments  (free, local vision model)
python3 latex-converter/convert_lecture.py boards/mylecture -o output/mylecture --model qwen2.5vl:7b
# 3b. fuse transcript + fragments → rough draft  (free, local)
python3 latex-converter/fuse_lecture.py output/mylecture.json output/mylecture.fragments.json -o output/mylecture-fused --model gemma3:12b

# 4. polish into the final study guide  (one Claude pass)
python3 latex-converter/finish_lecture.py output/mylecture-fused.bodies.json -o output/mylecture-final --title "My Lecture"
```

Open `output/mylecture-final.pdf`. Every stage is resumable — re-running reuses
the cached `.fragments.json` / `.bodies.json`, so iterating costs nothing.

## Example output

`output/mit6042-mst-final.pdf` was generated end-to-end by this pipeline (and
the one-command wrapper) from **MIT OpenCourseWare 6.042J, Lecture 8 —
*Graph Theory II: Minimum Spanning Trees*** (CC BY-NC-SA), a lecture it had
never seen. From a dark-chalkboard video + captions it produced a clean
chapter: the walk/path distinction, the *n−1 edges* theorem with an induction
proof, spanning trees, the edge-swap lemma, and Kruskal's algorithm with a
full greedy-correctness proof — with native TikZ diagrams and exam callouts.
(Board reading used `--board dark`; see `board-extractor`.)

## Why this split (the cost trade-off)

You can't hand a 65-minute video to Claude — it can't watch video — so the
board snapshots must be extracted either way. The real choice is *who reads
the ~33 board images and writes the document*:

- **All-Claude:** send 33 board images + transcript to Claude. Most faithful,
  but ~3× the Claude usage per lecture and **re-spent on every re-run**.
- **This pipeline:** local vision model reads the boards (free, cached),
  local model drafts, Claude does one cheap text-only polish. ~⅓ the Claude
  usage on a single run, and the gap widens enormously across a semester
  because the expensive vision work is done once, locally, and reused.

Reading handwriting, a local 7B model is a little less accurate than Claude,
so the finisher's polish can drift toward standard textbook phrasing where a
board was hard to read. For a study guide that's a fair trade for keeping a
whole term nearly free and private.

See `CLAUDE.md` for design decisions, model rationale, and gotchas.

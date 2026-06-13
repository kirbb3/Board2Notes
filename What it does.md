# Project Spec: Lecture-to-Notes Synthesizer (MVP)

## Project Goal

Build a post-lecture processing pipeline that takes a recorded university STEM lecture (video + text transcript) and automatically generates a single, clean, textbook-quality study guide PDF with perfectly typed math, embedded graphs, and prioritized exam highlights.

## Core Architecture & Feature Flow

### Step 1: The Browser Grabber (Chrome Extension)

- **Goal:** Bypass Panopto streaming restrictions on Canvas.
    
- **Function:** A simple browser extension button. When clicked on a Canvas/Panopto lecture page, it sniffs the underlying network request, locates the raw video stream (`.mp4`), grabs the closed-caption transcript file, and downloads both to the user's local machine.
    

### Step 2: Global Canvas Stitching & Spatial Tracker

- **Goal:** Solve the "split-board" problem and eliminate obstructions.
    
- **Function:** Process the video _globally_ in hindsight. It maps the classroom wall as a single continuous canvas. It ignores the professor's body when they block the board. It reconstructs the writing chronologically from left to right, maintaining logical flow even if the professor jumps across multiple physical boards.
    

### Step 3: Multimodal Element Separator

- **Goal:** Extract math and visuals into their proper formats.
    
- **Function:** Crop and segment the board into two categories:
    
    - **Math/Text:** Translates handwritten derivations, matrices, and equations into clean digital LaTeX tokens.
        
    - **Graphs/Diagrams:** Identifies geometric shapes or coordinate plots, crops them, runs a high-contrast filter to remove background classroom glare (making lines dark and crisp on a pure white background), and saves them as local image assets.
        

### Step 4: Audio-Visual Context Fusion

- **Goal:** Turn raw equations into a readable story.
    
- **Function:** Syncs the transcript timestamps with the video timeline. It uses the professor's spoken words to draft brief, textbook-style explanatory prose between the math blocks, while stripping out all verbal filler ("um," "uh") and unrelated tangents. It uses audio timestamps to anchor graphs precisely next to the text explaining them.
    

### Step 5: The Verbal Flagging System ("The Star System")

- **Goal:** Highlight high-value exam materials automatically.
    
- **Function:** Scans the text transcript for spoken high-priority phrases (e.g., _"This is a classic midterm problem,"__"Make sure you know this for the test"_). When triggered, it wraps that specific section or problem in a highly visible **★ EXAM QUESTION** callout box in the final layout.
    

### Step 6: PDF Compiler

- **Goal:** Output a clean document.
    
- **Function:** Assembles everything into a single, cohesive PDF file organized by clear section headings (e.g., `Problem 1`, `Theorem 2`). Equations must be rendered in crisp typography, diagrams embedded inline, and formatting looking like a textbook chapter rather than a collection of video screenshots.
    

## Technical Instructions for Claude Code

- **Processing Paradigm:** Post-lecture, on-demand batch processing (Plan A). Prioritize layout accuracy and global context over real-time processing speed.
    
- **Input:** 1 Video File (`.mp4`) + 1 Transcript File (`.txt` / `.vtt`).
    
- **Output:** 1 Document File (`.pdf`).
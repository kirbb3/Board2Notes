[[What it does]]

## The Build Order for Claude Code

### Phase 1: The Chrome Extension (The Data Grabber)

Before you can process anything, you need data. Start by telling Claude Code to build _only_ the extension.

- **Prompt to Claude:** _"Let's build a simple Chrome Extension. When clicked on a page with a video player, it needs to look at the network logs, find the direct `.mp4` video stream URL, download the video, and extract the closed-caption transcript file."_
    

### Phase 2: Simple Audio-Text Fusion (The Base Pipeline)

Don't worry about computer vision or math yet. Get the text engine working first using just the transcript file.

- **Prompt to Claude:** _"Now, let's write a script that takes the downloaded transcript file, strips out verbal filler ('um', 'uh', tangents), scans for exam-related keywords to add star flags, and generates a basic structured text document."_
    

### Phase 3: The Math & Graph Extractor (The Vision Layer)

Once the text pipeline works, add the image processing to extract the board contents from the video file.

- **Prompt to Claude:** _"Let's build the video processing module. It needs to look at the video, wait for the presenter to step away from the board, and isolate text areas from drawn graphs. Turn the messy handwriting into clean text tokens, and clean up the graphs so they are high-contrast black and white images."_
    

### Phase 4: The Canvas Stitcher (The Spatial Fix)

Teach the vision layer how to handle a professor moving between multiple whiteboards.

- **Prompt to Claude:** _"Let's update the video module to handle split boards. If the professor writes on the left board and then the right board, stitch those frames together into a single master canvas so the reading order flows logically from left to right."_
    

### Phase 5: The Final PDF Assembler (The Compiler)

Tie everything together into the final textbook-quality output.

- **Prompt to Claude:** _"Finally, let's combine the processed text from Phase 2 with the math and graphs from Phase 3 and 4. Assemble them chronologically into a beautifully formatted single PDF document with section headings and styled callout boxes for the starred exam tips."_
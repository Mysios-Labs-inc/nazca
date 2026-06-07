# mediagen

Thin, Claude-driven CLI for AI **image** + **video** generation. Two commands,
each does one thing and prints the output path. Claude (or you) orchestrates.

## Install
```bash
pip install -e .
```

## Auth — one path for everything
**Vertex AI via gcloud. No API keys, no provider SDKs.**
```bash
gcloud auth login
```
Project `your-gcp-project`, region `us-central1` by default
(override: `VERTEX_PROJECT`, `VERTEX_LOCATION`).

## image  (Vertex Gemini image models / "nano-banana")
```bash
mediagen image -o out.png -p "a tomahawk steak, warm amber grade" --model nano-banana
mediagen image -o out.png -p "restyle: deep amber/ochre, side-back key" --ref dish.jpg   # image-to-image
mediagen image ... --dry-run     # show planned request, no API call
```
Models: `nano-banana` (default → gemini-2.5-flash-image), `nano-banana-pro`,
`imagen`, or any Vertex publisher model id. `--ref` = restyle the real photo
(image-to-image), not text-to-image.

## video  (Vertex Veo 3.1, ported from the proven make_clip.sh)
```bash
mediagen video -o clip.mp4 -s start.png -p "slow push-in, embers glow"
mediagen video -o clip.mp4 -s start.png --end end.png -p "the skewer lifts"   # keyframe
mediagen video ... --dry-run     # write request JSON, no credits
```
Start frame + optional end frame (keyframe interpolation). Silent by default —
add audio in post. Clips come back 9:16, 8s, 720p unless overridden.

## Design
- One auth path (gcloud + Vertex REST) for both commands. No keys, no SDKs.
- Not a framework. No brand logic, no overlay, no posting — those belong in a
  Skill (the "how") / MCP (posting) layer on top.

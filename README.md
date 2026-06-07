# mediagen

Thin, Claude-driven CLI for AI **image** + **video** generation. Two commands,
each does one thing and prints the output path. Claude (or you) orchestrates.

## Install
```bash
pip install -e ".[all]"          # or [fal] / [gemini]
```

## Auth
- `image`: `FAL_KEY` (fal) or `GEMINI_API_KEY` (Gemini)
- `video`: gcloud auth (Vertex). Project `your-gcp-project`, us-central1 (override via env).

## image
```bash
mediagen image -o out.png -p "a tomahawk steak, warm amber grade" --model nano-banana
mediagen image -o out.png -p "restyle: deep amber/ochre, side-back key" --ref dish.jpg   # image-to-image
mediagen image -o out.png -p "..." --provider gemini --model gemini-2.5-flash-image
mediagen image ... --dry-run     # show planned request, no API call
```
Models: `nano-banana` (default), `nano-banana-pro`, `seedream`, `flux`, `gemini-2.5-flash-image`.
`--ref` = restyle the real photo (image-to-image), not text-to-image.

## video  (Vertex Veo 3.1, ported from the proven make_clip.sh)
```bash
mediagen video -o clip.mp4 -s start.png -p "slow push-in, embers glow"
mediagen video -o clip.mp4 -s start.png --end end.png -p "the skewer lifts"   # keyframe
mediagen video ... --dry-run     # write request JSON, no credits
```
Start frame + optional end frame (keyframe interpolation). Silent by default —
add audio in post. Clips come back 9:16, 8s, 720p unless overridden.

## Notes
- fal edit endpoints/arg shapes vary by model — `--dry-run` shows the exact call.
- Not a framework. No brand logic, no posting. Those live in a Skill / MCP layer.

# visionplatform-models

This repository hosts GitHub Release assets for VisionPlatform's auto-labeling models.

## Expected download format

The application resolves mirrored assets from:

`https://github.com/wzhtsuki/visionplatform-models/releases/latest/download/<filename>`

## Current mirror summary

- YAML configs scanned: 179
- Remote URL references: 200
- Unique files to mirror: 172
- Filename collisions: none

## How to populate the mirror

1. Create or edit a GitHub Release in this repository.
2. Upload model files as Release assets using their original filenames.
3. Keep the latest release populated with the current model set.

## Notes

- The application currently tries this mirror first.
- If an asset is missing, it can fall back to the original upstream source.
- To force mirror-only downloads, set `model_mirror.fallback_to_original: false` in VisionPlatform.
- Most source files come from X-AnyLabeling releases, with a few from `dl.fbaipublicfiles.com`.

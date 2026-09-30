# Source provenance

Authoritative source: [miffn/OFS-custom](https://github.com/miffn/OFS-custom), pinned commit `d341a387649a3a9cdd5757ad9f0b1a3ca83af7d8` (main, 2026-08-15). The earlier implementation was initially copied from `D:\ES post\OFS-4.0.8`; on 2026-09-30 its scene files were compared against the pinned repository. Source model defaults and publication HUD now follow that repository. Original projects and source media are not modified.

- `vendor/ofs/{Mesh,SceneGraph,SceneShader,Shader,GltfLoader,Transform}.*`: repository `OFS-lib/Scenegraph` (GPL-3.0, original license in `COPYING`). Files match the pinned revision apart from trailing blank lines, the standalone Shader include path, removed UTF-8 BOM, and throwing shader errors.
- `vendor/ofs/OFS_EmbeddedSimulator3DMath.h`: extracted from `OFS-lib/UI`.
- `renderer/cylinder.h`, `renderer/motion.h`: model geometry and six-axis transformation mapping from `src/UI/OFS_EmbeddedSimulator3D.cpp`.
- `renderer/hud.h`: extracted `drawAxisHud` from that source with a standalone state container and Clamp/Format adapters. Retains actual-axis visibility, colored bars, numeric Pitch, Stroke geometry-derived percentage, badge, and projected anchor line. Source clean-preview preset keeps this HUD enabled.
- `vendor/imgui`: fetched from official `ocornut/imgui` commit `c191faf0ba478e9c58a69c63306986a21ebfb6e4`, the exact submodule revision pinned by OFS-custom. Unmodified core/OpenGL3 backend apart from trailing blank lines; MIT license in `LICENSE.txt`.
- `vendor/ofs/OFS_Util.h`: new standalone adapter for logging and UTF-8 file reads; `SceneShader.h` include path corrected; shader errors now throw instead of silently producing blank frames.
- `vendor/glad`: original generated GL 3.3 loader; license notices retained in source headers (WTFPL OR CC0-1.0, AND Apache-2.0).
- `vendor/ofs/cgltf.h`: original bundled cgltf; MIT license retained in header.
- `vendor/ofs/json.hpp`: original nlohmann JSON single header; license in `JSON-LICENSE.MIT`.
- `models/*.glb` except the character: user's existing OFS body presets, copied from `data/models/simulator/body`; `models/blonde-chibi-simulator-ofs.glb`: previously copied user model from WSL `ES post/character-reference`. These assets are now explicit alternatives only. Asset rights remain those of the source assets; no new distribution permission is asserted.

Default empty selectedModel uses the source white cylinder (radius .52, half-height .84, 96 segments), red/purple side markers, and translucent white anchor. Existing character GLB and other assets are explicit alternatives only.

New renderer code and source modifications are GPL-3.0. EGL replaces desktop/SDL callbacks. ImGui renders only the extracted HUD into the same transparent offscreen canvas. Camera, shader, transformations, colors and anchoring follow source; the standalone base font is 18 px with configurable HUD scaling, so desktop GUI pixel equivalence is not asserted. Blended pixels are unpremultiplied before writing straight-alpha RGBA for FFmpeg.

Pinned source Git blob identities are recorded in `OFS-SOURCE.json`; local verification copies are in the project's ignored `data/reference/ofs-custom` directory. Input and binary fingerprints are recorded in each output manifest.

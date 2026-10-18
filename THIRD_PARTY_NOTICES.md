# Third-party notices

Code Mortis is an independent project. It bundles the unmodified Open Code Review executable, version 1.13.1, from https://github.com/alibaba/open-code-review, licensed under Apache License 2.0. Copyright 2026 alibaba/open-code-review Contributors. Its license is reproduced in LICENSES/Apache-2.0.txt.

backend/code_mortis/providers.json incorporates provider metadata from that version's extensions/frontend/src/shared/providers.generated.ts. Code Mortis adds OpenCode and custom-endpoint presets. This metadata remains covered by the upstream Apache License 2.0.

Frontend UI primitives in frontend/src/components/ui are generated from shadcn/ui, licensed under MIT. Copyright (c) 2023 shadcn. The MIT terms in LICENSES/shadcn-MIT.txt apply to those files.

Other dependencies retain their own licenses, supplied in the installed Python and JavaScript packages. The application uses Inter by Rasmus Andersson, distributed by @fontsource-variable/inter under the SIL Open Font License 1.1. See LICENSES/Inter-OFL.txt.

The OpenAI and Claude marks in frontend/public/brands belong to OpenAI and Anthropic, respectively. They identify connection providers and do not imply affiliation or endorsement. The SVG assets come from [OpenAI's official logo pack](https://cdn.openai.com/brand/OpenAI-Logos-2025.zip) and [Anthropic's press kit](https://anthropic.com/press-kit); trademark rights remain with their owners.

Additional provider marks in frontend/public/brands come from [Lobe Icons](https://github.com/lobehub/lobe-icons) at revision c385b2b8d1f9e19aa86e628d4e23c91ee1111a47, licensed under MIT. Copyright (c) 2023 LobeHub. Its license is reproduced in LICENSES/lobe-icons-MIT.txt. Eden AI and LiteLLM marks come from their [official website](https://www.edenai.co/) and [documentation](https://docs.litellm.ai/), respectively. Each SVG records its source. Provider names and marks remain the property of their respective owners and identify supported connections without implying affiliation or endorsement.

The optional native runner installs the unmodified official @openai/codex 0.160.1 and @anthropic-ai/claude-code 2.1.292 packages from npm. Codex is Apache-2.0 licensed. Claude Code remains subject to Anthropic's applicable software and service terms; it is not relicensed under Code Mortis's MIT license. Each user authenticates their own account through the official client. Package license files remain in the image. See https://github.com/openai/codex and https://code.claude.com/docs/en/legal-and-compliance.

# Third-party notices

Code Mortis is an independent project. backend/code_mortis/integrations/providers.json incorporates provider metadata from Open Code Review 1.13.1's extensions/frontend/src/shared/providers.generated.ts at https://github.com/alibaba/open-code-review. Copyright 2026 alibaba/open-code-review Contributors. Code Mortis adds OpenCode and custom-endpoint presets. This metadata remains covered by the upstream Apache License 2.0, reproduced in LICENSES/Apache-2.0.txt. The OCR executable is no longer bundled.

The application image also bundles the unmodified Trivy 0.76.0 executable from https://github.com/aquasecurity/trivy, licensed under Apache License 2.0. Its upstream license remains in /usr/share/doc/trivy/LICENSE in the image; the license terms are also reproduced in LICENSES/Apache-2.0.txt. Downloaded vulnerability databases and checks retain their upstream data-source notices.

Frontend UI primitives in frontend/src/components/ui are generated from shadcn/ui, licensed under MIT. Copyright (c) 2023 shadcn. The MIT terms in LICENSES/shadcn-MIT.txt apply to those files.

Other dependencies retain their own licenses, supplied in the installed Python and JavaScript packages. The application uses Inter by Rasmus Andersson, distributed by @fontsource-variable/inter under the SIL Open Font License 1.1. See LICENSES/Inter-OFL.txt.

The OpenAI and Claude marks in frontend/public/brands belong to OpenAI and Anthropic, respectively. They identify connection providers and do not imply affiliation or endorsement. The SVG assets come from [OpenAI's official logo pack](https://cdn.openai.com/brand/OpenAI-Logos-2025.zip) and [Anthropic's press kit](https://anthropic.com/press-kit); trademark rights remain with their owners.

Additional provider marks in frontend/public/brands come from [Lobe Icons](https://github.com/lobehub/lobe-icons) at revision c385b2b8d1f9e19aa86e628d4e23c91ee1111a47, licensed under MIT. Copyright (c) 2023 LobeHub. Its license is reproduced in LICENSES/lobe-icons-MIT.txt. Eden AI and LiteLLM marks come from their [official website](https://www.edenai.co/) and [documentation](https://docs.litellm.ai/), respectively. Each SVG records its source. Provider names and marks remain the property of their respective owners and identify supported connections without implying affiliation or endorsement.

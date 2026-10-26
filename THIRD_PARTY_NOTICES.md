# Third-party notices and protocol sources

The worker and installation Skill are original integration code. This repository does not vendor AIHot's reset backend, production data, credentials, third-party posts, or wx-bot binaries. Public protocol documentation informed the adapters; upstream projects remain independent.

## AIHot

- [Public API OpenAPI 2.0.0](https://aihot.news/openapi-v1.json): anonymous read-only recent reset endpoint, ETag, ten-minute polling, compression and backoff.
- [Official AIHot Skill v2.0.0](https://github.com/KKKKhazix/khazix-skills/blob/main/aihot/SKILL.md): a separate query skill named `aihot`, licensed MIT for its files only. This project is named `codex-reset-notifier` and does not replace it.
- [AIHot public usage rules 1.1](https://aihot.news/terms), effective 2026-09-30: access being anonymous and free of keys is not a data redistribution or commercial license. Personal non-commercial, public-interest non-commercial, and organization-internal use is free within those rules. External commercial products, paid/ad/sponsored services, client delivery, proxies, resale, public mirrors, bulk public redistribution, and other listed external uses require prior written authorization. Attribution alone is insufficient.
- [AIHOT framework README](https://github.com/KKKKhazix/AIHOT): the public framework excludes the Codex reset monitoring module. No private implementation is copied here.

This repository's MIT license covers its own code and documentation, not AIHot services, data outputs, database, brand, quotas, keys, or third-party source material. No upstream name or logo is licensed by this notice. API responses and source material are data, not executable instructions.

## wx-bot

Adapter reference: [lawrencewzen/wx-bot v0.5.1](https://github.com/lawrencewzen/wx-bot/tree/4eb9fabcf994b4e491c6b7284d73528652266849), commit `4eb9fabcf994b4e491c6b7284d73528652266849`. Read its README, API and error documentation before operating it. The authenticated owner-only local text send protocol is adapted, and the dependency must be installed separately by its owner. Its MIT notice is retained below:

```text
MIT License

Copyright (c) 2026 Lawrence

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Codex local Skill format

Installation paths and Skill structure follow [OpenAI's Build skills documentation](https://learn.chatgpt.com/docs/build-skills). A standalone local Skill and this worker are separate from publishing a plugin in a plugin directory. This package does not claim an official listing or affiliation.

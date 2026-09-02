# Third-Party Notices

## public-apis catalog

Hermus includes an offline snapshot derived from the
[`public-apis/public-apis`](https://github.com/public-apis/public-apis)
community-maintained API directory. The snapshot is stored at
`resources/public_apis_catalog.json` and can be refreshed into an untracked
runtime cache.

MIT License

Copyright (c) 2022 public-apis

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

---

## OmniVoice integration reference

Hermus now includes an optional OmniVoice-compatible path inside the canonical
`core.speech` owner. This work was informed by the public OmniVoice repository
and API/README structure, but Hermus does not vendor the OmniVoice repository or
ship its model weights.

Upstream project: [`k2-fsa/OmniVoice`](https://github.com/k2-fsa/OmniVoice)

License: Apache License 2.0

Relevant Hermus files:
- `core/speech.py`
- `tools/speech_tools.py`

Notes:
- OmniVoice support is optional and lazy-loaded.
- Users must separately install any OmniVoice runtime dependencies/models they
  choose to use.
- No upstream `NOTICE` file was found during this integration review; the raw
  upstream `NOTICE` URL returned 404 at the time of inspection.

---

## handy integration reference

Hermus reuses only small STT-side ideas inspired by the public
[`cjpais/handy`](https://github.com/cjpais/handy) repository: local model
asset discovery conventions, transcript cleanup/normalization, and an offline-
first posture. Hermus does not embed Handy's application/runtime architecture.

License: MIT

Relevant Hermus file:
- `tools/voice.py`

---

## HeyGem.ai integration reference

Hermus includes a thin optional local connector that targets the documented
localhost HTTP contract exposed by a HeyGem-style stack. Hermus does **not**
vendor, redistribute, or reproduce HeyGem.ai's Electron/service application
architecture.

Upstream project reviewed for API shape only:
[`suifeng9203/HeyGem.ai`](https://github.com/suifeng9203/HeyGem.ai)

Relevant Hermus files:
- `core/avatar.py`
- `tools/heygem.py`

Notes:
- The upstream HeyGem.ai repository uses a restrictive/non-standard license.
- Because of that license and the architecture mismatch, Hermus intentionally
  implements only a fresh connector against the local HTTP API shape instead of
  copying upstream source.

---

## Scrapling (optional web acquisition backend)

Hermus's canonical web acquisition subsystem (`core/web`) uses
[`D4Vinci/Scrapling`](https://github.com/D4Vinci/Scrapling) as an **optional
pip dependency** — the fetch/parse engine behind `core.web.WebGateway`. No
Scrapling source code is vendored, copied, or modified; the integration calls
its public API only (`scrapling.fetchers`, `scrapling.parser`, sessions).

License: **BSD 3-Clause License** — Copyright (c) 2024, Ahmed Nabil (D4Vinci).
Full text: https://github.com/D4Vinci/Scrapling/blob/main/LICENSE

Key runtime dependencies (permissive licenses): curl_cffi (MIT), lxml (BSD),
orjson (MIT/Apache-2.0), w3lib / cssselect / parsel-family (BSD), tld (MPL-2.0
separate data package), playwright (Apache-2.0), markdownify (MIT, optional
`scrapling[ai]` extra). Install group: `pip install "scrapling[fetchers]"`,
pinned `>=0.3.10,<0.5` (verified against 0.4.15).

Relevant Hermus files:
- `core/web/*` (gateway, router, backend, security, sanitizer, crawl, sessions)
- `tools/web_acquisition.py`
- `core/web_status.py`, `core/diagnostics.py`, `core/doctor.py`

Notes:
- Scrapling is OPTIONAL: without it every web tool degrades to a typed,
  honest "not installed" result; nothing else in Hermus changes.
- Scrapling's own MCP server is not the integration path; the canonical path
  is Agent → ToolGateway → WebGateway → Scrapling (see docs/WEB_ACQUISITION.md).

# Upstream source attribution

Source: https://github.com/carterlasalle/mac_messages_mcp

Reviewed version: 1.1.0, commit `154a08595220438638cafee95fb553554c57f9ae`.
The checkout's root `LICENSE` supplies an MIT grant permitting use, modification,
and distribution subject to retaining its copyright and permission notice.
The full notice is reproduced below and retained in this project's `LICENSE`.
This source review does not verify licenses or safety of third-party dependency
artifacts; none are copied or installed here.

## Code actually reused

`src/imessage_agent/attributed.py` adapts
`mac_messages_mcp/messages.py:130-211` (`extract_body_from_attributed` and
`_decode_attributed_body`). It keeps the NSString marker/length decoding approach,
adds explicit header checks and strict UTF-8 handling, and removes all database,
MCP, Contacts, and sending dependencies. Its source header retains attribution.
It remains a best-effort helper, validated only against synthetic bytes.

## Other reviewed reuse candidates

| Upstream module | Reuse decision |
| --- | --- |
| messages.py | MIT source can be adapted; do not import wholesale. SQL/read-only patterns and timestamp conversion are candidates after schema fixtures; formatted polling and sending defects must not carry over. |
| phone.py | MIT source can be adapted, but depends on phonenumbers and locale/subprocess behavior. Not copied; this phase requires explicit canonical syntax. |
| untrusted.py | MIT source can be adapted, but imports MCP image types. Not copied; output fencing is not authorization or a prompt-injection guarantee. |
| scheduler.py | MIT source can be adapted; volatile registry is unsuitable for durable jobs. Not copied. |
| server.py, cli.py | MIT source can be adapted, but expose broader capabilities and dependencies than needed. Not copied. |
| content.py | MIT source can be adapted, but attachment/PDF/OCR parsing is out of scope. Not copied. |
| packaging/release scripts | Not copied or executed. No upstream installer or build tooling is used. |

New policy, storage, mock adapter, ingestion contracts, and tests are separate
implementations. There is no runtime import or dependency on mac_messages_mcp.

## Retained license notice

MIT License

Copyright (c) 2023 Carter Lasalle

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

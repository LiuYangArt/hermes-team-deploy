---
name: lark-lingo
description: Use when working with Feishu/Lark Lingo glossary entries through lark-cli raw APIs, especially for listing repos or classifications, checking existing terms, creating review drafts, batch-importing glossary content from structured JSON, or turning wiki/doc-derived terminology into Lingo entries. Trigger on requests mentioning lingo, 词典, 词条, glossary, baike, batch import, for Lingo endpoints without a dedicated CLI command. Do not trigger for unrelated raw APIs.
---

# Lark Lingo

## Overview

Use this skill to operate Feishu/Lark Lingo through `lark-cli api`, with a safe default of creating review drafts instead of direct exempt-review entities.

Lingo is not exposed as a dedicated `lark-cli lingo ...` command set, so most work goes through raw OpenAPI endpoints.

## Quick Start

1. Confirm auth and scopes before touching Lingo data.
2. Discover repo IDs and classification IDs.
3. List existing entities and dedupe by `main_key` or `outer_info`.
4. Default to `POST /open-apis/lingo/v1/drafts`.
5. For bulk import on Linux, use the bundled Python builder and `--data @file`.

## Preconditions

- Ensure `lark-cli` is installed. Use the configured bot identity by default (`--as bot`). If an endpoint requires user identity, stop and explain the missing capability; use only an explicitly authorized credential bound to the current requester through the existing project authorization mechanism. Never start a shared user login or reuse another member’s token.
- Check that the selected identity and app have `baike:entity`.
- If the source material comes from Lark docs or wiki, also ensure `search:docs:read` and use doc/wiki tooling first.
- Only use direct entity creation when the user explicitly wants exempt-review behavior and `baike:entity:exempt_review` is available.

## Discovery Workflow

### 1. List repos

```bash
lark-cli api GET /open-apis/lingo/v1/repos --as bot
```

Use this to find the actual repo ID. Do not assume a classification name is a repo.

### 2. List classifications

```bash
lark-cli api GET /open-apis/lingo/v1/classifications --as bot
```

Lingo entries belong to second-level classifications. In practice, import payloads should include both:
- `id`: second-level classification ID
- `father_id`: first-level classification ID

### 3. List existing entities

```bash
lark-cli api GET "/open-apis/lingo/v1/entities?repo_id=<repo_id>&page_size=100" --as bot
```

Follow pagination until complete before claiming a deduplication check is complete.

### 4. Dedupe before writing

Check for:
- Existing `main_keys[].key`
- Existing `aliases[].key`
- Existing `outer_info.provider` + `outer_info.outer_id`

When bulk-importing a prepared glossary, assign stable `outer_id` values so later updates can target the same external records.

## Writing Workflow

### Default: create drafts

Use:

```bash
lark-cli api POST /open-apis/lingo/v1/drafts?repo_id=<repo_id> --as bot --data ...
```

Drafts are reviewable and safer than direct entity writes.

### Direct write: only when explicitly requested

`POST /open-apis/lingo/v1/entities` should be treated as higher risk and requires the exempt-review scope path.

## Bulk Import Workflow (Hermes / Linux)

Resolve paths relative to this skill's directory returned by `skill_view`; do not use Codex or Windows paths. Requires Python 3.9+ (standard library only).

1. Read existing entries through all pages and deduplicate before any write.
2. Give each normalized entry an explicit stable `outer_id`; do not derive identity from list order.
3. Build payloads in a new empty directory:

```bash
python3 <skill-dir>/scripts/build_lingo_payloads.py --source <source.json> --output-dir <new-payload-dir> --classification-id <second-level-id> --father-id <first-level-id> --provider <provider>
```

4. On an authorized import request, submit each file with `lark-cli api POST '/open-apis/lingo/v1/drafts?repo_id=<repo_id>' --as bot --data @<payload-file>`. Read each result, save returned draft IDs and stop on errors. Before retrying an ambiguous response, reconcile existing entries/drafts; never blindly replay the batch.
5. Preparing JSON does not authorize submission. Installation and dry runs must not create drafts. Missing scopes or unsupported identity must be reported, not bypassed.

For professional definitions, also load `plain-language-writing`. Preserve terminology, source links and uncertainty.

## Normalized Source Format

If the user asks for "先整理成词条列表" or "先转成 Lingo 可导入格式", normalize the source into this shape first:

```json
{
  "source_document": {
    "title": "FD 关卡背景设定",
    "url": "https://..."
  },
  "entries": [
    {
      "main_key": "新英格兰殖民地",
      "aliases": ["Neo England", "NeoEngland"],
      "description": "项目内定义的短释义",
      "related_docs": [
        { "title": "FD 关卡背景设定", "url": "https://..." }
      ]
    }
  ]
}
```

Keep descriptions project-facing and concise. If the source borrows names from another IP, do not overemphasize the original work unless the user explicitly wants cross-reference context.

## References

- Read [`references/lingo-openapi.md`](./references/lingo-openapi.md) for endpoint summaries, scopes, and field mapping.
- Use JSON files with `--data @file`; never interpolate glossary text into shell commands.

## Common Mistakes

- Treating a first-level classification such as `世界观词库` as if it were a repo.
- Omitting the second-level classification ID from import payloads.
- Creating direct entities when drafts were intended.
- Skipping dedupe and producing duplicate terms.
- Bypassing identity restrictions or treating skill installation as permission to write glossary data.

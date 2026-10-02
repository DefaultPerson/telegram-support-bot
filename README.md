# HM Support Bot

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.22-green?logo=telegram&logoColor=white)

Telegram feedback bot for customer support. Messages from private chats are automatically routed to forum topics in a support group, and staff replies are forwarded back to the user.

## Features

- **Forum topics** — a dedicated topic is created for each user in the support group, on `/start` by default;
  with `BOT_TOPIC_ON_START=false` it waits for the user's first real message, so `/start`-only users and their
  block/unblock notices never reach the group
- **Two-way messaging** — user messages go to the topic, staff replies go back to the DM
- **Media groups** — album support (photos, videos, audio, documents)
- **Blocking** — `/ban` to block/unblock users
- **Silent mode** — `/silent` disables reply forwarding for a specific user
- **Newsletter** — `/newsletter` for mass messaging via aiogram-newsletter
- **Localization** — English and Russian with per-user language selection
- **Throttling** — spam protection with configurable cooldown
- **Policy engine** *(optional)* — declarative YAML rules to auto-reply, tag, close, suppress notifications, or skip topic creation
- **LLM drafts** *(optional)* — classify the first message and suggest a reply to the manager via inline buttons (any OpenAI-compatible provider)

## C4

```mermaid
graph TB
    A[Telegram Users] --> B[HM Support Bot]
    B --> C[Topic Manager]
    B --> D[User Manager]
    B --> E[Newsletter Service]
    C --> F[SQLite Database]
    D --> F
    E --> F
    F --> G[Redis Cache]
    B --> H[Telegram Bot API]
    H --> A

    subgraph AdminCommands
        I["/newsletter - Newsletter"]
        J["/ban - Block User"]
        K["/silent - Silent Mode"]
        L["/information - User Info"]
    end

    AdminCommands --> B
```

## Quick Start

1. deps: Linux, Docker, Python 3.11+, Redis
2. env: `cp .env.example .env` and fill in the variables
3. install: `pip install -r requirements.txt`
4. dev: `python -m app`
5. prod: `docker compose up -d`

## Policy & AI extensions

Both layers are **off by default** — leaving the env vars at their defaults
keeps the bot behaving exactly as without them.

### Policy engine

Declarative rules in a YAML file decide what happens to incoming messages and
lifecycle events, with no business logic in the code.

```bash
cp config/policy.example.yaml config/policy.yaml   # then edit
# in .env:
POLICY_ENABLED=true
POLICY_CONFIG_PATH=config/policy.yaml
```

Matchers: `event_type` (`user_message` | `user_started` | `user_stopped` |
`topic_created`), `keywords_any`, `regex`, `message_length`, `has_link`,
`first_message` (true only for the user's very first message; one dropped by
`suppress_topic_creation` or not delivered to the topic does not count), combined
with `all` / `any`. Actions: `auto_reply`, `set_tag`, `close_topic`, `escalate`,
`suppress_group_notify`, `suppress_topic_creation`. See
`config/policy.example.yaml` for a documented example.

On upgrade, users who wrote earlier are recognised by their stored transcript or
pending AI draft. One who only ever sent media without a caption and never got a
text reply is taken for new, so their next message counts as the first.

`auto_reply` options (defaults keep the old behaviour):

| Option | Default | Description |
|---|---|---|
| `once` | `defaults.auto_reply_once` (`false`) | Send the template to a user only once; remembered in PostgreSQL per user and template key |
| `suppress_draft` | `true` | Any auto-reply skips the LLM draft; `false` keeps the draft (for "message received" notices) and keeps the notice out of the draft's transcript |

Manager commands in a topic: `/template <key>`, `/tag [name]`, `/close`,
`/escalate`.

### LLM drafts

| Env var | Default | Description |
|---|---|---|
| `AI_PROVIDER` | `none` | `none` disables; `openai_compatible` enables |
| `AI_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible base URL |
| `AI_API_KEY` | _(empty)_ | API key; empty also disables |
| `AI_MODEL` | `openai/gpt-5.6-luna` | Model id |
| `AI_SYSTEM_PROMPT_PATH` | `config/system_prompt.txt` | System prompt file |
| `AI_TIMEOUT_S` | `8` | Timeout of one request (a single attempt) |
| `AI_MAX_RETRIES` | `2` | Retries after a 429, a 5xx or a timeout; the client waits out `Retry-After` in between |
| `AI_TOTAL_TIMEOUT_S` | `0` | Ceiling on one draft, retries included; `0` derives it as `AI_TIMEOUT_S × (retries + 1) + 120 × retries` |
| `AI_MAX_TOKENS` | `4096` | Cap on the drafted reply length (reasoning tokens included) |
| `AI_REASONING_EFFORT` | _(empty)_ | `minimal`, `low`, `medium` or `high`, sent as OpenRouter's `reasoning.effort` with drafts and classification; empty sends nothing |
| `AI_VISION` | `true` | Send attached images to the model (needs a multimodal `AI_MODEL`) |
| `AI_MAX_IMAGES` | `4` | Images attached per album |
| `AI_IMAGE_MAX_BYTES` | `5242880` | Per-image size ceiling; larger ones are skipped |

When enabled, the first message of a conversation is classified and a draft
reply is posted into the topic with **Send / Skip** buttons. Install the extra
dependency with `pip install -r requirements-ai.txt` (or build the image with
`--build-arg INSTALL_AI=1`).

### Draft log, categories and automatic replies

These options live in the `ai` section of the policy file (so they need
`POLICY_ENABLED=true`) and are all off by default.

| Option | Default | Description |
|---|---|---|
| `log_drafts` | `false` | Record every draft and its outcome in PostgreSQL: `sent` (Send button), `skipped` (Skip), `manager_replied` (the manager wrote to the user in the topic while the draft was pending), `superseded` (a newer draft for the same user), `auto_sent` (automatic reply) |
| `categories` | `[]` | Classify the user's first message with a separate short LLM request. Each item: `key` (up to 40 Latin letters, digits, `_` or `-`), `title`, `icon`, `needs_human`, `notify_admins`. The icon goes before the topic name, the category is shown in the draft header and stored in the log, and `notify_admins` messages every admin in `BOT_DEV_IDS` with a link to the topic. An unclear answer maps to `other` when that key exists |
| `auto_threshold` | `0.95` | `/ai_auto` warns before enabling a category whose share of sent drafts is lower |
| `auto_min_drafts` | `20` | ... or which has fewer reviewed drafts than this |

Admin commands in the support group (admins are `BOT_DEV_IDS`):

- `/ai_stats [days]` — per category, over all time or the last 1 to 3650 days:
  total drafts, sent, skipped, manager replied, automatic, and the share of sent
  drafts. The share counts reviewed
  drafts only: sent / (sent + skipped + manager replied).
- `/ai_auto` — every category with its automatic-reply mode and stats.
- `/ai_auto <key> on|off` — asks for confirmation with the category's stats
  (and a warning below the bar); the mode changes only when an admin presses
  **Confirm**. Categories with `needs_human: true` cannot be turned on.

With the mode on, a draft for a conversation of that category goes to the user
right away, as if the manager had pressed Send, and the topic gets a copy marked
as an automatic reply. Silent mode (`/silent`) keeps it a normal draft. Modes are
stored in PostgreSQL and are all off until an admin turns one on.

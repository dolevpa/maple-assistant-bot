# MapleStory Classic Assistant (Discord bot)

A Discord bot that answers questions about **MapleStory Classic** (Classic World / MSCW): classes & builds, grind maps,
EXP, mechanics, party quests, events, patch notes. Slash command `/ask question:<text>`, or @mention the bot. Also `/help`.

Answers come from the xAI API (OpenAI-compatible, `https://api.x.ai/v1`). Before every answer the bot retrieves
reference material and passes it to the model with a system prompt that requires inline citations and
"I'm not sure" instead of guessing. Sources are appended under each answer.

## Retrieval sources

| Priority | Source | How |
|---|---|---|
| **Primary** | [MeowDB MapleStory Classic guides](https://meowdb.com/msclassic/guides) (~34 guides) | Server-rendered HTML. Fetched by `guides.py`, parsed into heading sections, cached to `cache/guides/*.json`, refreshed every `GUIDES_REFRESH_HOURS` (default 6) in the background, BM25 search per question. Cited with guide URL + section anchor. |
| News | Nexon MapleStory news CMS (`g.nexonstatic.com/maplestory/cms/v1/news`, the JSON feed behind nexon.com/maplestory/news) | Classic World posts are tagged and preferred; used for patch/event/maintenance questions. |
| Fallback | [maplestorywiki.net](https://maplestorywiki.net) (MediaWiki API) | Original MapleStory — may differ from Classic; the prompt says so. |
| Fallback | [StrategyWiki](https://strategywiki.org) MapleStory pages | Only when guide match is weak. |

`maplestory.wiki.gg` returns 403 to non-browser clients, so it is not used.

MeowDB etiquette: honest `User-Agent`, 1.5 s delay between requests, one request at a time, disk cache so normal
questions never hit MeowDB, content used only as cited retrieval context (their robots.txt blocks AI *training*
crawlers and welcomes AI search/citation — this bot only does the latter). Keep it that way.

## Setup

```bash
cd /workspace/maplestory-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # run.sh does this automatically if .venv is missing
cp .env.example .env     # then edit .env (gitignored)
./run.sh start
```

### Configuration (process env, or `.env` in this directory; re-read at runtime)

| Variable | Purpose | Default |
|---|---|---|
| `DISCORD_BOT_TOKEN` | Discord bot token (required; normally already in the environment) | – |
| `XAI_API_KEY` | xAI API key. **Optional at startup**: without it `/ask` replies "the AI key isn't configured yet". Add it to `.env` any time — no restart needed. | – |
| `XAI_MODEL` | Model name | `grok-4.7` (current flagship per docs.x.ai/docs/models) |
| `XAI_BASE_URL` | API base URL | `https://api.x.ai/v1` |
| `ASK_COOLDOWN_SECONDS` | Per-user cooldown for `/ask` and mentions | `15` |
| `GUIDES_REFRESH_HOURS` | Max age of cached MeowDB guides | `6` |
| `DISCORD_GUILD_ID` | Optional: also sync commands instantly to one server | – |

Secrets are never written to files or logs by the bot.

## Run / stop

```bash
./run.sh start     # background, logs -> bot.log, pid -> bot.pid
./run.sh stop      # or ./stop.sh
./run.sh restart
./run.sh status
./run.sh logs      # tail -f bot.log
```

Successful startup in `bot.log`: `slash commands synced globally: /ask, /help`, then `LOGIN OK: logged in as ...`.

## Invite the bot

`https://discord.com/oauth2/authorize?client_id=<APPLICATION_CLIENT_ID>&scope=bot%20applications.commands&permissions=84992`

(permissions 84992 = View Channels, Send Messages, Embed Links, Read Message History). The application's client ID is shown
in the Discord Developer Portal → your app → General Information, and printed in `bot.log` at login (`id=...`).
No privileged intents are required: Discord delivers message content for @mentions and DMs without the Message Content intent.
Global slash commands can take a little while to appear in a server the first time.

## Files

- `bot.py` – Discord client, `/ask`, `/help`, @mention handling, cooldown, message splitting, sources block
- `retrieval.py` – combines guides + news + wiki fallbacks into numbered context
- `guides.py` – MeowDB fetch / parse / disk cache / refresh loop / BM25 search
- `llm.py` – xAI call and the system prompt (MapleStory Classic focus, cite sources, admit uncertainty)
- `config.py`, `textutil.py` – config loading, tokenization
- `run.sh`, `stop.sh`, `requirements.txt`, `.env.example`, `cache/` (guide cache, regenerable)

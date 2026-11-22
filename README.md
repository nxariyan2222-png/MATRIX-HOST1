# MATRIX PYTHON HOSTING

Telegram-controlled Python hosting approval system. Users send `.py` or `.zip`; the configured Admin approves each upload. Approved projects are written to a dedicated orphan branch in the configured GitHub repository, then a separate Render Web Service is created for that branch.

## Configured values

- Admin Telegram ID: `8959111543`
- Force-subscribe channel ID: `-1003636772569`
- Force-subscribe channel: `https://t.me/MATRIX_TRADER777`
- GitHub repository: `https://github.com/nxariyan2222-png/MATRIX-HOST12`
- Render plan: `free`

## Required Render environment variables

| Variable | What to enter |
|---|---|
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `ADMIN_ID` | `8959111543` (already configured in render.yaml) |
| `FORCE_SUBSCRIBE` | `true` |
| `FORCE_SUB_CHANNEL_ID` | `-1003636772569` |
| `FORCE_SUB_CHANNEL_LINK` | `https://t.me/MATRIX_TRADER777` |
| `GITHUB_TOKEN` | Fine-grained GitHub token with Contents read/write on MATRIX-HOST12 |
| `GITHUB_OWNER` | `nxariyan2222-png` |
| `GITHUB_REPO` | `MATRIX-HOST12` |
| `GITHUB_BRANCH` | `main` |
| `RENDER_API_KEY` | Render API key with service read/write access |
| `RENDER_OWNER_ID` | Render workspace/owner ID from Render settings |
| `RENDER_REGION` | `oregon` (or a supported region) |
| `RENDER_PLAN` | `free` |
| `DATABASE_URL` | Optional PostgreSQL connection string for persistent bot records |

Make the Telegram bot an **administrator** in `@MATRIX_TRADER777`; otherwise `getChatMember` may not reliably verify every user's membership.

## GitHub / Render prerequisites

1. Keep the controller repository private if possible.
2. Create a fine-grained GitHub token with **Contents: Read and write** for `MATRIX-HOST12`.
3. Connect the same GitHub account/repository to Render. Render's GitHub integration must be allowed to access `MATRIX-HOST12`.
4. Create a Render API key with permission to create, read, restart, suspend, resume and delete services.
5. Set `RENDER_OWNER_ID` to the workspace ID (not the Telegram Admin ID).
6. Deploy this controller as a Render Web Service from `MATRIX-HOST12` using this `render.yaml`.
7. Render automatically supplies `RENDER_EXTERNAL_URL`; the controller uses Telegram webhooks on Render so incoming Telegram updates can wake the sleeping free web service. You can optionally set `WEBHOOK_SECRET` to a long random value; if omitted, a random secret is generated at startup.

Each approved project is stored on its own `matrix-project-*` orphan branch so that the branch checked out by a user's Render service contains only that project's files, not the controller's main branch contents.

## Telegram commands

### Users
- `/start` — open the bot
- Send a `.py` or `.zip` document — submit hosting request
- `/mybots` — list and manage only your own bots
- `/status PROJECT_ID` — check your service
- `/setenv PROJECT_ID KEY VALUE` — set a secret environment variable (value is not echoed)
- `/upload` — upload instructions
- `/help` — command help

Users can Start, Stop, Restart, view logs and Delete their own project only. All actions are checked against the project owner ID in the database.

### Admin (`8959111543` only)
- `/admin` — Admin panel
- `/pending` — pending uploads with approve/reject buttons
- `/allbots` — manage all hosted projects
- `/stats` — usage counts
- `/ban USER_ID` and `/unban USER_ID`

The Admin Telegram ID is the sole authorization check; there is no password login.

## Important Render Free limitations

- Render Free web services can spin down after 15 minutes without inbound HTTP traffic and may take about a minute to wake up. The controller uses Telegram webhooks, so a new Telegram update can wake it, but the first response can be delayed. Therefore this package **cannot guarantee 24/7 continuous execution** for uploaded bots on Render Free.
- User-uploaded bot scripts that use long polling (outbound Telegram polling) do not themselves generate inbound HTTP traffic and can still be put to sleep. A webhook-based user bot can receive inbound updates, but this also does not guarantee continuous 24/7 operation.
- Render Free provides 750 instance hours per workspace per month. Multiple services share that pool; a continuously running service can consume most/all of it.
- Free web services have ephemeral filesystems and no persistent disks. For durable controller records, configure `DATABASE_URL` to a PostgreSQL database. Do not store important user uploads only on the controller's `/tmp` directory.
- Render Free does not offer Background Workers. This project creates per-project **Web Services** with a small health endpoint wrapper; this does not bypass Render's free sleep/usage limits.

## Security notes

- Every user upload requires Admin approval.
- ZIP extraction rejects traversal paths, symlinks, hidden `.env` files, `.git`, `.github`, and deployment config files; size and file-count limits are enforced.
- User code is never executed inside the controller service. Each approved upload gets its own Render service and its own isolated branch checkout.
- Uploaded Python is arbitrary executable code. Review it before approval. User code may still use network access and its own environment variables; do not add controller secrets to user services.
- User projects do not receive `BOT_TOKEN`, `GITHUB_TOKEN`, or `RENDER_API_KEY` from the controller.
- This is not a guarantee that arbitrary untrusted code is harmless. Admin approval and per-service isolation reduce risk but do not replace a proper sandbox/security review.

## Local test

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill required secrets, then export them in your shell or use your preferred env loader.
python main.py
```

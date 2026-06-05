# AIfred deploy

## Always-on service (systemd user unit)

```bash
mkdir -p ~/.config/systemd/user
cp /home/mi4u/AIfred/deploy/aifred.service ~/.config/systemd/user/aifred.service
systemctl --user daemon-reload
systemctl --user enable --now aifred.service

# start at boot WITHOUT login (lingering):
sudo loginctl enable-linger mi4u

# logs / status
systemctl --user status aifred.service
journalctl --user -u aifred.service -f
```

## Ports

- **9120** — FastAPI web API + UI backend. **This is the port to expose via Cloudflare tunnel.**
  Bound to 127.0.0.1; the tunnel terminates TLS and forwards to `http://127.0.0.1:9120`.
- 5173 — vite dev server (local dev only, not for prod; `bun run build` serves from FastAPI later).

### Cloudflare tunnel

```bash
cloudflared tunnel --url http://127.0.0.1:9120
# or a named tunnel mapping a hostname -> http://127.0.0.1:9120
```

Set `AIFRED_WEB_TOKEN` in `.env` before exposing publicly — it gates every `/api/*` call.

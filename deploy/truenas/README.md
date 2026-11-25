# Running NakaVid on TrueNAS

NakaVid runs as a TrueNAS custom app: one Compose file, four containers.

| Container | Image | Job |
|---|---|---|
| `postgres` | `postgres:16-alpine` | the database |
| `web` | `ghcr.io/waalabs/nakavid` | the CMS (gunicorn); applies migrations on start |
| `worker` | `ghcr.io/waalabs/nakavid` | the pipeline: probe, score, extract, transcode, combine |
| `caddy` | `caddy:2.10-alpine` | the LAN entry point, video range-serving, Immich relay |

The `Image` GitHub workflow builds `ghcr.io/waalabs/nakavid` on every push to
`main`. TrueNAS pulls it; nothing is built on the NAS.

## Network posture

`postgres`, `web` and `worker` sit only on an internal Docker network with no
route out — not to the internet, not to the LAN. `caddy` is the only container
that also joins an ordinary network: it publishes port 8080 to the LAN, and it
relays the worker's Immich requests to the single Immich URL set in the
compose file. So footage can only leave through code in the Caddyfile, not
through anything the app does.

A gate for footage tagged safe for external use would follow the same shape:
one more container on the outside network, serving only those items.

## One-time setup

1. **Datasets.** Create `nakavid` with two children under a pool, e.g.
   `tank/nakavid/media` (footage, clips, stills, combines) and
   `tank/nakavid/postgres`. Give `media` to the `apps` user (uid/gid 568) —
   the app containers run as 568. Leave `postgres` owned by root; the
   Postgres image sets its own ownership on first start. Snapshot both.
2. **Registry access.** The image is private. Create a GitHub personal access
   token (classic) with only `read:packages`, then add it under
   Apps > Configuration > Manage Container Images / Docker registries
   (registry `ghcr.io`, your GitHub username, the token).
3. **Install.** Apps > Discover Apps > ⋮ > Install via YAML. Paste
   `compose.yaml`, then replace every `CHANGE_ME` and the `/mnt/tank/...`
   paths:
   - `CHANGE_ME_DB_PASSWORD` — appears twice, must match.
   - `CHANGE_ME_LONG_RANDOM_STRING` — e.g. `openssl rand -base64 48`.
   - `CHANGE_ME_NAS_HOSTNAME` / `CHANGE_ME_NAS_IP` — how you reach the NAS in a
     browser; both the allowed hosts and the CSRF origins lines.
   - `CHANGE_ME_IMMICH_API_KEY` and `CHANGE_ME_IMMICH_URL`.
4. **First user** (fresh install only — skip when migrating). From the NAS
   shell:
   ```sh
   docker exec -it nakavid-web-1 python manage.py createsuperuser
   ```
5. Open `http://<nas>:8080/`.

## Moving the existing library over

Stored paths are written as `/nakavid/...` and resolved against
`NAKAVID_STORAGE_ROOT`, so files can move without rewriting rows.

1. Stop the old web and worker so nothing changes mid-copy.
2. Dump the old database: `pg_dump -Fc -h <host> -U nakavid nakavid > nakavid.dump`.
3. Copy the media: `rsync -a <old storage root>/ <nas>:/mnt/tank/nakavid/media/`,
   then `chown -R 568:568` it on the NAS.
4. Install the app, then stop `web` and `worker`, and restore into the new
   database:
   ```sh
   docker exec -i nakavid-postgres-1 pg_restore --clean --if-exists --no-owner \
     -U nakavid -d nakavid < nakavid.dump
   ```
5. Start the app again. `web` applies any migrations newer than the dump.

## Updating

After a merge to `main`, wait for the `Image` workflow to finish, then pull
the new image and restart the app (Apps > nakavid > Update if offered,
otherwise `docker compose -p nakavid pull && docker compose -p nakavid up -d`
from the NAS shell). Migrations run when `web` starts.

## Notes

- No GPU: ffmpeg encodes on the CPU, as the dev server does today.
- Logs: Apps > nakavid > Logs, or `docker logs nakavid-worker-1`.
- Tuning gunicorn: set `GUNICORN_WORKERS`, `GUNICORN_THREADS` or
  `GUNICORN_TIMEOUT` in the `web` environment.

# Deploying on Oracle Cloud (Always Free)

One small Arm VM runs everything in containers (`compose.yaml` at the repo root): PostgreSQL,
the API, and the scheduled jobs. The data stays on the host, outside the containers: the
database on an attached block volume (`/data`), the raw store in the repo's `data/raw`. The
image is built on the server from the checked-out code, which also holds the compose file
and `.env`.

| Service | When | What |
|---|---|---|
| `postgres` | always | PostgreSQL 16 (`postgres:16`), data in `FI_PG_DATA` on the host, reachable only on the compose network |
| `api` | always | API on `127.0.0.1:8000` |
| `cron` | Mon–Fri 23:30 UTC | `fin-intel sync-daily`: market bars, splits and dividends, breadth, SEC fundamentals (bulk), insiders, congress, FRED, EIA, Korean and Japanese filings, Taiwanese prices, company metrics, watchlist prices |
| | Sun 06:00 UTC | `fin-intel sync-weekly`: SEC tickers, Massive reference, 13F, CFTC, ADR share counts, SIC codes, corporate events, missing SEC filings, filing text, Taiwanese and European statements, cross-listings, release calendar, contracts, input-output tables, short interest, N-PORT, Congress, lobbying, bills, presidential documents, outcomes, World Bank commodities, `prune-raw` |
| | hourly | `fin-intel sync-news --stalest 1`: the GDELT query refreshed longest ago (GDELT throttles bursts) |
| | daily 04:00 UTC | `deploy/backup.sh`: irreplaceable tables (raw index, portfolio) and the raw files present at dump time to an rclone remote, verified |

The cron service runs them with [supercronic](https://github.com/aptible/supercronic) (cron
for containers: logs to `docker compose logs cron`, never starts a job that is still
running), from a crontab `deploy/cron.sh` writes at start. A run missed while the server was
down isn't replayed; the next one picks up from the last success.

**Configuration.** As in TrueNAS's app catalog, the image holds only the code, pinned
(Python, packages, supercronic, uv); everything that differs between machines is a setting
in `.env`, with defaults in `compose.yaml`: host paths and owners, the API address, the
schedule (`FI_SCHEDULE_*`, empty turns a job off), memory limits per service, PostgreSQL's
memory settings and its image (pinned by digest). `.env.example` documents every one. The
app containers run read-only, as `finintel`'s uid, with every capability dropped and
`no-new-privileges`; each service has a healthcheck (`docker compose ps`).

## 2. Create the VM

Compute → Instances → Create instance:

- **Image:** Canonical Ubuntu 24.04 (aarch64)
- **Shape:** `VM.Standard.A1.Flex`, 2 OCPUs, 12 GB memory (the Always Free maximum)
- **Boot volume:** 100 GB (the free allowance is 200 GB in total)
- **SSH key:** paste your public key (`~/.ssh/id_ed25519.pub`)

No ports need opening: the API is reached through Tailscale or a Cloudflare Tunnel (step 6), never directly.

## 3. Install

```sh
ssh ubuntu@<vm-public-ip>
curl -fsSL https://raw.githubusercontent.com/deOliveira-R/financial_intelligence/main/deploy/install.sh | sudo bash
```

This installs git and Docker (Ubuntu's `docker.io` and `docker-compose-v2`), creates the
`finintel` user, and clones the repo to `/home/finintel/financial_intelligence`. Once `.env`
exists (next steps), re-running it builds the image, applies migrations and (re)starts the
containers; that's also how updates are deployed.

## 4. Configure

Copy your local `.env` up, then add the server-only settings:

```sh
# from your machine
scp .env ubuntu@<vm-public-ip>:/tmp/fin-intel.env
# on the VM
sudo install -o finintel -g finintel -m 600 /tmp/fin-intel.env /home/finintel/financial_intelligence/.env && rm /tmp/fin-intel.env
sudo -u finintel nano /home/finintel/financial_intelligence/.env
```

Add (and remove any `FI_DATABASE_URL` and `FI_RAW_DIR`: compose sets them):

```sh
FI_API_KEY=<long random string: openssl rand -hex 32>
FI_WATCHLIST=AAPL,MSFT,NVDA,SPY          # Tiingo history + SEC fundamentals
FI_FRED_SERIES=GDP,CPIAUCSL,DGS10,FEDFUNDS,UNRATE
FI_MARKET_OTC=true
FI_BACKUP_REMOTE=r2:fin-intel-backups   # see step 7

# Containers (compose.yaml); the rest of the settings and their defaults: .env.example
FI_PG_PASSWORD=<openssl rand -hex 24>   # the app's database password
FI_PG_DATA=/data/postgres               # database directory on the host
PG_UID=999                              # its owner (999: the image's postgres user)
PG_GID=999
APP_UID=1002                            # finintel's ids (id finintel): owner of data/raw
APP_GID=1002
RCLONE_CONFIG_DIR=/home/finintel/.config/rclone
```

A changed setting applies with `sudo docker compose up -d` (from the repo directory); a
code change needs `install.sh`.

## 5. PostgreSQL on a block volume

The database is tens of GB, so it lives on a block volume (the free tier includes 200 GB of
block storage; the boot volume takes 100). Create one in the OCI console (Block Storage >
Block Volumes, same availability domain), attach it to the instance (paravirtualized), then:

```sh
sudo mkfs.ext4 -L fidata /dev/sdb && sudo mkdir -p /data
echo "UUID=$(sudo blkid -s UUID -o value /dev/sdb) /data ext4 defaults,noatime,_netdev,nofail 0 2" | sudo tee -a /etc/fstab
sudo mount /data
sudo install -d -o 999 -g 999 -m 700 /data/postgres
```

The `postgres` container creates the database on first start (user `finintel`, database
`fin_intel`, password `FI_PG_PASSWORD`). Its memory settings come from `.env` (`PG_*`, defaults for the
12 GB VM); the rest is in `deploy/postgres/postgresql.conf` and `pg_hba.conf` (password
logins from the compose network only). Start everything, then load the data by restoring a backup (step 7) or by
syncing from scratch:

```sh
sudo bash /home/finintel/financial_intelligence/deploy/install.sh
cd /home/finintel/financial_intelligence && sudo docker compose ps
curl -s localhost:8000/health
```

## 6. Access the API

**Private (recommended): [Tailscale](https://tailscale.com).** It's free for personal use and needs no domain. Your devices reach the VM over a private network, which also keeps you within the personal-use terms of Tiingo's and Massive's free plans.

```sh
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
sudo tailscale serve --bg 8000      # https://<vm-name>.<tailnet>.ts.net -> the API
```

**Public: [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).** This needs a domain on Cloudflare (free plan). Create a tunnel to `http://localhost:8000` and put Cloudflare Access in front of it, or rely on `FI_API_KEY`. Only serve SEC and FRED data publicly: the free tiers of the commercial providers don't allow redistribution.

Every endpoint except `/health` requires `X-API-Key` when `FI_API_KEY` is set:

```sh
curl -H "X-API-Key: $KEY" https://<host>/securities/AAPL
```

### Agents (MCP)

`fin-intel mcp` serves the research tools (dossiers, timeline, supply chain, filing search
and text, time series, data catalog) over MCP on stdio, read-only. Run it in the API
container through SSH, so nothing new listens on the network. In Claude Code on the laptop:

```sh
claude mcp add fin-intel -- ssh fin-intel \
  'sudo docker exec -i fin-intel-api-1 fin-intel mcp'
```

## 7. Backups

Any [rclone](https://rclone.org) remote works. [Cloudflare R2](https://developers.cloudflare.com/r2/) has 10 GB free; OCI Object Storage has 20 GB free in an Always Free tenancy (S3-compatible API). The `cron` container reads the remote's configuration from `RCLONE_CONFIG_DIR`.

```sh
sudo apt-get install -y rclone && sudo -u finintel rclone config   # e.g. "oci" (type s3, provider Other)
cd /home/finintel/financial_intelligence && sudo docker compose exec cron /app/deploy/backup.sh
```

Nearly every table can be rebuilt from the raw provider responses. Once all fundamentals are loaded the database is tens of GB, so `backup.sh` doesn't snapshot it every night. Instead, nightly it uploads:
- `essential/`: the tables that can't be rebuilt (a data-only `pg_dump`). That's the raw response index (which body file is which response), accounts, portfolio transactions and broker snapshots. A few hundred KB, kept 30 days.
- `raw/`: the response bodies. They're content-addressed, so only new files upload.

Set `FI_BACKUP_DB_SNAPSHOT=1` to also upload a full `pg_dump` (faster restore, much larger).

Every upload is verified by checksum.

**Restore** (a few hours, mostly re-loading fundamentals), with the containers running:

```sh
cd /home/finintel/financial_intelligence
sudo -u finintel rclone copy oci:fin-intel-backups/raw data/raw
sudo -u finintel rclone copy oci:fin-intel-backups/essential data/raw/.restore
sudo docker compose exec cron bash -c 'fin-intel migrate &&
  pg_restore --dbname fin_intel --data-only $(ls data/raw/.restore/*.dump | tail -1) &&
  fin-intel rebuild all' && sudo rm -r data/raw/.restore
```

## Operations

```sh
cd /home/finintel/financial_intelligence
sudo docker compose ps                                   # services and health
sudo docker compose logs cron --since 24h                # last syncs' output
sudo docker compose exec cron fin-intel sync-daily       # sync now
sudo docker compose exec cron fin-intel sync-prices TSLA # any CLI command
sudo docker compose exec postgres psql -U finintel fin_intel
curl -s -H "X-API-Key: $KEY" localhost:8000/status       # recent runs, failing items
sudo bash deploy/install.sh                              # deploy updates (pull, build, migrate, restart)
```

Long jobs run detached in their own container, then are followed and waited on:

```sh
sudo docker compose run -d --name fi-job cron fin-intel rebuild fundamentals
sudo docker logs -f fi-job; sudo docker wait fi-job; sudo docker rm fi-job
```

`install.sh` applies pending migrations before restarting the API, so the schema and code stay in step.

## Moving from the host install (2026-10)

Until 2026-10 the server ran PostgreSQL from Ubuntu's package (cluster in
`/data/postgresql/16/main`, owned by the host's `postgres` user) and the app from a uv
virtualenv under systemd timers. The containers reuse that data directory as it is: same
major version, and the database's `C.UTF-8` collation sorts by code point under any glibc,
so its indexes stay valid. One time, with no sync running:

```sh
cd /home/finintel/financial_intelligence
# .env: add the container settings of step 4 with FI_PG_DATA=/data/postgresql/16/main and
# PG_UID/PG_GID = the host postgres user's ids (id postgres); remove FI_DATABASE_URL, FI_RAW_DIR
sudo -u postgres psql -c "ALTER ROLE finintel PASSWORD '<FI_PG_PASSWORD>'"
sudo systemctl disable --now fin-intel-api fin-intel-daily.timer fin-intel-weekly.timer \
  fin-intel-backup.timer postgresql
sudo bash deploy/install.sh
```

Rollback: `sudo docker compose down`, then `sudo systemctl enable --now postgresql
fin-intel-api` and the three timers (the units and the virtualenv are left in place).

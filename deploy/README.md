# Deploying on Oracle Cloud (Always Free)

One small Arm VM runs everything: the API as a service, the scheduled syncs as systemd timers, PostgreSQL, and nightly backups to object storage. The database lives on an attached block volume (`/data`); the raw store on the VM's disk.

| Unit | When | What |
|---|---|---|
| `fin-intel-api.service` | always | API on `127.0.0.1:8000` |
| `fin-intel-daily.timer` | Mon–Fri 23:30 UTC | `fin-intel sync-daily`: market bars, splits and dividends, breadth, SEC fundamentals (bulk), insiders, congress, FRED, EIA, Korean and Japanese filings, Taiwanese prices, company metrics, watchlist prices |
| `fin-intel-weekly.timer` | Sun 06:00 UTC | `fin-intel sync-weekly`: SEC tickers, Massive reference, 13F, CFTC, ADR share counts, SIC codes, corporate events, missing SEC filings, filing text, Taiwanese and European statements, cross-listings, release calendar, contracts, input-output tables, short interest, N-PORT, Congress, lobbying, bills, presidential documents, outcomes, `prune-raw` |
| `fin-intel-backup.timer` | daily 04:00 UTC | `deploy/backup.sh`: irreplaceable tables (raw index, portfolio) and the raw files present at dump time to an rclone remote, verified |

## 1. Oracle account

1. Sign up at [oracle.com/cloud/free](https://www.oracle.com/cloud/free/). A card is required for verification; Always Free resources are not charged.
2. **Choose the home region carefully.** It can't be changed, and Always Free disk exists only there. Arm capacity is sometimes scarce in popular regions. If VM creation reports "out of capacity", retry later or pick a quieter region at signup.
3. Recommended: upgrade to **Pay As You Go** (Billing → Upgrade). Always Free resources stay free, and this is widely reported to stop Oracle reclaiming "idle" free VMs (CPU, network and memory all under 20% for 7 days, which a light workload like this can hit). Oracle's documentation doesn't promise it, so keep the backups running regardless. Add a budget alert at $1 so any accidental paid resource is noticed.

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

This installs git, rclone and PostgreSQL, creates the `finintel` user and its database (`fin_intel`, local socket, peer authentication), installs uv (which brings its own Python 3.14), clones the repo to `/home/finintel/financial_intelligence`, syncs dependencies, and installs the systemd units. Re-run it to deploy updates.

## 4. Configure

Copy your local `.env` up, then add the server-only settings:

```sh
# from your machine
scp .env ubuntu@<vm-public-ip>:/tmp/fin-intel.env
# on the VM
sudo install -o finintel -g finintel -m 600 /tmp/fin-intel.env /home/finintel/financial_intelligence/.env && rm /tmp/fin-intel.env
sudo -u finintel nano /home/finintel/financial_intelligence/.env
```

Add:

```sh
FI_API_KEY=<long random string: openssl rand -hex 32>
FI_WATCHLIST=AAPL,MSFT,NVDA,SPY          # Tiingo history + SEC fundamentals
FI_FRED_SERIES=GDP,CPIAUCSL,DGS10,FEDFUNDS,UNRATE
FI_MARKET_OTC=true
FI_BACKUP_REMOTE=r2:fin-intel-backups   # see step 7
```

## 5. PostgreSQL on a block volume

The database is tens of GB, so it lives on a block volume (the free tier includes 200 GB of
block storage; the boot volume takes 100). Create one in the OCI console (Block Storage >
Block Volumes, same availability domain), attach it to the instance (paravirtualized), then:

```sh
sudo mkfs.ext4 -L fidata /dev/sdb && sudo mkdir -p /data
echo "UUID=$(sudo blkid -s UUID -o value /dev/sdb) /data ext4 defaults,noatime,_netdev,nofail 0 2" | sudo tee -a /etc/fstab
sudo mount /data
sudo systemctl stop postgresql
sudo rsync -a /var/lib/postgresql/16 /data/postgresql/ && sudo chown -R postgres:postgres /data/postgresql
sudo sed -i "s|^data_directory = .*|data_directory = '/data/postgresql/16/main'|" /etc/postgresql/16/main/postgresql.conf
printf '%s\n' 'shared_buffers = 2GB' 'effective_cache_size = 7GB' 'work_mem = 64MB' \
  'maintenance_work_mem = 1GB' 'max_wal_size = 8GB' 'checkpoint_timeout = 15min' \
  'random_page_cost = 1.1' 'effective_io_concurrency = 100' |
  sudo tee /etc/postgresql/16/main/conf.d/fin-intel.conf
sudo systemctl start postgresql
```

The app connects with the default `FI_DATABASE_URL=postgresql+psycopg:///fin_intel`.
Load the data by restoring a backup (below) or by syncing from scratch.

Then start everything:

```sh
sudo systemctl enable --now fin-intel-api fin-intel-daily.timer fin-intel-weekly.timer fin-intel-backup.timer
systemctl list-timers 'fin-intel*'
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
and text, time series, data catalog) over MCP on stdio, read-only. Run it on the server
through SSH, so nothing new listens on the network. In Claude Code on the laptop:

```sh
claude mcp add fin-intel -- ssh fin-intel \
  'sudo -u finintel bash -c "cd /home/finintel/financial_intelligence && exec .venv/bin/fin-intel mcp"'
```

## 7. Backups

Any [rclone](https://rclone.org) remote works. [Cloudflare R2](https://developers.cloudflare.com/r2/) has 10 GB free; OCI Object Storage has 20 GB free in an Always Free tenancy (S3-compatible API).

```sh
sudo -u finintel rclone config      # create a remote, e.g. "oci" (type s3, provider Other)
sudo systemctl start fin-intel-backup && journalctl -u fin-intel-backup -n 20
```

Nearly every table can be rebuilt from the raw provider responses. Once all fundamentals are loaded the database is tens of GB, so `backup.sh` doesn't snapshot it every night. Instead, nightly it uploads:
- `essential/`: the tables that can't be rebuilt (a data-only `pg_dump`). That's the raw response index (which body file is which response), accounts, portfolio transactions and broker snapshots. A few hundred KB, kept 30 days.
- `raw/`: the response bodies. They're content-addressed, so only new files upload.

Set `FI_BACKUP_DB_SNAPSHOT=1` to also upload a full `pg_dump` (faster restore, much larger).

Every upload is verified by checksum.

**Restore** (a few hours, mostly re-loading fundamentals):

```sh
sudo -u finintel bash -c 'cd ~/financial_intelligence &&
  rclone copy oci:fin-intel-backups/raw data/raw &&
  rclone copy oci:fin-intel-backups/essential /tmp/essential &&
  .venv/bin/fin-intel migrate &&
  pg_restore --dbname fin_intel --data-only $(ls /tmp/essential/*.dump | tail -1) &&
  .venv/bin/fin-intel rebuild all'
```

## Operations

```sh
journalctl -u fin-intel-daily -n 50          # last sync's output
sudo systemctl start fin-intel-daily         # sync now
sudo -u finintel /home/finintel/financial_intelligence/.venv/bin/fin-intel sync-prices TSLA
curl -s -H "X-API-Key: $KEY" localhost:8000/status    # recent runs, failing items
curl -fsSL https://raw.githubusercontent.com/deOliveira-R/financial_intelligence/main/deploy/install.sh | sudo bash   # deploy updates
```

`install.sh` applies pending migrations before restarting the API, so the schema and code stay in step.

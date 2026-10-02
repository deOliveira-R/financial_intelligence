# Deploying on Oracle Cloud (Always Free)

One small Arm VM runs everything: the API as a service, the scheduled syncs as systemd timers, and nightly backups to object storage. SQLite and the raw store stay on the VM's disk, as they do locally.

| Unit | When | What |
|---|---|---|
| `fin-intel-api.service` | always | API on `127.0.0.1:8000` |
| `fin-intel-daily.timer` | Mon–Fri 23:30 UTC | `fin-intel sync-daily`: Massive market bars, recent splits and dividends, FRED series, watchlist prices |
| `fin-intel-weekly.timer` | Sun 06:00 UTC | `fin-intel sync-weekly`: SEC tickers, Massive reference, watchlist fundamentals, `prune-raw` |
| `fin-intel-backup.timer` | daily 04:00 UTC | `deploy/backup.sh`: database snapshot and new raw files to an rclone remote |

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

This installs git, sqlite3 and rclone, creates the `finintel` user, installs uv (which brings its own Python 3.14), clones the repo to `/home/finintel/financial_intelligence`, syncs dependencies, and installs the systemd units. Re-run it to deploy updates.

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

## 5. Copy the data instead of re-fetching it

The local database and raw store took hours of rate-limited syncing (and Tiingo's monthly symbol quota), so copy them:

```sh
# on your machine: fold the WAL into the main file first
sqlite3 data/fin_intel.db "PRAGMA wal_checkpoint(TRUNCATE);"
rsync -avz --progress data/ ubuntu@<vm-public-ip>:/tmp/fin-intel-data/
# on the VM
sudo rsync -a /tmp/fin-intel-data/ /home/finintel/financial_intelligence/data/
sudo chown -R finintel:finintel /home/finintel/financial_intelligence/data && sudo rm -rf /tmp/fin-intel-data
```

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

## 7. Backups

Any [rclone](https://rclone.org) remote works. [Cloudflare R2](https://developers.cloudflare.com/r2/) has 10 GB free; OCI Object Storage has 20 GB free in an Always Free tenancy (S3-compatible API).

```sh
sudo -u finintel rclone config      # create a remote, e.g. "r2" (type s3, provider Cloudflare)
sudo systemctl start fin-intel-backup && journalctl -u fin-intel-backup -n 20
```

Each night `backup.sh` uploads a consistent database snapshot (gzip, kept 8 days) and any new raw files. Raw files are content-addressed, so only new ones upload. To restore, download a snapshot to `data/fin_intel.db` and the raw files to `data/raw/`. `fin-intel rebuild all` reconstructs every table from raw alone if needed.

## Operations

```sh
journalctl -u fin-intel-daily -n 50          # last sync's output
sudo systemctl start fin-intel-daily         # sync now
sudo -u finintel /home/finintel/financial_intelligence/.venv/bin/fin-intel sync-prices TSLA
curl -s -H "X-API-Key: $KEY" localhost:8000/status    # recent runs, failing items
curl -fsSL https://raw.githubusercontent.com/deOliveira-R/financial_intelligence/main/deploy/install.sh | sudo bash   # deploy updates
```

`install.sh` applies pending migrations before restarting the API, so the schema and code stay in step.

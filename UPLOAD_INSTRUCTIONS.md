# Bulk Upload Recon Documents

## Quick Start

**Option A: Run the script yourself (easiest)**

```powershell
# Set your write token (from Fly secrets / .env)
$env:OSHINEI_WRITE_TOKEN = "your-token-here"

# Run the script
python bulk_recon_upload.py --url "https://oshinei-dashboard.fly.dev:8443" --folder "All recon data"
```

**Option B: Dry-run first (see what would upload)**

```powershell
$env:OSHINEI_WRITE_TOKEN = "your-token-here"
python bulk_recon_upload.py --url "https://oshinei-dashboard.fly.dev:8443" --folder "All recon data" --dry-run
```

**Option C: Let me (Claude) run it via your computer**

Tell me "run the upload" and I'll use your computer to execute it. I'll need the write token first.

## Getting Your Write Token

The write token is set in Fly production as `OSHINEI_SEED_TOKEN`. You can:

1. Check your `.env` file locally (if you have a copy)
2. Ask your admin for the token
3. Look in your Fly secrets dashboard at https://fly.io

If you don't have it, the upload will fail with a 403 error.

## What the Script Does

- **Reads** the "All recon data" folder (910 files, ~6 branches, May 2026 data)
- **Maps** branch/channel names to database IDs automatically
- **Uploads** each document to `/api/uploads/{type}/confirm` with the right metadata
- **Parallelizes** 4 uploads at once (customizable with `--workers`)
- **Reports** success/failure for each file

It's idempotent — uploading the same file twice is rejected by the app (same file hash), so safe to re-run.

## Examples

```powershell
# Run with all defaults (4 parallel workers)
python bulk_recon_upload.py --token "my-token"

# Verbose mode (see every upload)
python bulk_recon_upload.py --token "my-token" --verbose

# Slower but safer (1 worker)
python bulk_recon_upload.py --token "my-token" --workers 1

# Different folder
python bulk_recon_upload.py --token "my-token" --folder "C:\path\to\recon\data"
```

## Next Steps

1. Get your write token
2. Run with `--dry-run` to preview
3. Run without `--dry-run` to actually upload
4. Check the recon app → browse uploaded documents by branch/month

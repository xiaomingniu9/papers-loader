# Papers Loader

A tool that automatically downloads research papers (PDFs) from chemistry and biology journals, organized into date-named directories.

## Supported Journals

- Nature Chemical Biology
- Nature (Research Articles)
- Nature Chemistry
- Nature Reviews Chemistry
- Nature Reviews Drug Discovery
- Nature Reviews Molecular Cell Biology
- Cell Chemical Biology
- Science
- JACS (Journal of the American Chemical Society)

## Setup

**Requirements:** Python 3.10+, Google Chrome

```bash
pip3 install -r tools/requirements.txt
```

## Usage

```bash
# Download all papers published today
python3 tools/download_papers.py

# Download from a specific journal
python3 tools/download_papers.py --url "https://www.nature.com/nchem/research-articles"

# Download papers from a specific date
python3 tools/download_papers.py --date 2026-03-13

# Preview without downloading
python3 tools/download_papers.py --dry-run

# Show browser window (needed for Cloudflare-protected sites like Science/ACS)
python3 tools/download_papers.py --visible
```

Papers are saved to `~/Downloads/research-papers/YYYY-MM-DD/`.

## Auto Mode

Schedule daily downloads at 12 PM using macOS `launchd`:

```bash
# Enable
python3 tools/download_papers.py --install-schedule

# Disable
python3 tools/download_papers.py --uninstall-schedule
```

If your Mac is asleep at 12 PM, the job runs when it wakes up.

## Adding Journals

1. Add the journal URL and parser type to `tools/journal_configs.json`
2. If the publisher is new (not Nature/Cell/Science/ACS), add a parser class in `tools/download_papers.py`
3. Test with `--dry-run`

## Note on Cloudflare

Science and ACS use aggressive bot detection. Use `--visible` to open a browser window where you can solve the challenge manually. The other 7 journals work fully automatically.

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

# Preview papers from a specific date
python3 tools/download_papers.py --date 2026-03-10 --dry-run

# Show browser window (needed for Cloudflare-protected sites like Science/ACS)
python3 tools/download_papers.py --visible

# Combine flags (e.g., specific journal + specific date)
python3 tools/download_papers.py --url "https://www.nature.com/nchem/research-articles" --date 2026-03-10
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

## Customizing Journals

All supported journals are configured in `tools/journal_configs.json`. You can add, remove, or edit journals freely.

Each entry looks like this:

```json
{
  "name": "Nature Chemistry",
  "url": "https://www.nature.com/nchem/research-articles",
  "rss_url": "https://www.nature.com/nchem.rss",
  "parser": "nature"
}
```

| Field | Description |
|-------|-------------|
| `name` | Display name (used as the folder name) |
| `url` | The journal's article listing page |
| `rss_url` | RSS feed URL (optional, used as fallback when the site is blocked) |
| `parser` | Which parser to use: `nature`, `cell`, `science`, or `acs` |

**To add a journal:** Copy an existing entry, update the fields, and pick the parser that matches the publisher. Test with `--dry-run` to verify.

**To remove a journal:** Delete its entry from the JSON file.

**To add a new publisher:** If the journal uses a publisher other than Nature/Cell/Science/ACS, you'll need to add a new parser class in `tools/download_papers.py`.

## Note on Cloudflare

Science and ACS use aggressive bot detection. Use `--visible` to open a browser window where you can solve the challenge manually. The other 7 journals work fully automatically.

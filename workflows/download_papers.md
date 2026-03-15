# Download Research Papers

## Objective
Automatically download research papers (PDFs) from 9 chemistry/biology journals, organized into date-named directories.

## Prerequisites
- Python 3.10+
- Google Chrome installed
- Dependencies: `pip3 install -r tools/requirements.txt`

## Manual Mode

```bash
# Download all papers published today from all journals
python3 tools/download_papers.py

# Download from a specific journal URL
python3 tools/download_papers.py --url "https://www.nature.com/nchem/research-articles"

# Download papers from a specific date
python3 tools/download_papers.py --date 2026-03-13

# Preview what would be downloaded (no actual downloads)
python3 tools/download_papers.py --dry-run

# Show browser window (needed for Science/ACS Cloudflare challenges)
python3 tools/download_papers.py --visible

# Combine flags
python3 tools/download_papers.py --url "https://www.nature.com/nchem/research-articles" --date 2026-03-13 --dry-run --verbose
```

## Auto Mode (Daily at 12 PM)

```bash
# Install daily schedule
python3 tools/download_papers.py --install-schedule

# Remove daily schedule
python3 tools/download_papers.py --uninstall-schedule
```

Uses macOS `launchd` (runs missed jobs if Mac was asleep at 12 PM).

## Output Location
```
~/Downloads/research-papers/
  2026-03-13/
    Chemical recycling of hydrofluorocarbons by transfer fluorination.pdf
    ...
  2026-03-14/
    ...
```

## Supported Journals

| Journal | Parser | Status |
|---------|--------|--------|
| Nature Chemical Biology | nature | Works |
| Nature (Research Articles) | nature | Works |
| Nature Chemistry | nature | Works |
| Nature Reviews Chemistry | nature | Works |
| Nature Reviews Drug Discovery | nature | Works |
| Nature Reviews Molecular Cell Biology | nature | Works |
| Cell Chemical Biology | cell | Works |
| Science | science | Cloudflare-blocked; use `--visible` |
| JACS | acs | Cloudflare-blocked; use `--visible` |

## Cloudflare-Protected Sites
Science and ACS have aggressive bot detection. When running headless, these sites will be skipped with a warning. To access them:
1. Run with `--visible` flag
2. Solve the Cloudflare challenge manually in the browser window
3. The script will detect when the challenge is solved and continue
4. Subsequent headless runs may work using the saved browser profile

## Troubleshooting

- **ChromeDriver version mismatch**: Delete `.tmp/chrome-profile/` and re-run. `webdriver-manager` auto-downloads the correct version.
- **No articles found for a date**: Most journals don't publish on weekends. Use `--dry-run` to check what dates are available.
- **PDF validation failures**: Some articles may be behind institutional paywalls. The script validates downloaded files are actual PDFs.
- **Logs**: Check `.tmp/paper_download.log` for detailed output.

## Adding New Journals
1. Edit `tools/journal_configs.json` to add the new journal URL and parser type
2. If the journal uses a new publisher (not Nature/Cell/Science/ACS), add a new parser class in `tools/download_papers.py`
3. Test with `--dry-run` first

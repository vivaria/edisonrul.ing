#!/usr/bin/env python3
"""
fetch.py  —  Fetch rulings for edisonformat.com and edisonformat.net.

Writes a single, fully-rendered Markdown file per card, with edisonformat.net
(post-UTW) content first and edisonformat.com (historical, pre-UTW) second.

The cardpool CSV (default: edison_cardpool.csv) is the source of truth for
which slugs receive a file. Cards found by the efcom fetcher but absent from
the CSV are logged but not written.

The efnet phase reads from a local clone of the edisonformat.net card-data
repository (one JSON file per card, named by numeric ID).  No network requests
are made for that phase.
"""

from __future__ import annotations

import re
import asyncio
import argparse
import json
import tqdm
import unicodedata
from pathlib import Path
from dataclasses import dataclass

import pandas as pd
from bs4 import BeautifulSoup
import markdownify
from playwright.async_api import async_playwright


# ============================================================================
# SECTION 1 — CONFIG & CONSTANTS
# ============================================================================

EFCOM_URLS = [
    "https://www.edisonformat.com/rulings/individual-rulings-a-c",
    "https://www.edisonformat.com/rulings/individual-rulings-d-e",
    "https://www.edisonformat.com/rulings/individual-rulings-f-h",
    "https://www.edisonformat.com/rulings/individual-rulings-i-k",
    "https://www.edisonformat.com/rulings/individual-rulings-l-o",
    "https://www.edisonformat.com/rulings/individual-card-rulings-p-r",
    "https://www.edisonformat.com/rulings/individual-card-rulings-s-t",
    "https://www.edisonformat.com/rulings/rules-u-z",
]

OUTPUT_ROOT          = Path("docs/source/cards").resolve()
CARDPOOL_CSV_DEFAULT = "edison_cardpool.csv"
PAGE_LOAD_TIMEOUT    = 30_000   # ms  (efcom)
JS_SETTLE_MS         =  2_000   # ms  (efcom)

# ---------------------------------------------------------------------------
# Errata / name-mapping table
#
# Maps the slug derived from the name on edisonformat.com to the canonical
# slug used in the cardpool CSV.
#
# Sources:
#   errata  – official OCG/TCG errata changed the printed card name
#   typo    – the rulings page has a misspelling vs the canonical name
#   encode  – non-ASCII character handled differently by the CSV slugifier
#   prefix  – the CSV includes an archetype prefix absent on the rulings page
# ---------------------------------------------------------------------------

ERRATA_SLUG_MAP: dict[str, str] = {
    # errata: name officially changed
    "after-genocide":                "after-the-struggle",
    "amazon-archer":                 "amazoness-archer",
    "crystal-counter":               "counter-gem",
    "earthbound-spirits-invitation": "call-of-the-earthbound",
    "hidden-book-of-spell":          "hidden-spellbook",
    "metaphysical-regeneration":     "supernatural-regeneration",
    "null-and-void":                 "muko",
    "red-eyes-b-chick":              "black-dragons-chick",
    "skull-zoma":                    "zoma-the-spirit",
    # typo on the rulings page
    "armityle-the-chaos-phantom":          "armityle-the-chaos-phantasm",
    "beast-king-barbaros-ur":              "beast-machine-king-barbaros-ur",
    "blackwing-silverwind-the-ascendent":  "blackwing-silverwind-the-ascendant",
    "cemetery-bomb":                       "cemetary-bomb",
    "cliff-the-trap-remover":              "dark-scorpion-cliff-the-trap-remover",
    "destruction-potion":                  "destruct-potion",
    "flint-missle":                        "flint-missile",
    "ive-shackles":                        "ivy-shackles",
    "level-returner":                      "level-retuner",
    "ogre-of-scarlet-sorrow":              "ogre-of-the-scarlet-sorrow",
    "paladin-of-cursed-dragon":            "paladin-of-the-cursed-dragon",
    "roar-of-the-earthbound":              "roar-of-the-earthbound-immortal",
    "shredder":                            "shreddder",
}


# ============================================================================
# SECTION 2 — UTILITIES
# ============================================================================

# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """
    Convert a card name to a URL/filesystem-safe slug matching the
    conventions used in the Edison cardpool CSV.
    """
    s = name.lower()
    # Normalize accented characters to ASCII base (Ü → U), drop non-decomposable non-ASCII
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    # " - " card-name separators → single hyphen
    s = re.sub(r"\s+-\s+", "-", s)
    s = s.replace("'", "")
    s = s.replace("d. d.", "d.d.")  # special case: extra space before dot removal
    s = s.replace(".", "")
    s = s.replace("&", "")
    s = s.replace("/", "")
    s = re.sub(r"[^\w\s-]", " ", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s


def resolve_slug(raw_slug: str) -> str:
    """Return the canonical CSV slug, applying the errata map if needed."""
    return ERRATA_SLUG_MAP.get(raw_slug, raw_slug)


def output_path(slug: str) -> Path:
    """Return the canonical output path for a given slug."""
    subfolder = "0-9" if slug[0].isdigit() else slug[0].upper()
    return OUTPUT_ROOT / subfolder / f"{slug}.md"


# ---------------------------------------------------------------------------
# CSV helpers  (inlined from reformat_txt)
# ---------------------------------------------------------------------------

def csv_to_dict(csv_path: str) -> dict[str, dict]:
    """
    Load the cardpool CSV into an ordered dict keyed by slug.

    Expected columns (at minimum): slug, card_name, id.
    """
    df = pd.read_csv(csv_path)
    df["slug"] = df["slug"].str.strip()
    result: dict[str, dict] = {}
    for _, row in df.iterrows():
        slug = row["slug"]
        result[slug] = row.to_dict()
    return result


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_NO_TEXT = "No card text found for this card."
_NO_RULING = "No rulings found for this card."

@dataclass
class EfnetData:
    card_name: str
    url: str
    psct: str
    rulings: str


@dataclass
class EfcomData:
    card_name: str
    source_url: str
    card_text: str
    rulings: str


def _render_efnet_section(data: EfnetData | None) -> str:
    psct_block = data.psct.replace("\n", "\n> ") if data.psct else _NO_TEXT
    psct_block = "\n".join(line.strip() for line in psct_block.split("\n"))
    rulings_block_raw = data.rulings if data.rulings else _NO_RULING
    rulings_list = []
    for ruling in rulings_block_raw.split("\n"):
        ruling = re.sub(r"●The ●", "●", ruling)
        ruling = re.sub(r"^●\s?(.+)$", r"*   \1", ruling).strip()
        rulings_list.append(ruling)
    rulings_block = "\n".join(rulings_list)
    return (
        "## Edisonformat.net (Revised, Post-UTW Rulings)\n\n"
        f"Source: [{data.url}]({data.url})\n\n"
        f"### Edison-Accurate PSCT\n\n"
        f"> {psct_block}\n\n"
        f"### Card Rulings\n\n"
        f"{rulings_block}"
    )


def _render_efcom_section(data: EfcomData | None) -> str:
    if data is None:
        return (
            "## Edisonformat.com (Historical, Pre-UTW Rulings)\n\n"
            f"### Card Text\n\n"
            f"> {_NO_TEXT}\n\n"
            f"### Card Rulings\n\n"
            f"{_NO_RULING}\n\n"
        )
    card_text_block = data.card_text if data.card_text else _NO_TEXT
    # add block quotes to multi-line card text
    card_text_block = "\n".join(f'> {line}' for line in card_text_block.split("\n"))
    rulings_block = data.rulings if data.rulings else _NO_RULING
    return (
        "## Edisonformat.com (Historical, Pre-UTW Rulings)\n\n"
        f"Source: [https://www.edisonformat.com/rulings](https://www.edisonformat.com/rulings)\n\n"
        f"### Card Text\n\n"
        f"{card_text_block}\n\n"
        f"### Card Rulings\n\n"
        f"{rulings_block}\n\n"
    )


def build_full_markdown(
    card_name: str,
    efnet: EfnetData | None,
    efcom: EfcomData | None,
) -> str:
    """Render the complete Markdown file for a card from both data sources."""
    efnet_section = _render_efnet_section(efnet)
    efcom_section = _render_efcom_section(efcom)
    return f"# {card_name}\n\n{efnet_section}\n\n\n{efcom_section}\n"


# ============================================================================
# SECTION 3 — EFCOM FETCH PHASE  (sync Playwright)
# ============================================================================

async def _efcom_fetch_page(page, url: str) -> BeautifulSoup:
    await page.goto(url, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
    await page.wait_for_timeout(JS_SETTLE_MS)
    return BeautifulSoup(await page.content(), "html.parser")


def _efcom_extract_rulings_html(soup: BeautifulSoup) -> str:
    """Return the HTML of the rulings content div, stripped of noise tags."""
    for div in soup.find_all("div", class_="blog-content"):
        if div.find("strong"):
            for tag in div.find_all(["script", "style"]):
                tag.decompose()
            return str(div)
    raise ValueError("Could not find rulings in efcom html.")


def _efcom_html_to_markdown(html: str) -> str:
    md = markdownify.markdownify(
        html,
        heading_style="ATX",
        bullets="*-",
        strip=["script", "style", "nav", "header", "footer"],
    )
    return re.sub(r"\n{3,}", "\n\n", md).strip()


_CARD_HEADING_RE = re.compile(
    r"^[ \t]*\*\*(.+?)\*\*",
    re.MULTILINE,
)


def _efcom_split_into_cards(markdown: str) -> list[tuple[str, str]]:
    """Split a full-page markdown blob into (card_name, card_markdown) pairs."""
    # Special case fix for Alien Hypno:
    # 1. Merge the split italic block:
    #    Matches the closing `*` of the first italic span + optional whitespace +
    #    a `**● ` that opens a bold span, and replaces with just ` ●` (staying in italic).
    markdown = re.sub(r'\*\s*\n\*\*● ', ' ● ', markdown)
    # 2. Remove the stray closing `**` at the end of the merged effect text.
    #    This bold closer is left over from the original `**● ...**` wrapping.
    markdown = re.sub(r'(\(3\).*?)\*\*$', r'\1', markdown, flags=re.DOTALL)

    matches = list(_CARD_HEADING_RE.finditer(markdown))
    if not matches:
        return []
    cards = []
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        cards.append((name, markdown[start:end].strip()))
    return cards


def _efcom_parse_card(card_md: str, source_url: str) -> EfcomData:
    """
    Extract card_name, card_text, and rulings from a single card's markdown
    block as fetched from edisonformat.com.
    """
    lines = card_md.strip().splitlines()

    card_name_raw = ""
    card_name_index = 0
    card_text_index = -1

    lines_stripped = []
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        # fix insufficiently-indented bullet points
        # hacky patch for https://github.com/matthewwithanm/python-markdownify/issues/211
        stripped = re.sub(r"^ {8}\*", (2 * 8 * " ") + "*", stripped)
        stripped = re.sub(r"^ {6}-", (2 * 6 * " ") + "-", stripped)
        stripped = re.sub(r"^ {4}\*", (2 * 4 * " ") + "*", stripped)
        stripped = re.sub(r"^ {2}-", (2 * 2 * " ") + "-", stripped)
        stripped = re.sub(r"^ {2}(\d.)", r"    \1", stripped)
        for pattern, symbol in [("(1)", "①"), ("(2)", "②"), ("(3)", "③"), ("(4)", "④"), ("(5)", "⑤"),
                                ("(A)", "Ⓐ"), ("(B)", "Ⓑ"),
                                ("(C)", "Ⓒ"), ("(L)", "③"), ("(M)", "Ⓜ"), ("(U)", "Ⓤ"), ("(S)", "Ⓢ")]:
            stripped = re.sub(re.escape(pattern), symbol, stripped)
        lines_stripped.append(stripped)
        if stripped.startswith("**") and stripped.endswith("**") and not card_name_raw:
            card_name_raw = stripped
            card_name_index = i
        elif stripped == "" and not card_text_index > 0:
            card_text_index = i

    # Remove bold from title
    card_name  = re.sub(r"^\*\*(.+)\*\*$", r"\1", card_name_raw)

    # Normal loop: Parse card text and rulings
    if card_text_index > 0:
        # Remove italics from single and multiline card text
        card_text_lines = lines_stripped[card_name_index + 1:card_text_index]
        card_text_raw = "|".join(card_text_lines)
        card_text = re.sub(r"^\*(.+)\*$", r"\1", card_text_raw)
        card_text = "\n".join(card_text.split("|"))
        # Fix bullet points and internal efcom URLs in rulings
        rulings_lines = lines_stripped[card_text_index + 1:] if card_text_index >= 0 else []
        rulings_lines = [re.sub(r"^\* (.+)$", r"*   \1", line) for line in rulings_lines if line]
        rulings = "\n".join(rulings_lines)
        rulings = re.sub(r"]\((/home/.+)\)", r"](https://www.edisonformat.com\1)", rulings)
    # Exception: If no empty line was found (i.e. -1), something is wrong with this card. Check whether the next line is italic (card text)
    else:
        next_line = lines_stripped[card_name_index + 1]
        # Italic? All lines are card text
        if next_line.startswith('*') and next_line.endswith('*'):
            card_text_lines = lines_stripped[card_name_index + 1:]
            card_text_raw = "|".join(card_text_lines)
            rulings_lines = []
        # Not italic? All lines are rulings
        else:
            card_text_raw = ""
            rulings_lines = lines_stripped[card_name_index + 1:]
        # Remove italics from single and multiline card text
        card_text = re.sub(r"^\*(.+)\*$", r"\1", card_text_raw)
        card_text = "\n".join(card_text.split("|"))
        # Fix bullet points and internal efcom URLs in rulings
        rulings_lines = [re.sub(r"^\* (.+)$", r"*   \1", line) for line in rulings_lines if line]
        rulings = "\n".join(rulings_lines)
        if "[REF]" in rulings and "http" not in rulings:
            rulings = re.sub(r"\[REF]\((.+)\)", r"[REF](https://www.edisonformat.com\1)", rulings)


    return EfcomData(
        card_name=card_name,
        source_url=source_url,
        card_text=card_text,
        rulings=rulings,
    )


async def fetch_efcom_cards(skip: bool = False) -> dict[str, EfcomData]:
    """
    Async Playwright phase: fetch all edisonformat.com rulings pages sequentially.

    Returns a dict mapping final (errata-resolved) slug → EfcomData.
    Cards that hit an errata mapping are logged.  Duplicates are skipped.
    """
    if skip:
        print("[efcom] Skipping efcom phase (--skip-efcom).")
        return {}

    results: dict[str, EfcomData] = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        for url in EFCOM_URLS:
            print(f"[efcom] Fetching {url}")
            try:
                soup = await _efcom_fetch_page(page, url)
            except Exception as exc:
                print(f"[efcom]   ERROR: {exc}")
                continue

            rulings_html = _efcom_extract_rulings_html(soup)
            markdown     = _efcom_html_to_markdown(rulings_html)
            cards        = _efcom_split_into_cards(markdown)

            print(f"[efcom]   Found {len(cards)} cards.")
            for card_name, card_md in cards:
                raw_slug   = slugify(card_name)
                final_slug = resolve_slug(raw_slug)

                if raw_slug != final_slug:
                    print(f"[efcom]   ERRATA  {raw_slug!r} -> {final_slug!r}")

                if final_slug in results:
                    existing = results[final_slug].card_name
                    print(f"[efcom]   DUPLICATE {final_slug!r} ('{card_name}' vs '{existing}') — skipping")
                    continue

                results[final_slug] = _efcom_parse_card(card_md, source_url=url)

        await browser.close()

    print(f"[efcom] Done. {len(results)} cards fetched.")
    return results


# ============================================================================
# SECTION 4 — EFNET LOAD PHASE  (local JSON repository)
# ============================================================================


def _efnet_field(data: dict, key: str) -> str:
    """
    Return data[key]["Edison"], falling back to data[key]["Base"].
    Returns "" if both are absent or start with a known sentinel phrase.
    """
    for variant in ("Edison", "Base"):
        value = data.get(key, {}).get(variant, "")
        if value:
            return value
    return ""


def load_efnet_cards(
    json_dir: str,
    card_data: dict[str, dict],
) -> dict[str, EfnetData]:
    """
    Read efnet card JSON files from a local repository clone.

    Each file is named {id}.json where id matches the 'id' column in the
    cardpool CSV.  Only slugs present in card_data are processed.

    Returns a dict mapping slug → EfnetData.
    """
    json_path = Path(json_dir)
    results: dict[str, EfnetData] = {}
    missing_files: list[str] = []

    for slug, card in card_data.items():
        card_id = card.get("id")
        if not card_id:
            missing_files.append(slug)
            continue

        path = json_path / f"{int(card_id)}.json"
        if not path.is_file():
            missing_files.append(slug)
            continue

        with open(path, encoding="utf-8") as f:
            try:
                raw = json.load(f)
            except json.decoder.JSONDecodeError:
                print(f"Failed to decode {path}")
                continue

        results[slug] = EfnetData(
            card_name=raw.get("name", card.get("card_name", slug)),
            url=card["url_efnet"],
            psct=_efnet_field(raw, "PSCT"),
            rulings=_efnet_field(raw, "Rulings"),
        )

    print(f"[efnet] Loaded {len(results)} cards from '{json_dir}'.")
    if missing_files:
        print(f"[efnet] {len(missing_files)} slugs had no matching JSON file:")
        for slug in sorted(missing_files):
            print(f"  {slug}")

    return results


# ============================================================================
# SECTION 5 — MERGE & WRITE PHASE
# ============================================================================

def merge_and_write(
    card_data: dict[str, dict],
    efnet_cards: dict[str, EfnetData],
    efcom_cards: dict[str, EfcomData],
) -> None:
    """
    For every slug in the cardpool CSV, render and write a single Markdown
    file combining both sources.  Logs a reconciliation report at the end.
    """
    csv_slugs   = set(card_data.keys())
    efcom_slugs = set(efcom_cards.keys())

    # efcom cards that are absent from the CSV (banned, section headers, etc.)
    efcom_extras = efcom_slugs - csv_slugs

    written      = 0
    no_efnet     = 0
    no_efcom     = 0
    no_either    = 0

    for slug, card in tqdm.tqdm(card_data.items()):
        card_name = card.get("card_name", slug)

        efnet = efnet_cards.get(slug)
        efcom = efcom_cards.get(slug)

        if efnet is None:
            no_efnet += 1
        if efcom is None:
            no_efcom += 1
        if efnet is None and efcom is None:
            no_either += 1

        # Use the card_name from whichever source we have, preferring efnet
        display_name = (
            efnet.card_name if efnet
            else card_name
        )

        path = output_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            build_full_markdown(display_name, efnet, efcom),
            encoding="utf-8",
        )
        written += 1

    # --- Reconciliation report ---
    not_fetched_efnet = csv_slugs - set(efnet_cards.keys())
    not_fetched_efcom = csv_slugs - efcom_slugs

    print("\n" + "=" * 70)
    print("RECONCILIATION REPORT")
    print("=" * 70)
    print(f"  CSV cards (source of truth)  : {len(csv_slugs)}")
    print(f"  Files written                : {written}")
    print(f"  Missing efnet data           : {no_efnet}")
    print(f"  Missing efcom data           : {no_efcom}")
    print(f"  Missing BOTH sources         : {no_either}")

    if efcom_extras:
        print(f"\n── efcom slugs NOT in cardpool CSV ({len(efcom_extras)}) ──")
        print("   Likely: banned cards, section-header false positives, or")
        print("   cards not yet added to the errata map.  No file written.")
        for slug in sorted(efcom_extras):
            print(f"   {slug!r:<52}  ←  '{efcom_cards[slug].card_name}'")

    if not_fetched_efnet:
        print(f"\n── In CSV but no efnet data ({len(not_fetched_efnet)}) ──")
        for slug in sorted(not_fetched_efnet):
            print(f"   {slug}")

    if not_fetched_efcom:
        print(f"\n── In CSV but no efcom data ({len(not_fetched_efcom)}) ──")
        for slug in sorted(not_fetched_efcom):
            print(f"   {slug}")

    print("=" * 70)


# ============================================================================
# SECTION 6 — ENTRY POINT
# ============================================================================

async def _async_main(args: argparse.Namespace) -> None:
    # Load cardpool CSV (source of truth)
    card_data = csv_to_dict(args.input)
    print(f"Loaded {len(card_data)} cards from '{args.input}'.\n")

    # Phase A — efnet (local JSON reads, synchronous)
    efnet_cards = load_efnet_cards(args.json_dir, card_data)
    print()

    # Phase B — efcom (async, sequential pages)
    efcom_cards = await fetch_efcom_cards(skip=args.skip_efcom)
    print()

    # Phase C — merge & write
    merge_and_write(card_data, efnet_cards, efcom_cards)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Combined edisonformat.com + edisonformat.net fetchr",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--json-dir",   required=True,
                        help="Path to the cloned efnet card JSON directory")
    parser.add_argument("--skip-efcom", action="store_true",
                        help="Skip the edisonformat.com fetch phase entirely")
    parser.add_argument("--input",      default=CARDPOOL_CSV_DEFAULT,
                        help=f"Cardpool CSV file (default: {CARDPOOL_CSV_DEFAULT})")

    args = parser.parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
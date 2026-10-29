#!/usr/bin/env python3
# SPDX-License-Identifier: MIT OR Apache-2.0
# Copyright (c) 2026 Kiwi Akio
"""
batcave-dl: Web scraper and CBZ downloader for batcave.biz comic reading lists.

Reads comic URLs from current_batcave_reading_list.md, creates a folder for each
comic, checks for new issues, and downloads missing issues as .cbz archives
without overwriting existing files.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
import ctypes.util
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import zipfile

DEFAULT_BASE_DIR = Path.home() / "Downloads" / "Comics"
DEFAULT_READING_LIST = DEFAULT_BASE_DIR / "current_batcave_reading_list.md"
REPO_READING_LIST = Path(__file__).resolve().parent / "current_batcave_reading_list.md"
CACHE_DIR = Path.home() / ".cache" / "batcave-dl"
COOKIE_JAR = CACHE_DIR / "cookies.txt"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
)


def _init_native_webp_jpeg() -> tuple[ctypes.CDLL | None, ctypes.CDLL | None]:
    """Load libwebp and libturbojpeg via ctypes for fast in-process WebP -> JPEG conversion."""
    try:
        webp_name = ctypes.util.find_library("webp")
        tj_name = ctypes.util.find_library("turbojpeg")
        if not webp_name or not tj_name:
            return None, None
        libwebp = ctypes.CDLL(webp_name)
        libtj = ctypes.CDLL(tj_name)

        libwebp.WebPDecodeRGB.argtypes = [
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        libwebp.WebPDecodeRGB.restype = ctypes.c_void_p
        libwebp.WebPFree.argtypes = [ctypes.c_void_p]
        libwebp.WebPFree.restype = None

        libtj.tjInitCompress.argtypes = []
        libtj.tjInitCompress.restype = ctypes.c_void_p
        libtj.tjDestroy.argtypes = [ctypes.c_void_p]
        libtj.tjDestroy.restype = ctypes.c_int
        libtj.tjFree.argtypes = [ctypes.c_void_p]
        libtj.tjFree.restype = None
        libtj.tjCompress2.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        libtj.tjCompress2.restype = ctypes.c_int
        return libwebp, libtj
    except Exception:
        return None, None


_LIBWEBP, _LIBTJ = _init_native_webp_jpeg()


def normalize_series_title(title: str) -> str:
    """Normalize ongoing/completed year ranges like '(2024-)' or '(2025-2026)' to '(2024)'."""
    title = title.strip()
    title = re.sub(r"\((\d{4})-[^)]*\)", r"(\1)", title)
    return sanitize_filename(title)


def normalize_issue_title(title: str) -> str:
    """Normalize chapter title and zero-pad single-digit issue numbers (#1 -> #01)."""
    title = normalize_series_title(title)
    # Zero-pad simple issue numbers like '#1' -> '#01' (leave '#10', '#Annual 2025' intact)
    title = re.sub(r"#(\d)(?!\d)", r"#0\1", title)
    return sanitize_filename(title)


def sanitize_filename(name: str) -> str:
    """Remove filesystem-unsafe characters while keeping readable comic titles."""
    name = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*●ᴥ◆◇□■△▲▽▼○◎]', "_", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name[:150] or "Unknown"


def detect_image_ext(data: bytes, url: str = "") -> str:
    """Sniff true image extension from magic bytes (batcave serves WebP under .jpg URLs)."""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return "avif"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    m = re.search(r"\.(jpe?g|png|webp|avif|gif|bmp)(?:\?|$)", url, re.I)
    return m.group(1).lower() if m else "jpg"


def convert_to_jpg(data: bytes, quality: int = 90) -> bytes:
    """
    Convert WebP/AVIF image bytes to standard JPEG (far smaller and faster than PNG
    for full-color comic pages, and supported natively by e-ink readers like Supernote).
    """
    # 1. Fast in-process path via libwebp + libturbojpeg
    if (
        _LIBWEBP is not None
        and _LIBTJ is not None
        and len(data) >= 12
        and data[:4] == b"RIFF"
        and data[8:12] == b"WEBP"
    ):
        w = ctypes.c_int()
        h = ctypes.c_int()
        rgb_ptr = _LIBWEBP.WebPDecodeRGB(data, len(data), ctypes.byref(w), ctypes.byref(h))
        if rgb_ptr:
            try:
                handle = _LIBTJ.tjInitCompress()
                if handle:
                    try:
                        jpeg_buf = ctypes.c_void_p()
                        jpeg_size = ctypes.c_ulong()
                        # TJPF_RGB=0, TJSAMP_420=2
                        rc = _LIBTJ.tjCompress2(
                            handle,
                            rgb_ptr,
                            w.value,
                            0,
                            h.value,
                            0,
                            ctypes.byref(jpeg_buf),
                            ctypes.byref(jpeg_size),
                            2,
                            quality,
                            0,
                        )
                        if rc == 0 and jpeg_buf and jpeg_size.value > 0:
                            try:
                                out = ctypes.string_at(jpeg_buf.value, jpeg_size.value)
                                if out.startswith(b"\xff\xd8\xff"):
                                    return out
                            finally:
                                _LIBTJ.tjFree(jpeg_buf)
                    finally:
                        _LIBTJ.tjDestroy(handle)
            finally:
                _LIBWEBP.WebPFree(rgb_ptr)

    # 2. Fallback to CLI converters (GraphicsMagick -> ImageMagick -> ffmpeg)
    for cmd in (
        ["gm", "convert", "-", "-quality", str(quality), "jpeg:-"],
        ["magick", "-", "-quality", str(quality), "jpeg:-"],
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-q:v",
            "3",
            "-f",
            "mjpeg",
            "pipe:1",
        ],
    ):
        if shutil.which(cmd[0]) is None:
            continue
        res = subprocess.run(
            cmd,
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if res.returncode == 0 and res.stdout.startswith(b"\xff\xd8\xff"):
            return res.stdout
    return data


def sync_browser_cookies(cookie_jar: Path) -> None:
    """Seed Netscape cookie jar with batcave.biz auth cookies from qutebrowser or Firefox."""
    cookie_jar.parent.mkdir(parents=True, exist_ok=True)
    auth_cookies: dict[str, str] = {}

    # 1. Check qutebrowser Chromium cookie store
    qb_cookies = Path.home() / ".local" / "share" / "qutebrowser" / "webengine" / "Cookies"
    if qb_cookies.is_file():
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=True) as tmp:
            shutil.copy2(qb_cookies, tmp.name)
            try:
                conn = sqlite3.connect(tmp.name)
                rows = conn.execute(
                    "SELECT name, value FROM cookies WHERE host_key LIKE '%batcave.biz%' "
                    "AND name IN ('dle_user_id', 'dle_password', 'dle_hash')"
                ).fetchall()
                for name, val in rows:
                    if val:
                        auth_cookies[name] = val
                conn.close()
            except sqlite3.Error:
                pass

    # 2. Check Firefox profiles if not found in qutebrowser
    if not auth_cookies:
        for ff_db in glob.glob(str(Path.home() / ".mozilla" / "firefox" / "*" / "cookies.sqlite")):
            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=True) as tmp:
                shutil.copy2(ff_db, tmp.name)
                try:
                    conn = sqlite3.connect(tmp.name)
                    rows = conn.execute(
                        "SELECT name, value FROM moz_cookies WHERE host LIKE '%batcave.biz%' "
                        "AND name IN ('dle_user_id', 'dle_password', 'dle_hash')"
                    ).fetchall()
                    for name, val in rows:
                        if val:
                            auth_cookies[name] = val
                    conn.close()
                except sqlite3.Error:
                    pass
            if auth_cookies:
                break

    # Preserve existing non-auth cookies (such as __guard_ref) from cookie_jar
    existing_lines: list[str] = []
    if cookie_jar.is_file():
        for line in cookie_jar.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 7 and parts[5] in auth_cookies:
                continue
            existing_lines.append(line)

    with cookie_jar.open("w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for line in existing_lines:
            f.write(line + "\n")
        for name, val in auth_cookies.items():
            f.write(f".batcave.biz\tTRUE\t/\tTRUE\t2147483647\t{name}\t{val}\n")


def solve_pow_challenge(html: str, cookie_jar: Path) -> bool:
    """Detect and solve BatCave's SHA-256 proof-of-work anti-bot challenge."""
    m = re.search(r'token:\s*"([^"]+)"', html)
    if not m or "/_v" not in html:
        return False

    token = m.group(1)
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{token}:{nonce}".encode("utf-8")).hexdigest()
        if digest.startswith("00"):
            break
        nonce += 1

    body = urllib.parse.urlencode(
        {
            "token": token,
            "mode": "modern",
            "workTime": "42.5",
            "iterations": str(nonce + 1),
            "hasCrypto": "1",
            "pow_nonce": str(nonce),
            "pow_hash": digest,
            "webdriver": "0",
            "touch": "0",
            "screen_w": "1920",
            "screen_h": "1080",
            "screen_cd": "24",
            "tz": "420",
            "dpr": "1",
            "cdp": "0",
            "cdpf": "",
        }
    )

    subprocess.run(
        [
            "curl",
            "-sL",
            "--max-time",
            "20",
            "-c",
            str(cookie_jar),
            "-b",
            str(cookie_jar),
            "-A",
            USER_AGENT,
            "-X",
            "POST",
            "-d",
            body,
            "https://batcave.biz/_v",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    return True


def fetch_html(url: str, cookie_jar: Path) -> str:
    """Fetch HTML page from batcave.biz, solving PoW challenge automatically if prompted."""
    for _ in range(3):
        res = subprocess.run(
            [
                "curl",
                "-sL",
                "--max-time",
                "30",
                "-c",
                str(cookie_jar),
                "-b",
                str(cookie_jar),
                "-A",
                USER_AGENT,
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        html = res.stdout
        if solve_pow_challenge(html, cookie_jar):
            continue
        return html
    return html


def extract_window_data(html: str) -> dict:
    """Extract window.__DATA__ JSON object from a comic or reader HTML page."""
    m = re.search(r"window\.__DATA__\s*=\s*\{", html)
    if not m:
        if "Sign in to keep reading" in html:
            raise RuntimeError(
                "BatCave.biz requires sign-in. Please log in via qutebrowser or Firefox first."
            )
        raise RuntimeError("Could not find window.__DATA__ on page.")
    brace_start = m.end() - 1
    data, _ = json.JSONDecoder().raw_decode(html, brace_start)
    images = data.get("images")
    pages = data.get("pages")
    if isinstance(images, list) and isinstance(pages, int) and 0 < pages < len(images):
        data["images"] = images[:pages]
    return data


def fetch_page_bytes(url: str, cookie_jar: Path, retries: int = 4) -> bytes:
    """Download a single comic page image with retry backoff."""
    last_err = None
    for attempt in range(retries):
        req_url = url
        if attempt > 0:
            sep = "&" if "?" in url else "?"
            req_url = f"{url}{sep}cb={int(time.time() * 1000)}"
        try:
            res = subprocess.run(
                [
                    "curl",
                    "-sL",
                    "--fail",
                    "--max-time",
                    "45",
                    "-b",
                    str(cookie_jar),
                    "-e",
                    "https://batcave.biz/",
                    "-A",
                    USER_AGENT,
                    req_url,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            if len(res.stdout) > 100:
                return res.stdout
            last_err = RuntimeError(f"Empty/tiny image payload ({len(res.stdout)} bytes)")
        except Exception as e:
            last_err = e
        time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Failed to download image {url}: {last_err}")


def parse_reading_list(list_path: Path) -> list[str]:
    """Read unique batcave.biz comic URLs from the markdown reading list."""
    if not list_path.is_file():
        raise FileNotFoundError(f"Reading list not found: {list_path}")
    text = list_path.read_text(encoding="utf-8")
    urls: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"https?://(?:www\.)?batcave\.biz/[^\s)>\]\"']+", text):
        u = m.group(0).strip()
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def is_issue_downloaded(
    comic_dir: Path,
    chap_id: int,
    raw_title: str,
    downloaded_ids: set[int],
) -> Path | None:
    """Check if an issue already exists on disk (never overwrite existing files)."""
    candidates = [
        comic_dir / f"{normalize_issue_title(raw_title)}.cbz",
        comic_dir / f"{normalize_series_title(raw_title)}.cbz",
        comic_dir / f"{sanitize_filename(raw_title)}.cbz",
    ]
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path
    if chap_id in downloaded_ids:
        # Check if any matching file is still present; if user deleted the file, re-download it
        for path in candidates:
            if path.is_file():
                return path
    return None


def cbz_has_webp_or_avif(cbz_path: Path) -> bool:
    """Return True if an existing .cbz archive contains any .webp or .avif entries."""
    try:
        with zipfile.ZipFile(cbz_path, "r") as zf:
            for name in zf.namelist():
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                if ext in ("webp", "avif"):
                    return True
    except Exception:
        return False
    return False


def convert_cbz_webp_to_jpg(cbz_path: Path, workers: int = 8) -> int:
    """
    Atomically convert any .webp/.avif pages inside an existing .cbz archive to .jpg
    so e-ink readers like Supernote can open the archive without 'corrupted file' errors.
    Returns number of converted pages.
    """
    if not cbz_has_webp_or_avif(cbz_path):
        return 0

    with zipfile.ZipFile(cbz_path, "r") as zf:
        entries = [(info.filename, zf.read(info.filename)) for info in zf.infolist()]

    def _process_entry(item: tuple[str, bytes]) -> tuple[str, bytes, bool]:
        name, blob = item
        ext = detect_image_ext(blob, name)
        stem = name.rsplit(".", 1)[0] if "." in name else name
        if ext in ("webp", "avif"):
            converted = convert_to_jpg(blob)
            if converted.startswith(b"\xff\xd8\xff"):
                return f"{stem}.jpg", converted, True
        return name, blob, False

    max_workers = max(1, workers, os.cpu_count() or 4)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        processed = list(pool.map(_process_entry, entries))

    converted_count = sum(1 for _, _, changed in processed if changed)
    if converted_count == 0:
        return 0

    part_file = cbz_path.with_suffix(".cbz.repack.part")
    try:
        with zipfile.ZipFile(part_file, "w", compression=zipfile.ZIP_STORED) as zf:
            for new_name, new_blob, _ in processed:
                zf.writestr(new_name, new_blob)
        part_file.replace(cbz_path)
    finally:
        if part_file.exists():
            part_file.unlink(missing_ok=True)

    return converted_count


def download_issue_cbz(
    news_id: int,
    chap: dict,
    target_cbz: Path,
    cookie_jar: Path,
    workers: int = 4,
    recode_jpg: bool = True,
) -> int:
    """Download all pages of an issue and package them into a .cbz archive atomically."""
    if target_cbz.exists():
        return 0

    chap_id = int(chap["id"])
    reader_url = f"https://batcave.biz/reader/{news_id}/{chap_id}"
    html = fetch_html(reader_url, cookie_jar)
    data = extract_window_data(html)
    images: list[str] = data.get("images") or []
    if not images:
        raise RuntimeError(f"No page images found at {reader_url}")

    total_pages = len(images)
    width = max(2, len(str(total_pages)))
    page_items: list[tuple[str, bytes] | None] = [None] * total_pages

    def _fetch_and_prepare(idx: int, img_url: str) -> tuple[str, bytes]:
        blob = fetch_page_bytes(img_url, cookie_jar)
        ext = detect_image_ext(blob, img_url)
        if recode_jpg and ext in ("webp", "avif"):
            converted = convert_to_jpg(blob)
            if converted.startswith(b"\xff\xd8\xff"):
                blob = converted
                ext = "jpg"
        return f"{idx + 1:0{width}d}.{ext}", blob

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {
            pool.submit(_fetch_and_prepare, idx, img_url): idx
            for idx, img_url in enumerate(images)
        }
        done_count = 0
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            page_items[idx] = fut.result()
            done_count += 1
            print(
                f"\r    Downloading pages: {done_count}/{total_pages}",
                end="",
                flush=True,
            )
    print()

    # Write to a temporary .part file first, then move into place without overwriting
    part_file = target_cbz.with_suffix(".cbz.part")
    try:
        with zipfile.ZipFile(part_file, "w", compression=zipfile.ZIP_STORED) as zf:
            for item in page_items:
                assert item is not None
                page_name, blob = item
                zf.writestr(page_name, blob)

        # Double-check we never overwrite an existing file
        if target_cbz.exists():
            part_file.unlink(missing_ok=True)
            return 0
        part_file.replace(target_cbz)
    finally:
        if part_file.exists():
            part_file.unlink(missing_ok=True)

    return total_pages


def main() -> int:
    default_list = DEFAULT_READING_LIST if DEFAULT_READING_LIST.is_file() else REPO_READING_LIST
    parser = argparse.ArgumentParser(
        prog="batcave-dl",
        description="Download new comic issues from batcave.biz based on current_batcave_reading_list.md",
    )
    parser.add_argument(
        "-l",
        "--list",
        type=Path,
        default=default_list,
        help=f"Path to markdown reading list (default: {DEFAULT_READING_LIST})",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=DEFAULT_BASE_DIR,
        help=f"Directory where comic folders are created (default: {DEFAULT_BASE_DIR})",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=4,
        help="Concurrent page downloads per issue (default: 4)",
    )
    parser.add_argument(
        "--convert-jpg",
        action="store_true",
        default=True,
        help="Convert WebP/AVIF pages to JPEG inside CBZ archives (enabled by default for Supernote compatibility)",
    )
    parser.add_argument(
        "--keep-webp",
        action="store_true",
        help="Keep raw WebP/AVIF pages instead of converting to JPEG",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Check for new issues and create folders without downloading pages",
    )
    args = parser.parse_args()
    recode_jpg = not args.keep_webp

    urls = parse_reading_list(args.list)
    if not urls:
        print(f"No batcave.biz URLs found in {args.list}")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    sync_browser_cookies(COOKIE_JAR)

    print(f"Loaded {len(urls)} comic(s) from {args.list.name}\n")

    new_downloaded = 0
    repacked_existing = 0
    skipped_existing = 0
    errors = 0

    for url in urls:
        try:
            html = fetch_html(url, COOKIE_JAR)
            data = extract_window_data(html)
        except Exception as e:
            print(f"[!] Error fetching {url}: {e}")
            errors += 1
            continue

        news_id = int(data["news_id"])
        raw_series_title = data.get("title") or url.rsplit("/", 1)[-1].replace(".html", "")
        folder_name = normalize_series_title(raw_series_title)
        comic_dir = args.output_dir / folder_name
        comic_dir.mkdir(parents=True, exist_ok=True)

        state_file = comic_dir / ".batcave_state.json"
        downloaded_ids: set[int] = set()
        if state_file.is_file():
            try:
                st = json.loads(state_file.read_text(encoding="utf-8"))
                downloaded_ids = set(map(int, st.get("downloaded_chapter_ids", [])))
            except Exception:
                pass

        chapters = sorted(
            data.get("chapters", []),
            key=lambda c: (int(c.get("posi", 0)), int(c.get("id", 0))),
        )

        # Determine which chapters are already on disk vs new
        missing_chapters: list[tuple[dict, Path]] = []
        existing_cbzs: list[Path] = []
        for chap in chapters:
            chap_id = int(chap["id"])
            raw_chap_title = chap.get("title") or f"{folder_name} #{chap.get('posi', chap_id)}"
            existing = is_issue_downloaded(comic_dir, chap_id, raw_chap_title, downloaded_ids)
            if existing is not None:
                skipped_existing += 1
                downloaded_ids.add(chap_id)
                existing_cbzs.append(existing)
            else:
                target_cbz = comic_dir / f"{normalize_issue_title(raw_chap_title)}.cbz"
                missing_chapters.append((chap, target_cbz))

        print(
            f"==> {folder_name} ({len(chapters)} total, "
            f"{len(chapters) - len(missing_chapters)} on disk, {len(missing_chapters)} new)"
        )

        if recode_jpg:
            for existing_cbz in existing_cbzs:
                if cbz_has_webp_or_avif(existing_cbz):
                    if args.dry_run:
                        print(f"  [webp->jpg] {existing_cbz.name}")
                    else:
                        try:
                            conv_pages = convert_cbz_webp_to_jpg(existing_cbz, workers=args.jobs)
                            if conv_pages > 0:
                                repacked_existing += 1
                                print(f"  [webp->jpg] {existing_cbz.name} ({conv_pages} pages converted)")
                        except Exception as e:
                            print(f"  [!] Failed to convert {existing_cbz.name}: {e}")
                            errors += 1

        if args.dry_run:
            for _, target_cbz in missing_chapters:
                print(f"  [new] {target_cbz.name}")
            continue

        for chap, target_cbz in missing_chapters:
            chap_id = int(chap["id"])
            print(f"  -> {target_cbz.name}")
            try:
                pages = download_issue_cbz(
                    news_id=news_id,
                    chap=chap,
                    target_cbz=target_cbz,
                    cookie_jar=COOKIE_JAR,
                    workers=args.jobs,
                    recode_jpg=recode_jpg,
                )
                if pages > 0:
                    new_downloaded += 1
                    downloaded_ids.add(chap_id)
                    state_file.write_text(
                        json.dumps(
                            {
                                "news_id": news_id,
                                "url": url,
                                "title": folder_name,
                                "downloaded_chapter_ids": sorted(downloaded_ids),
                            },
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
            except Exception as e:
                print(f"  [!] Failed to download {target_cbz.name}: {e}")
                errors += 1

    print(
        f"\nDone! Downloaded: {new_downloaded} new issue(s), "
        f"Converted existing WebP->JPG: {repacked_existing}, "
        f"Skipped (already downloaded): {skipped_existing}, Errors: {errors}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

# batcave-dl

A lightweight Python CLI tool that scrapes comic series from [batcave.biz](https://batcave.biz) based on a Markdown reading list, automatically organizes each series into its own folder, and downloads new issues as `.cbz` archives without overwriting existing files.

## Features

- **Reading List Driven**: Reads comic URLs directly from `current_batcave_reading_list.md`.
- **Incremental Sync**: Tracks downloaded chapters via `.batcave_state.json` and checks existing `.cbz` files on disk so it only downloads new issues and never overwrites existing files.
- **Anti-Bot & Auth Handling**:
  - Automatically solves BatCave's SHA-256 Proof-of-Work (`/_v`) challenge.
  - Automatically syncs your `batcave.biz` login session cookies from **qutebrowser** or **Firefox** (required for chapters gated behind *"Sign in to keep reading"*).
- **Atomic `.cbz` Packaging**: Downloads pages concurrently into a temporary `.cbz.part` archive before moving into place, ensuring interrupted runs never leave corrupted `.cbz` files.
- **Clean Naming**: Normalizes ongoing year ranges (e.g., `(2024-)` -> `(2024)`) and zero-pads single-digit issue numbers (`#1` -> `#01`) so comic readers sort issues properly.

---

## Prerequisites

Make sure the following are installed on your system (`batcave-dl` uses only the Python standard library—no `pip` packages required):

- **Python 3.9+**
- **curl**
- **qutebrowser** or **Firefox** (logged into your [batcave.biz](https://batcave.biz) account so the script can read your session cookies)
- **ImageMagick** *(optional)*: Only needed if you use the `--convert-jpg` flag to transcode WebP/AVIF pages to JPEG.

### Install Prerequisites

- **Arch Linux:**
  ```bash
  sudo pacman -S python curl imagemagick
  ```
- **Debian / Ubuntu:**
  ```bash
  sudo apt install python3 curl imagemagick
  ```

---

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/keahiakio/batcave-dl.git ~/batcave-dl
   cd ~/batcave-dl
   ```

2. **Make the script executable:**
   ```bash
   chmod +x ~/batcave-dl/batcave_dl.py
   ```

3. **Create a symlink in `~/.local/bin` for global execution:**
   ```bash
   mkdir -p ~/.local/bin
   ln -sf ~/batcave-dl/batcave_dl.py ~/.local/bin/batcave-dl
   ```

4. **Verify `~/.local/bin` is in your `PATH`:**
   Check with `echo $PATH`. If `~/.local/bin` is not listed, add this line to your `~/.bashrc` or `~/.zshrc`:
   ```bash
   export PATH="$HOME/.local/bin:$PATH"
   ```

5. **Set up your Comics directory and reading list (optional):**
   By default, `batcave-dl` saves comics to `~/Downloads/Comics` and looks for `~/Downloads/Comics/current_batcave_reading_list.md` (falling back to the `current_batcave_reading_list.md` inside this repo if not found):
   ```bash
   mkdir -p ~/Downloads/Comics
   cp ~/batcave-dl/current_batcave_reading_list.md ~/Downloads/Comics/
   ```

6. **Sign in to BatCave.biz once in your browser:**
   Open `https://batcave.biz` in **qutebrowser** or **Firefox** and log in. `batcave-dl` will automatically extract and cache your session cookies in `~/.cache/batcave-dl/cookies.txt`.

---

## Usage

Run `batcave-dl` from anywhere in your terminal:

```bash
batcave-dl
```

### Command-Line Options

```text
usage: batcave-dl [-h] [-l LIST] [-o OUTPUT_DIR] [-j JOBS] [--convert-jpg] [-n]

options:
  -h, --help            show this help message and exit
  -l, --list LIST       Path to markdown reading list
                        (default: ~/Downloads/Comics/current_batcave_reading_list.md)
  -o, --output-dir DIR  Directory where comic folders are created
                        (default: ~/Downloads/Comics)
  -j, --jobs JOBS       Concurrent page downloads per issue (default: 4)
  --convert-jpg         Convert WebP/AVIF pages to JPEG inside the CBZ archive
  -n, --dry-run         Check for new issues and create folders without downloading pages
```

### Examples

- **Check for new issues without downloading anything (dry run):**
  ```bash
  batcave-dl --dry-run
  ```

- **Download all new issues using 6 concurrent page workers:**
  ```bash
  batcave-dl -j 6
  ```

- **Use a custom reading list and output directory:**
  ```bash
  batcave-dl -l ~/Documents/my_comics.md -o ~/Comics
  ```

- **Convert WebP pages to JPEG inside the `.cbz` archive (for older CBZ readers):**
  ```bash
  batcave-dl --convert-jpg
  ```

---

## Reading List Format

Add any `batcave.biz` series URLs to `current_batcave_reading_list.md`. Markdown headings, bullet points, and notes are supported:

```markdown
#### DC Comics
- https://batcave.biz/33051-absolute-batman-2024.html
- https://batcave.biz/33086-absolute-superman-2024.html
- https://batcave.biz/33091-absolute-wonder-woman-2024.html

#### Marvel Comics
- https://batcave.biz/38792-midnight-spider-man-2026.html
```

# Contributing to batcave-dl

Thank you for considering contributing to `batcave-dl`! We welcome bug reports, feature suggestions, documentation updates, and pull requests.

---

## Code of Conduct

This project adheres to the Contributor Covenant [Code of Conduct](CODE_OF_CONDUCT.md). By contributing, you agree to uphold this standard.

---

## Design Principles

When submitting code changes, please keep the following design constraints in mind:

1. **Zero External Pip Dependencies**: `batcave-dl` uses only the Python standard library. Do not add requirements for third-party PyPI packages.
2. **Graceful Fallbacks**: Features requiring external libraries (like `libwebp` or `libturbojpeg`) should detect availability via `ctypes` and fall back gracefully to CLI tools (`imagemagick`, `graphicsmagick`, or `ffmpeg`) or provide sensible default behavior.
3. **Data Safety**: File operations must remain atomic (e.g. staging `.cbz.part` before moving to destination) so interruptions never leave corrupted files on disk.

---

## Development & Local Testing

### Prerequisites
- Python 3.9+
- Git
- `curl`

### Setup Instructions
1. Fork and clone the repository:
   ```bash
   git clone https://github.com/<your-username>/batcave-dl.git
   cd batcave-dl
   ```
2. Verify syntax and compilation:
   ```bash
   python3 -m py_compile batcave_dl.py
   ```
3. Test locally with a test reading list:
   ```bash
   python3 batcave_dl.py --help
   ```

---

## Coding Standards

- Follow [PEP 8](https://peps.python.org/pep-0008/) style conventions.
- Keep function responsibilities focused and handle network/cookie errors informatively.
- Write clear commit messages in [Conventional Commits](https://www.conventionalcommits.org/) format (e.g., `feat:`, `fix:`, `docs:`, `perf:`).

---

## Pull Request Process

1. Create a descriptive feature branch:
   ```bash
   git checkout -b feature/my-improvement
   ```
2. Make your changes and commit them cleanly.
3. Verify syntax and test functionality:
   ```bash
   python3 -m py_compile batcave_dl.py
   ```
4. Push to your fork and submit a Pull Request to `keahiakio/batcave-dl:main`.
5. Complete the PR template checklist to facilitate review.

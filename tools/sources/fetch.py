"""Cached downloads for map source data.

Source datasets are large and slow to fetch — a state census mesh is tens of megabytes, a
national address register hundreds. A map build re-runs many times while you tune it, so
every fetch is cached on disk and skipped when the cached copy already looks right.

Nothing here is Brazil-specific; see `docs/09-brazil-data-sources.md` for what to point it at.
Standard library only, deliberately: no requests, no geopandas, no GDAL.
"""

from __future__ import annotations

import ftplib
import hashlib
import os
import shutil
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

__all__ = ["fetch", "ftp_list", "cache_dir", "ssl_context", "CacheError"]

USER_AGENT = "SubwayBuilder-MapTools/1.0 (+https://github.com/Subway-Builder-Modded)"
CHUNK = 1 << 20
_TIMEOUT = 120

_SYSTEM_BUNDLES = (
    "/opt/homebrew/etc/ca-certificates/cert.pem",
    "/usr/local/etc/ca-certificates/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
)

_context: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """A verifying TLS context that actually works on the host it is running on.

    Python installs managed by pyenv, mise, asdf and similar do not always resolve to a CA
    bundle that covers current roots, and a statistical agency chaining to a newer root then
    fails verification even though `curl` on the same machine succeeds. Rather than turn
    verification off — which would silently accept anything — try the bundles most likely to be
    complete, in order, and keep the first that loads certificates: `SSL_CERT_FILE` if the
    caller set one, then `certifi` if installed, then the platform bundles, then whatever
    OpenSSL defaults to.

    Verification is never disabled. If none of the candidates work the default context is
    returned and the caller sees a normal certificate error.
    """
    global _context
    if _context is not None:
        return _context

    candidates: list[str | None] = []
    if os.environ.get("SSL_CERT_FILE"):
        candidates.append(os.environ["SSL_CERT_FILE"])
    try:
        import certifi

        candidates.append(certifi.where())
    except ImportError:
        pass
    candidates.extend(_SYSTEM_BUNDLES)
    candidates.append(None)  # OpenSSL defaults

    for context in _ssl_candidates():
        _context = context
        return _context

    _context = ssl.create_default_context()
    return _context


def _ssl_candidates() -> list[ssl.SSLContext]:
    """Every verifying context worth trying, best first.

    A bundle can load hundreds of certificates and still miss the one root a particular host
    chains to, so the caller retries down this list on a verification failure rather than
    trusting the first entry.
    """
    bundles: list[str | None] = []
    if os.environ.get("SSL_CERT_FILE"):
        bundles.append(os.environ["SSL_CERT_FILE"])
    try:
        import certifi

        bundles.append(certifi.where())
    except ImportError:
        pass
    bundles.extend(path for path in _SYSTEM_BUNDLES if os.path.exists(path))
    bundles.append(None)

    contexts = []
    for bundle in bundles:
        try:
            context = (
                ssl.create_default_context(cafile=bundle)
                if bundle
                else ssl.create_default_context()
            )
        except (OSError, ssl.SSLError):
            continue
        if context.get_ca_certs():
            contexts.append(context)
    return contexts


class CacheError(RuntimeError):
    """A download completed but failed its integrity check."""


def cache_dir() -> Path:
    """Root for cached downloads. Override with `SBMAP_CACHE`."""
    root = os.environ.get("SBMAP_CACHE")
    base = Path(root) if root else Path.home() / ".cache" / "subway-builder-maps"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _looks_complete(path: Path, expect_bytes: int | None, sha256: str | None) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    if expect_bytes is not None and path.stat().st_size != expect_bytes:
        return False
    if sha256 is not None and _sha256(path) != sha256.lower():
        return False
    return True


def _report(done: int, total: int | None, label: str) -> None:
    if not sys.stderr.isatty():
        return
    if total:
        pct = done / total * 100
        sys.stderr.write(f"\r  {label}: {done / 1e6:,.1f} / {total / 1e6:,.1f} MB ({pct:4.1f}%)")
    else:
        sys.stderr.write(f"\r  {label}: {done / 1e6:,.1f} MB")
    sys.stderr.flush()


def fetch(
    url: str,
    dest: str | Path | None = None,
    *,
    expect_bytes: int | None = None,
    sha256: str | None = None,
    refresh: bool = False,
    quiet: bool = False,
) -> Path:
    """Download `url` unless a good copy is already cached, and return the local path.

    `dest` may be a file path or a directory; omit it to place the file under `cache_dir()`
    keyed by host and URL path. `expect_bytes` and `sha256` are verified after the download
    and, when supplied, also used to decide whether an existing file can be trusted — without
    either, any non-empty cached file is accepted.

    Set `refresh` to force a re-download. Raises `CacheError` if the fetched bytes fail a
    check, leaving the partial file at `<dest>.part` for inspection.

    Supports `http(s)://` and `ftp://`.
    """
    parsed = urllib.parse.urlparse(url)
    name = Path(urllib.parse.unquote(parsed.path)).name or "download"

    if dest is None:
        target = cache_dir() / parsed.netloc / urllib.parse.unquote(parsed.path).lstrip("/")
    else:
        target = Path(dest)
        if target.is_dir() or str(dest).endswith(os.sep):
            target = target / name
    target.parent.mkdir(parents=True, exist_ok=True)

    if not refresh and _looks_complete(target, expect_bytes, sha256):
        if not quiet:
            print(f"  cached: {target.name} ({target.stat().st_size / 1e6:,.1f} MB)")
        return target

    partial = target.with_name(target.name + ".part")
    if not quiet:
        print(f"  fetching: {url}")

    if parsed.scheme == "ftp":
        total = _download_ftp(parsed, partial, name, quiet)
    else:
        total = _download_http(url, partial, name, quiet)

    if not quiet and sys.stderr.isatty():
        sys.stderr.write("\n")

    if expect_bytes is not None and partial.stat().st_size != expect_bytes:
        raise CacheError(
            f"{url} gave {partial.stat().st_size:,} bytes, expected {expect_bytes:,} "
            f"(partial left at {partial})"
        )
    if sha256 is not None:
        got = _sha256(partial)
        if got != sha256.lower():
            raise CacheError(f"{url} sha256 {got} != expected {sha256} (partial left at {partial})")

    partial.replace(target)
    if not quiet:
        size = target.stat().st_size
        suffix = "" if total is None or total == size else f" (server declared {total:,})"
        print(f"  saved: {target} ({size / 1e6:,.1f} MB){suffix}")
    return target


def _download_http(url: str, partial: Path, label: str, quiet: bool) -> int | None:
    global _context
    contexts = _ssl_candidates() or [ssl.create_default_context()]
    if _context is not None and _context in contexts:  # keep a known-good choice in front
        contexts.insert(0, contexts.pop(contexts.index(_context)))

    last_error: Exception | None = None
    for index, context in enumerate(contexts):
        try:
            result = _download_http_once(url, partial, label, quiet, context)
            _context = context  # remember what worked for the rest of the run
            return result
        except urllib.error.URLError as error:
            if not isinstance(error.reason, ssl.SSLCertVerificationError):
                raise
            last_error = error
            if not quiet and index == 0:
                print("    TLS verification failed; trying another CA bundle")
    raise last_error if last_error else RuntimeError(f"could not download {url}")


def _download_http_once(
    url: str, partial: Path, label: str, quiet: bool, context: ssl.SSLContext
) -> int | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=_TIMEOUT, context=context) as response:
        declared = response.headers.get("Content-Length")
        total = int(declared) if declared and declared.isdigit() else None
        done = 0
        with partial.open("wb") as handle:
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                handle.write(block)
                done += len(block)
                if not quiet:
                    _report(done, total, label)
    return total


def _download_ftp(parsed: urllib.parse.ParseResult, partial: Path, label: str, quiet: bool) -> int | None:
    path = urllib.parse.unquote(parsed.path)
    ftp = ftplib.FTP(parsed.hostname, timeout=_TIMEOUT)
    try:
        ftp.login(parsed.username or "anonymous", parsed.password or "anonymous@")
        ftp.voidcmd("TYPE I")
        try:
            total = ftp.size(path)
        except ftplib.all_errors:
            total = None
        done = 0
        with partial.open("wb") as handle:

            def write(block: bytes) -> None:
                nonlocal done
                handle.write(block)
                done += len(block)
                if not quiet:
                    _report(done, total, label)

            ftp.retrbinary(f"RETR {path}", write, blocksize=CHUNK)
    finally:
        try:
            ftp.quit()
        except ftplib.all_errors:
            ftp.close()
    return total


def ftp_list(url: str) -> list[str]:
    """List the names in an FTP directory.

    Worth having as its own function because some statistical agencies — IBGE among them —
    serve every file happily over HTTPS but answer an HTTPS *directory* request with a portal
    page containing no file list. Download over HTTPS, enumerate over FTP.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "ftp":
        raise ValueError(f"ftp_list needs an ftp:// URL, got {parsed.scheme!r}")
    ftp = ftplib.FTP(parsed.hostname, timeout=_TIMEOUT)
    try:
        ftp.login(parsed.username or "anonymous", parsed.password or "anonymous@")
        return ftp.nlst(urllib.parse.unquote(parsed.path) or "/")
    finally:
        try:
            ftp.quit()
        except ftplib.all_errors:
            ftp.close()


def unpack(archive: str | Path, dest: str | Path | None = None, *, quiet: bool = False) -> Path:
    """Extract an archive next to itself (or into `dest`) and return the directory.

    Skips extraction when the destination already holds files, so it is safe to call on every
    run of a build script.
    """
    archive = Path(archive)
    out = Path(dest) if dest else archive.with_suffix("")
    if out.exists() and any(out.iterdir()):
        if not quiet:
            print(f"  already unpacked: {out}")
        return out
    out.mkdir(parents=True, exist_ok=True)
    shutil.unpack_archive(str(archive), str(out))
    if not quiet:
        print(f"  unpacked: {archive.name} -> {out}")
    return out

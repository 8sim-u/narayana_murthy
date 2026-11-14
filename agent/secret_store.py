"""Windows-only protected storage for WAMSS workstation credentials.

Secrets are encrypted with machine-scope DPAPI and stored in separate files.
The full-agent installer supplies a restrictive user/service ACL; the packaged
tray monitor restricts its per-user store to the current Windows user.
"""
from __future__ import annotations

import argparse
import ctypes
import getpass
import os
import subprocess
import sys
import warnings
from ctypes import wintypes
from pathlib import Path
from typing import Mapping, Optional

CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4
TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1

SECRET_FILES = {
    "WAMSS_EMPLOYEE_TOKEN": "employee-token.dpapi",
}
SENSITIVE_ENV_NAMES = ("WAMSS_EMPLOYEE_TOKEN", "WAMSS_API_KEY")


class SecretStoreError(RuntimeError):
    """A sanitized secret-store failure; error text never includes a secret."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


def _require_windows() -> None:
    if os.name != "nt":
        raise SecretStoreError("Protected workstation credentials are supported only on Windows.")


def _assert_no_reparse_path(path: Path) -> None:
    """Reject symlinks/junctions in every existing component of a protected path."""
    try:
        absolute = Path(os.path.abspath(str(path)))
        current = Path(absolute.anchor)
        for component in absolute.parts[1:]:
            current = current / component
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                break
            if getattr(info, "st_file_attributes", 0) & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
                raise SecretStoreError("Credential paths must not contain Windows reparse points.")
    except SecretStoreError:
        raise
    except OSError:
        raise SecretStoreError("Windows could not safely inspect a credential path.") from None


def _input_blob(data: bytes):
    backing = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(backing, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, backing


def _local_free(pointer) -> None:
    if pointer:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(pointer, ctypes.c_void_p))


def _protect(data: bytes, name: str) -> bytes:
    _require_windows()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    incoming, keepalive = _input_blob(data)
    outgoing = _DataBlob()
    description = f"WAMSS protected secret: {name}"
    ok = crypt32.CryptProtectData(
        ctypes.byref(incoming), description, None, None, None,
        CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(outgoing),
    )
    if not ok:
        raise SecretStoreError("Windows DPAPI could not protect a credential.")
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        _local_free(outgoing.pbData)
        del keepalive


def _unprotect(data: bytes, name: str) -> bytes:
    _require_windows()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    incoming, keepalive = _input_blob(data)
    outgoing = _DataBlob()
    description = wintypes.LPWSTR()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(incoming), ctypes.byref(description), None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(outgoing),
    )
    if not ok:
        raise SecretStoreError("Windows DPAPI could not unlock a protected credential.")
    try:
        if not description.value or description.value != f"WAMSS protected secret: {name}":
            raise SecretStoreError("Protected credential metadata is invalid.")
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        _local_free(outgoing.pbData)
        _local_free(description)
        del keepalive


def _write_protected_values(secret_dir: Path, values: Mapping[str, str]) -> None:
    """Write only encrypted bytes; callers are responsible for the directory ACL."""
    _require_windows()
    employee_token = values.get("WAMSS_EMPLOYEE_TOKEN", "")
    if not isinstance(employee_token, str) or not employee_token or not employee_token.strip():
        raise SecretStoreError("A non-empty employee-scoped token is required.")

    try:
        _assert_no_reparse_path(secret_dir)
        secret_dir.mkdir(parents=True, exist_ok=True)
        _assert_no_reparse_path(secret_dir)
        for name, filename in SECRET_FILES.items():
            value = values.get(name, "")
            target = secret_dir / filename
            _assert_no_reparse_path(target)
            if name == "WAMSS_EMPLOYEE_TOKEN" and (not isinstance(value, str) or not value or not value.strip()):
                raise SecretStoreError("A non-empty employee-scoped token is required.")
            if not value:
                target.unlink(missing_ok=True)
                continue
            encrypted = _protect(value.encode("utf-8"), name)
            temporary = secret_dir / f".{filename}.{os.getpid()}.tmp"
            _assert_no_reparse_path(temporary)
            try:
                with temporary.open("wb") as stream:
                    stream.write(encrypted)
                    stream.flush()
                    os.fsync(stream.fileno())
                # The temporary and final files contain ciphertext only. Removing
                # first also permits rotation when the old file is read-only.
                target.unlink(missing_ok=True)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    except SecretStoreError:
        raise
    except OSError:
        raise SecretStoreError("Protected credential files could not be written.") from None


def write_protected_values(secret_dir: Path | str, values: Mapping[str, str]) -> None:
    """Public write entry point for callers that provide their own masked UI."""
    _write_protected_values(Path(secret_dir), values)


def load_into_environment(secret_dir: Path | str, *, required: bool = True) -> bool:
    """Load protected secrets before importing modules that capture env values."""
    _require_windows()
    # Never inherit a deployment-wide key from the launching administrator,
    # machine environment, or a legacy plaintext config file.
    for name in SENSITIVE_ENV_NAMES:
        os.environ.pop(name, None)
    root = Path(secret_dir)
    _assert_no_reparse_path(root)
    loaded: dict[str, str] = {}
    try:
        for name, filename in SECRET_FILES.items():
            _assert_no_reparse_path(root / filename)
            try:
                protected = (root / filename).read_bytes()
            except FileNotFoundError:
                continue
            if not protected:
                raise SecretStoreError("A protected credential file is empty.")
            raw = _unprotect(protected, name)
            try:
                value = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise SecretStoreError("A protected credential has invalid encoding.") from None
            if not value:
                raise SecretStoreError("A protected credential is empty.")
            loaded[name] = value
    except SecretStoreError:
        for name in SENSITIVE_ENV_NAMES:
            os.environ.pop(name, None)
        raise
    except OSError:
        for name in SENSITIVE_ENV_NAMES:
            os.environ.pop(name, None)
        raise SecretStoreError("Protected credentials could not be read.") from None

    if required and not loaded.get("WAMSS_EMPLOYEE_TOKEN"):
        raise SecretStoreError("The protected employee token is missing.")
    os.environ.update(loaded)
    return bool(loaded.get("WAMSS_EMPLOYEE_TOKEN"))


def current_user_sid() -> str:
    """Return the current process token's SID without trusting username env vars."""
    _require_windows()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        raise SecretStoreError("The current Windows account could not be identified.")
    sid_text = wintypes.LPWSTR()
    try:
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, None, 0, ctypes.byref(required))
        if not required.value:
            raise SecretStoreError("The current Windows account could not be identified.")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token, TOKEN_USER_CLASS, buffer, required.value, ctypes.byref(required)
        ):
            raise SecretStoreError("The current Windows account could not be identified.")
        token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        if not advapi32.ConvertSidToStringSidW(token_user.User.Sid, ctypes.byref(sid_text)):
            raise SecretStoreError("The current Windows account could not be identified.")
        return sid_text.value
    finally:
        if token:
            kernel32.CloseHandle(token)
        _local_free(sid_text)


def _run_icacls(path: Path, *arguments: str) -> None:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    executable = Path(system_root) / "System32" / "icacls.exe"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [str(executable), str(path), *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False, creationflags=flags,
        )
    except OSError:
        raise SecretStoreError("Windows could not apply the credential-store ACL.") from None
    if result.returncode != 0:
        raise SecretStoreError("Windows could not apply the credential-store ACL.")


def restrict_store_to_current_user(secret_dir: Path | str) -> Path:
    """Protect a packaged-monitor store to the current user; secret files are read-only."""
    _require_windows()
    root = Path(secret_dir)
    try:
        _assert_no_reparse_path(root)
        root.mkdir(parents=True, exist_ok=True)
        _assert_no_reparse_path(root)
    except OSError:
        raise SecretStoreError("The per-user credential directory could not be created.") from None
    except SecretStoreError:
        raise
    sid = current_user_sid()
    _run_icacls(root, "/reset")
    _run_icacls(root, "/inheritance:r")
    _run_icacls(root, "/grant:r", f"*{sid}:(OI)(CI)(M)")
    for filename in SECRET_FILES.values():
        path = root / filename
        _assert_no_reparse_path(path)
        if path.exists():
            _run_icacls(path, "/reset")
            _run_icacls(path, "/inheritance:r")
            _run_icacls(path, "/grant:r", f"*{sid}:(R)")
    return root


def provision_interactively(secret_dir: Path | str) -> None:
    """Masked console provisioning used by the elevated full-agent installer."""
    _require_windows()
    if not sys.stdin.isatty():
        raise SecretStoreError("Credential provisioning requires an interactive, non-echoing console.")
    def hidden_input(prompt: str) -> str:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                return getpass.getpass(prompt)
            except getpass.GetPassWarning:
                raise SecretStoreError("The console cannot securely hide credential input.") from None

    employee = hidden_input("Employee's personal token (required; input hidden): ")
    confirmation = hidden_input("Confirm employee token (input hidden): ")
    if not employee or not employee.strip() or employee != confirmation:
        raise SecretStoreError("The employee token is empty or the confirmations do not match.")
    _write_protected_values(Path(secret_dir), {
        "WAMSS_EMPLOYEE_TOKEN": employee,
    })
    print("Protected credentials saved; no credential value was displayed or printed.")


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Provision the employee's Windows-protected token without command-line exposure.")
    parser.add_argument("--secret-dir", required=True, help="Nonsecret directory path for DPAPI-protected credentials")
    args = parser.parse_args(argv)
    try:
        provision_interactively(args.secret_dir)
        return 0
    except SecretStoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Credential provisioning cancelled.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())

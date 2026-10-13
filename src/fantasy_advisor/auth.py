"""Local OAuth PKCE login and OS credential-store integration."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

from . import config


class AuthError(RuntimeError):
    pass


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise AuthError("Falta keyring. Instala el proyecto con: python -m pip install -e .") from exc
    backend = keyring.get_keyring()
    module = backend.__class__.__module__
    if getattr(backend, "priority", 0) <= 0 or any(
        name in module.lower() for name in ("fail", "null", "plaintext", "file")
    ):
        raise AuthError(
            "No hay un almacén seguro del sistema disponible. No guardaré tokens en texto plano."
        )
    if os.name == "nt" and "windows" not in module.lower():
        raise AuthError("En Windows se requiere Windows Credential Manager para guardar el token.")
    return keyring


_TOKEN_USERNAME = "oauth-tokens"
_TOKEN_MANIFEST_SERVICE = f"{config.KEYRING_SERVICE}.oauth-manifest"
_TOKEN_CHUNK_SERVICE_PREFIX = f"{config.KEYRING_SERVICE}.oauth"
# Credential Manager's generic blob limit is 2560 bytes. keyring's Windows
# backend stores strings as UTF-16, so these ASCII chunks stay comfortably below it.
_TOKEN_CHUNK_CHARS = 900
_TOKEN_MAX_CHUNKS = 256


def save_tokens(tokens: dict) -> None:
    if not isinstance(tokens, dict) or not (tokens.get("access_token") or tokens.get("id_token")):
        raise AuthError("La respuesta OAuth no contiene credenciales que se puedan guardar.")
    serialized = json.dumps(tokens, separators=(",", ":"), ensure_ascii=True)
    chunks = [serialized[i:i + _TOKEN_CHUNK_CHARS]
              for i in range(0, len(serialized), _TOKEN_CHUNK_CHARS)]
    if not chunks or len(chunks) > _TOKEN_MAX_CHUNKS:
        raise AuthError("La respuesta OAuth es demasiado grande para el almacén local.")

    keyring = _keyring()
    old_manifest = _read_manifest(keyring)
    generation = secrets.token_hex(16)
    chunk_services = [_token_chunk_service(generation, index) for index in range(len(chunks))]
    try:
        for service, chunk in zip(chunk_services, chunks):
            keyring.set_password(service, _TOKEN_USERNAME, chunk)
        manifest = json.dumps({
            "version": 1,
            "generation": generation,
            "count": len(chunks),
            "sha256": hashlib.sha256(serialized.encode("ascii")).hexdigest(),
        }, separators=(",", ":"))
        # Publish last so readers never observe a partially written token set.
        keyring.set_password(_TOKEN_MANIFEST_SERVICE, _TOKEN_USERNAME, manifest)
    except Exception as exc:
        for service in chunk_services:
            _delete_if_present(keyring, service, suppress_errors=True)
        raise AuthError(
            "Windows Credential Manager no pudo guardar los tokens. "
            "No se escribió ningún archivo de credenciales."
        ) from exc

    if old_manifest:
        _delete_generation(keyring, old_manifest, suppress_errors=True)
    # Migrate away from the original single-credential format, if present.
    _delete_if_present(keyring, config.KEYRING_SERVICE, suppress_errors=True)


def load_tokens() -> dict:
    keyring = _keyring()
    manifest = _read_manifest(keyring)
    if manifest:
        chunks = []
        for index in range(manifest["count"]):
            chunk = keyring.get_password(
                _token_chunk_service(manifest["generation"], index), _TOKEN_USERNAME
            )
            if chunk is None:
                raise AuthError("Falta un fragmento de la sesión guardada; vuelve a conectar la cuenta.")
            chunks.append(chunk)
        serialized = "".join(chunks)
        digest = hashlib.sha256(serialized.encode("ascii")).hexdigest()
        if not secrets.compare_digest(digest, manifest["sha256"]):
            raise AuthError("La sesión guardada está incompleta; vuelve a conectar la cuenta.")
        try:
            tokens = json.loads(serialized)
        except json.JSONDecodeError as exc:
            raise AuthError("La sesión guardada no se pudo leer; vuelve a conectar la cuenta.") from exc
        if not isinstance(tokens, dict) or not (tokens.get("access_token") or tokens.get("id_token")):
            raise AuthError("La sesión guardada no contiene un token utilizable.")
        return tokens

    # Read credentials from the original single-entry format for migration.
    raw = keyring.get_password(config.KEYRING_SERVICE, _TOKEN_USERNAME)
    if not raw:
        raise AuthError("No hay una cuenta conectada. Ejecuta `fantasy-advisor connect`.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthError("El token guardado no se pudo leer; vuelve a conectar la cuenta.") from exc


def delete_tokens() -> None:
    keyring = _keyring()
    manifest = _read_manifest(keyring)
    if manifest:
        _delete_generation(keyring, manifest)
    _delete_if_present(keyring, _TOKEN_MANIFEST_SERVICE)
    _delete_if_present(keyring, config.KEYRING_SERVICE)


def _token_chunk_service(generation: str, index: int) -> str:
    return f"{_TOKEN_CHUNK_SERVICE_PREFIX}.{generation}.{index:04d}"


def _read_manifest(keyring) -> dict | None:
    raw = keyring.get_password(_TOKEN_MANIFEST_SERVICE, _TOKEN_USERNAME)
    if raw is None:
        return None
    try:
        manifest = json.loads(raw)
        generation = manifest["generation"]
        count = manifest["count"]
        digest = manifest["sha256"]
        if (manifest.get("version") != 1 or not isinstance(generation, str)
                or len(generation) != 32 or not generation.isalnum()
                or not isinstance(count, int) or not 1 <= count <= _TOKEN_MAX_CHUNKS
                or not isinstance(digest, str) or len(digest) != 64):
            raise ValueError
        return manifest
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AuthError("El índice de la sesión guardada está dañado; vuelve a conectar la cuenta.") from exc


def _delete_generation(keyring, manifest: dict, suppress_errors: bool = False) -> None:
    for index in range(manifest["count"]):
        _delete_if_present(
            keyring,
            _token_chunk_service(manifest["generation"], index),
            suppress_errors=suppress_errors,
        )


def _delete_if_present(keyring, service: str, suppress_errors: bool = False) -> None:
    try:
        if keyring.get_password(service, _TOKEN_USERNAME) is not None:
            keyring.delete_password(service, _TOKEN_USERNAME)
    except Exception:
        if not suppress_errors:
            raise


def make_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge.rstrip(b"=").decode("ascii")


def authorization_url(challenge: str, state: str) -> str:
    params = {
        "p": config.LALIGA_SIGNIN_POLICY,
        "client_id": config.LALIGA_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.LALIGA_REDIRECT_URI,
        "scope": config.LALIGA_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "nonce": state,
    }
    return f"{config.LALIGA_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _post_token(payload: dict) -> dict:
    url = f"{config.LALIGA_TOKEN_URL}?p={urllib.parse.quote(config.LALIGA_SIGNIN_POLICY)}"
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do not echo response bodies: auth servers can include sensitive diagnostics.
        raise AuthError(f"LALIGA rechazó la autenticación (HTTP {exc.code}).") from None
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise AuthError("No se pudo completar la autenticación con LALIGA.") from exc
    if not isinstance(result, dict) or not (result.get("access_token") or result.get("id_token")):
        raise AuthError("La respuesta de autenticación no contiene un token utilizable.")
    return result


def redeem_authorization_code(callback_url: str, expected_state: str, verifier: str) -> dict:
    parsed = urllib.parse.urlsplit(callback_url.strip())
    if parsed.scheme != "authredirect" or parsed.netloc != "com.lfp.laligafantasy":
        raise AuthError("Pega la URL completa de retorno authredirect://com.lfp.laligafantasy?... .")
    params = urllib.parse.parse_qs(parsed.query)
    if params.get("error"):
        raise AuthError("LALIGA/Apple canceló o rechazó el inicio de sesión.")
    code = (params.get("code") or [""])[0]
    state = (params.get("state") or [""])[0]
    if not code:
        raise AuthError("La URL de retorno no contiene el código de autorización.")
    if not state or not secrets.compare_digest(state, expected_state):
        raise AuthError("El estado OAuth no coincide; inicia el proceso de nuevo.")
    tokens = _post_token({
        "grant_type": "authorization_code",
        "client_id": config.LALIGA_CLIENT_ID,
        "code": code,
        "redirect_uri": config.LALIGA_REDIRECT_URI,
        "code_verifier": verifier,
        "scope": config.LALIGA_SCOPE,
    })
    save_tokens(tokens)
    return tokens


def refresh_tokens(tokens: dict) -> dict:
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise AuthError("La sesión venció. Ejecuta `fantasy-advisor connect` de nuevo.")
    refreshed = _post_token({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": config.LALIGA_CLIENT_ID,
        "scope": config.LALIGA_SCOPE,
    })
    refreshed.setdefault("refresh_token", refresh_token)
    save_tokens(refreshed)
    return refreshed

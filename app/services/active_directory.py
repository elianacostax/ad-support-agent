import json
import os
import subprocess

from app.config import settings


def _validate_username(username: str) -> str:
    username = username.strip()

    if not username:
        raise ValueError("El usuario de red es obligatorio.")

    if not username.replace("-", "").replace("_", "").isalnum():
        raise ValueError("El formato del usuario no es válido.")

    return username


def get_user(username: str) -> dict | None:
    username = _validate_username(username)

    script = r"""
$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory -ErrorAction Stop

$user = Get-ADUser `
    -Identity $env:AD_TARGET_USER `
    -Server $env:AD_SERVER `
    -Properties Enabled, LockedOut, PasswordExpired, PasswordLastSet

if ($null -eq $user) {
    exit 2
}

$result = @{
    username = $user.SamAccountName
    enabled = $user.Enabled
    locked_out = $user.LockedOut
    password_expired = $user.PasswordExpired
    password_last_set = if ($user.PasswordLastSet) {
        $user.PasswordLastSet.ToString('o')
    } else {
        $null
    }
}

$result | ConvertTo-Json -Compress
"""

    env = os.environ.copy()
    env["AD_TARGET_USER"] = username
    env["AD_SERVER"] = settings.ad_server

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        check=False,
    )

    if result.returncode != 0:
        error = result.stderr.strip()

        if "Cannot find an object" in error:
            return None

        raise RuntimeError(
            "No fue posible consultar Active Directory."
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Respuesta inesperada de Active Directory."
        ) from exc


# Contraseña temporal fija (decisión operativa 2026-10-09).
# Sustituyó a la generación aleatoria de 16 caracteres con `secrets`
# para simplificar su comunicación al usuario. El riesgo de un valor
# compartido se mitiga con el cambio obligatorio al iniciar sesión
# (ChangePasswordAtLogon) — ver AUDITORIA.md.
TEMP_PASSWORD = "Temporal$2026*"


def reset_user_password(username: str) -> dict:
    username = _validate_username(username)

    temp_password = TEMP_PASSWORD

    script = r"""
$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory -ErrorAction Stop

$user = Get-ADUser `
    -Identity $env:AD_TARGET_USER `
    -Server $env:AD_SERVER `
    -Properties Enabled

if ($null -eq $user) {
    Write-Error "USER_NOT_FOUND"
    exit 2
}

if (-not $user.Enabled) {
    @{
        username = $user.SamAccountName
        reset = $false
        reason = "ACCOUNT_DISABLED"
    } | ConvertTo-Json -Compress
    exit 0
}

$secure = New-Object System.Security.SecureString

foreach ($char in $env:AD_TEMP_PASSWORD.ToCharArray()) {
    $secure.AppendChar($char)
}

$secure.MakeReadOnly()

Set-ADAccountPassword `
    -Identity $user.SamAccountName `
    -Reset `
    -NewPassword $secure `
    -Server $env:AD_SERVER

Set-ADUser `
    -Identity $user.SamAccountName `
    -ChangePasswordAtLogon $true `
    -Server $env:AD_SERVER

@{
    username = $user.SamAccountName
    reset = $true
    reason = "PASSWORD_RESET"
} | ConvertTo-Json -Compress
"""

    env = os.environ.copy()
    env["AD_TARGET_USER"] = username
    env["AD_SERVER"] = settings.ad_server
    env["AD_TEMP_PASSWORD"] = temp_password

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        check=False,
    )

    if result.returncode == 2:
        raise LookupError("Usuario no encontrado.")

    if result.returncode != 0:
        error = result.stderr.strip()

        raise RuntimeError(
            "No fue posible restablecer la contraseña en Active Directory: "
            f"{error}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Respuesta inesperada de Active Directory al restablecer contraseña: "
            f"{result.stdout.strip()}"
        ) from exc

    data["temp_password"] = temp_password

    return data


def unlock_user(username: str) -> dict:
    username = _validate_username(username)

    script = r"""
$ErrorActionPreference = 'Stop'

Import-Module ActiveDirectory -ErrorAction Stop

$user = Get-ADUser `
    -Identity $env:AD_TARGET_USER `
    -Server $env:AD_SERVER `
    -Properties Enabled, LockedOut

if ($null -eq $user) {
    Write-Error "USER_NOT_FOUND"
    exit 2
}

if (-not $user.Enabled) {
    @{
        username = $user.SamAccountName
        unlocked = $false
        reason = "ACCOUNT_DISABLED"
    } | ConvertTo-Json -Compress

    exit 0
}

if (-not $user.LockedOut) {
    @{
        username = $user.SamAccountName
        unlocked = $false
        reason = "ACCOUNT_NOT_LOCKED"
    } | ConvertTo-Json -Compress

    exit 0
}

Unlock-ADAccount `
    -Identity $user.SamAccountName `
    -Server $env:AD_SERVER `
    -Confirm:$false

@{
    username = $user.SamAccountName
    unlocked = $true
    reason = "UNLOCKED"
} | ConvertTo-Json -Compress
"""

    env = os.environ.copy()
    env["AD_TARGET_USER"] = username
    env["AD_SERVER"] = settings.ad_server

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        check=False,
    )

    if result.returncode == 2:
        raise LookupError("Usuario no encontrado.")

    if result.returncode != 0:
        error = result.stderr.strip()

        raise RuntimeError(
            f"No fue posible desbloquear la cuenta en Active Directory: {error}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Respuesta inesperada de Active Directory al desbloquear: "
            f"{result.stdout.strip()}"
        ) from exc
# 06 — Migración a Linux (Ubuntu)

**Estado: fase de validación.** El servidor de despliegue asignado es Ubuntu; la app hoy
corre en Windows porque `active_directory.py` invoca PowerShell. Este documento registra
qué se validó, qué falta y cómo operar.

> **Verificación:** todo lo marcado ✅ fue probado de punta a punta contra el DC real
> `SIMM-PDC1.unetrans.com.co` el **2026-10-13**. Nada de este documento es teórico.

## 1. Diagnóstico: cuánto de Windows necesita la app

Se revisó el código completo archivo por archivo. **Solo un módulo depende de Windows**:

| Archivo | Depende de Windows | Detalle |
|---|---|---|
| `app/services/active_directory.py` | **SÍ** | 3 funciones → `subprocess.run(["powershell.exe", ...])` con el módulo RSAT `ActiveDirectory` |
| Todo lo demás (`main.py`, `agent.py`, `approval_*.py`, `db/`, `teams_*`, `audit.py`) | No | Python puro + asyncio + SQLAlchemy + httpx |

Los 3 puntos de dependencia (líneas 57, 157 y 250 de `active_directory.py`):

```python
result = subprocess.run(
    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
    ...
)
```

El script PowerShell usa `Import-Module ActiveDirectory`, que **solo existe en Windows**
(no hay `pwsh` + RSAT para Linux; `pwsh` existe para Linux pero sin ese módulo). Por eso
la app no puede correr en Ubuntu sin migrar este módulo.

**Conclusión:** migrar a Linux = reescribir **un solo archivo** manteniendo su interfaz
pública (`get_user`, `reset_user_password`, `unlock_user`) — nada más del proyecto cambia.

## 2. Por qué LDAP en vez de virtualizar Windows

AD *es* un servidor LDAP estándar. Las operaciones equivalentes:

| Operación actual (PowerShell) | Equivalente LDAP |
|---|---|
| `Get-ADUser -Properties Enabled, LockedOut, PasswordExpired, PasswordLastSet` | Leer `userAccountControl`, `lockoutTime`, `pwdLastSet`, `accountExpires` |
| `Set-ADAccountPassword -Reset` | Modificar `unicodePwd` (valor UTF-16-LE) sobre **LDAPS** |
| `Unlock-ADAccount` | Escribir `lockoutTime = 0` |
| `Set-ADUser -ChangePasswordAtLogon $true` | Escribir `pwdLastSet = 0` |

`ldap3` **ya está en `requirements.txt`** (2.9.1) — hoy sin usar. Es una librería Python
pura, sin dependencias nativas.

### Opciones descartadas

| Opción | Por qué no |
|---|---|
| Contenedores Windows en Docker sobre Linux | Imposible: requieren el kernel de Windows |
| VM Windows (KVM/QEMU) en el servidor Ubuntu | Posible, pero exige licencia de Windows Server, ~4–8 GB RAM extra, y el app igual necesitaría invocar PowerShell remoto (WinRM) |
| `pwsh` en Linux + módulo ActiveDirectory | El módulo RSAT es Windows-only; no existe para Linux |

## 3. Validación de conectividad (2026-10-13)

**Servidor de pruebas:** `poc2@10.250.99.78` — Ubuntu 26.04.1 LTS (Python 3.14.4, sin
Docker, sin `pip3` por defecto).

| Prueba | Resultado |
|---|---|
| DNS `SIMM-PDC1.unetrans.com.co` | ✅ → `10.5.3.34` |
| TCP 636 (LDAPS) | ✅ OPEN |
| TCP 389 (LDAP) | ✅ OPEN |
| Handshake TLS | ✅ TLS 1.2 |
| Certificado del DC | ✅ Vigente, SAN con `Nombre DNS=SIMM-PDC1.unetrans.com.co` |
| Confianza en la CA | ⚠️ `Verify return code: 21` **antes** → ✅ `0 (ok)` **después** de instalar la CA |
| Bind LDAP + lectura de usuario | ✅ (ver §5) |

**Datos del DC obtenidos vía SSO (`Get-ADDomain`):**

| Dato | Valor |
|---|---|
| `DNSRoot` | `unetrans.com.co` |
| `NetBIOSName` | `UNETRANS` |
| `UPNSuffix` | *(vacío — el UPN es `usuario@DNSRoot`)* |
| `DomainMode` | `Windows2008R2Domain` |
| `LockoutThreshold` | **5** intentos |
| `LockoutDuration` | **0** (= bloqueo hasta desbloqueo manual) |

### 3.1 El certificado del DC vence pronto ⚠️

| Certificado | Emisor | Vigencia |
|---|---|---|
| `CN=SIMM-PDC1.unetrans.com.co` | `CA-SIMM-PDC1-CA` | 2025-12-05 → **2026-12-05** |
| `CN=CA-SIMM-PDC1-CA` (root, self-signed) | — | → 2028-04-10 |

> **Acción pendiente:** el certificado del DC vence el **2026-12-05**. Si el equipo de AD
> lo renueva, hay que re-importar la cadena en el servidor Ubuntu (la root no cambia, así
> que probablemente no haga falta nada — pero verificar con `openssl s_client` tras la
> renovación). Programar recordatorio.

### 3.2 Instalación de la CA (ya ejecutada)

El servidor Ubuntu no confiaba en la CA interna (`Verify return code: 21`). Procedimiento
aplicado:

```bash
# 1. Exportar la root desde Windows (Cert:\LocalMachine\Root, sujeto = CA-SIMM-PDC1-CA)
#    → archivo DER .crt

# 2. Subir por SFTP y convertir a PEM
openssl x509 -inform DER -in ca-simm-pdc1.crt -out ca-simm-pdc1-ca.pem

# 3. Instalar como CA de confianza (requiere sudo)
sudo cp ca-simm-pdc1-ca.pem /usr/local/share/ca-certificates/ca-simm-pdc1-ca.crt
sudo update-ca-certificates          # → "1 added, 0 removed"

# 4. Verificar
openssl s_client -connect SIMM-PDC1.unetrans.com.co:636 \
  -servername SIMM-PDC1.unetrans.com.co </dev/null 2>&1 | grep 'Verify return code'
# → Verify return code: 0 (ok)
```

## 4. Bind LDAP desde Linux — resultado

**Bind OK** sobre LDAPS con simple bind (UPN):

```
BIND OK como: sacosta@unetrans.com.co
Who am I: u:UNETRANS\sacosta

Entrada: CN=Sergio Elian Acosta Gonzalez,OU=Cuentas tecnicas MDA,OU=MesaDeAyuda,
         OU=Usuarios,DC=unetrans,DC=com,DC=co
{
  "sam": "sacosta",
  "enabled": true,
  "locked_out_effective": false,
  "pwdLastSet": "2026-10-09 16:06:44 UTC",
  "accountExpires": "2027-01-01 05:00:00 UTC"
}
```

Los campos coinciden 1:1 con los que devuelve el `Get-ADUser` actual.

### 4.1 Mapeo de atributos verificado

| Campo devuelto hoy por la app | Atributo LDAP | Nota |
|---|---|---|
| `enabled` | `userAccountControl` | `not bool(uac & 0x0002)` (`ACCOUNTDISABLE`) |
| `locked_out` | `lockoutTime` | **`> 0`** = bloqueada (ver nota abajo) |
| `password_expired` | `userAccountControl` | `bool(uac & 0x800000)` (`PASSWORD_EXPIRED`) — el mismo flag que usa PowerShell |
| `password_last_set` | `pwdLastSet` | FileTime → datetime |

> **Gotcha `lockoutTime`:** `ldap3` lo entrega como `datetime`, y cuando la cuenta **no
> está bloqueada** el valor es `1601-01-01 00:00:00` (cero de FileTime), **no** `None`.
> Hay que comparar el datetime contra la época, no hacer `int()`. El bit `0x0010`
> (`LOCKOUT`) de `userAccountControl` **no** sirve para detectar bloqueo.

## 5. Entorno preparado en el servidor

Quedó instalado y listo para reutilizar:

| Recurso | Ubicación |
|---|---|
| venv con `ldap3` | `~/venv-adtest` |
| CA ya instalada | `/usr/local/share/ca-certificates/ca-simm-pdc1-ca.crt` |
| Config OpenSSL legacy | `~/openssl-legacy.cnf` |

### 5.1 OpenSSL legacy provider (necesario solo para NTLM)

En Ubuntu 26.04, OpenSSL 3 trae MD4 en el provider *legacy* deshabilitado. `ldap3` usa
MD4 al calcular el hash NT para NTLM, así que un bind NTLM falla con
`ValueError: unsupported hash type MD4`.

**No afecta al plan** — usaremos simple bind sobre LDAPS, que no necesita MD4. Se documenta
por si se requiere NTLM en el futuro:

```ini
# ~/openssl-legacy.cnf
openssl_conf = openssl_init

[openssl_init]
providers = provider_sect

[provider_sect]
default = default_sect
legacy = legacy_sect

[default_sect]
activate = 1

[legacy_sect]
activate = 1
```

Se usa con `OPENSSL_CONF=$HOME/openssl-legacy.cnf`.

## 6. ⚠️ Lección aprendida: bloqueo de cuenta al probar

El umbral de bloqueo del dominio es **5 intentos con password incorrecta**, y la duración
es **0** (hasta desbloqueo manual — **no se desbloquea solo**).

Al probar variantes de credenciales, la cuenta `sacosta` se bloqueó **dos veces**. Cada
bind fallido incrementa `badPwdCount`, y al llegar a 5 la cuenta queda bloqueada.

**Consecuencia operativa:** probar credenciales contra AD con cuentas reales es peligroso.
Protocolo a seguir:

1. **Nunca** probar más de una variante por ejecución.
2. Verificar el estado antes: `Get-ADUser <u> -Properties LockedOut, badPwdCount`.
3. Si `badPwdCount` > 0, **parar** y confirmar la password con el dueño antes de seguir.
4. Desbloquear tras un fallo: `Unlock-ADAccount -Identity <u>`.

**Mejor aún:** usar una cuenta de servicio dedicada (ver §8) con umbral de bloqueo alto
o nulo, en vez de la cuenta personal de un humano.

## 7. Cómo detectar que el bind falla y por qué

| Mensaje | Causa |
|---|---|
| `invalidCredentials` | Password incorrecta, **o cuenta bloqueada** (AD no distingue), o UPN/NetBIOS mal escrito |
| `Verify return code: 21` | La CA del DC no está instalada en el host |
| `ValueError: unsupported hash type MD4` | NTLM sin el provider legacy de OpenSSL |
| Timeout / conexión rechazada | Firewall o el DC no escucha en 636/389 |

`invalidCredentials` **no** significa necesariamente password mala: revisar siempre
`LockedOut` primero (§6).

## 8. Pendientes antes del despligue

| # | Pendiente | Bloqueante |
|---|---|---|
| 1 | **Cuenta de servicio AD** (`svc_adsupport`) con permisos delegados de reset/desbloqueo | **SÍ** — no se despliega sin ella |
| 2 | Reescribir `active_directory.py` con `ldap3` (mismo interfaz) | SÍ |
| 3 | Probar **escritura** desde Linux (`unicodePwd`, `lockoutTime=0`, `pwdLastSet=0`) | SÍ |
| 4 | `systemd` + logrotate + cron de backup | No (operativo) |
| 5 | Verificar tras renovación del cert del DC (§3.1) | No |

### Requisitos de la cuenta de servicio (para pedir a AD)

- Permiso de **lectura** en `OU=Usuarios,DC=unetrans,DC=com,DC=co`
- Permiso de **reset de contraseña** (`Change password` delegado)
- Permiso de **desbloqueo** (`Reset password` delegado)
- Password **que no expire** (si expira, el agente cae en silencio)
- **Sin** smartcard-required
- Umbral de bloqueo **alto o nulo** (§6)

### Configuración prevista en `.env`

```dotenv
# Cuenta de servicio AD (LDAP) — pendiente de crear
AD_BIND_USER=svc_adsupport@unetrans.com.co
AD_BIND_PASSWORD=<password>
```

`AD_SERVER` y `AD_DOMAIN` ya existen y se reutilizan tal cual.

## 9. Próximos pasos sugeridos

1. Recibir la cuenta de servicio → probar bind + lectura (10 s con el venv ya listo).
2. Reescribir `active_directory.py` con `ldap3`, conservando:
   - `_validate_username` (puerta anti-inyección — **se mantiene igual**, sigue siendo válida)
   - la firma de las 3 funciones y sus tipos de retorno
   - el contrato de excepciones (`ValueError`, `LookupError`, `RuntimeError`)
3. Correr la suite existente (`tests/` + `test_full_flow.py`) — mockea AD, debe pasar sin cambios.
4. Probar escritura en una cuenta de **prueba**, nunca en producción.
5. Empaquetar: `systemd` + logrotate + backup.

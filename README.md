# AD Support Agent

Agente conversacional interno de soporte de **Active Directory** en **Microsoft Teams**:
desbloqueo de cuentas, restablecimiento de contraseña y consulta de estado, con aprobación
en Microsoft Graph/Teams y notificación automática del resultado al solicitante.

- **Stack:** Python 3.14 · FastAPI · SQLAlchemy (SQLite) · Alembic · SDK microsoft_teams · MSAL/Graph · PowerShell + módulo ActiveDirectory
- **Versión de la app:** 0.5.0 (`app/main.py`)
- **Pruebas:** 32 pytest (`tests/`) + 52 checks de flujo integral (`test_full_flow.py`)
- **Plataforma:** Windows (PowerShell + RSAT) · **Migración a Linux en curso** — ver [docs/06-migracion-a-linux.md](docs/06-migracion-a-linux.md)

## Inicio rápido

```powershell
# 1) Dependencias (en una máquina nueva)
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2) Configurar .env (ver docs/05-configuracion-y-operacion.md)

# 3) Migraciones
.venv\Scripts\python.exe -m alembic upgrade head

# 4) Arrancar (IMPORTANTE: usar -m, no un script suelto)
.venv\Scripts\python.exe -m app.main
```

Servicio en `http://0.0.0.0:3978` · Health check en `/health` ·
Aprobaciones vía Microsoft Teams/Graph.

## Documentación completa

| Documento | Contenido |
|---|---|
| [docs/README.md](docs/README.md) | Índice de la documentación |
| [docs/01-arquitectura.md](docs/01-arquitectura.md) | Componentes, estructura del código y dependencias |
| [docs/02-base-de-datos.md](docs/02-base-de-datos.md) | Esquema, columnas, índices, migraciones y estados |
| [docs/03-flujos.md](docs/03-flujos.md) | Flujos con diagramas: conversación, aprobación, worker, notificaciones |
| [docs/04-reglas-negocio.md](docs/04-reglas-negocio.md) | Reglas: intenciones, extracción de usuario, roles, validaciones, mensajes |
| [docs/05-configuracion-y-operacion.md](docs/05-configuracion-y-operacion.md) | Variables `.env`, comandos, pruebas, runbook e incidentes |
| [docs/06-migracion-a-linux.md](docs/06-migracion-a-linux.md) | Migración a Ubuntu: diagnóstico, validación LDAPS, pendientes |
| [AUDITORIA.md](AUDITORIA.md) | Auditoría de seguridad/código/pruebas (2026-10-09) |

## Pruebas

```powershell
.venv\Scripts\python.exe -m pytest tests\ -q      # 32 pruebas unitarias/de API
.venv\Scripts\python.exe test_full_flow.py        # 52 checks de flujo integral
```

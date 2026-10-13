# Documentación — AD Support Agent

Índice navegable de la documentación completa del proyecto.

| # | Documento | Contenido |
|---|---|---|
| 1 | [01-arquitectura.md](01-arquitectura.md) | Stack, componentes, estructura de carpetas, mapa de módulos y modelo de seguridad |
| 2 | [02-base-de-datos.md](02-base-de-datos.md) | Tabla `approval_requests` (18 columnas), 4 índices, cadena Alembic, máquina de estados |
| 3 | [03-flujos.md](03-flujos.md) | Diagramas Mermaid: conversación, aprobación Graph, worker de 15 s, notificaciones |
| 4 | [04-reglas-negocio.md](04-reglas-negocio.md) | Intenciones, extracción de usuario, roles/aprobadores, validaciones AD, timeouts, catálogo de mensajes y auditoría |
| 5 | [05-configuracion-y-operacion.md](05-configuracion-y-operacion.md) | Variables de entorno, comandos, pruebas, gestión del servicio, runbook de incidentes |
| 6 | [06-migracion-a-linux.md](06-migracion-a-linux.md) | Diagnóstico Windows-only, validación LDAPS contra el DC real, mapeo de atributos, pendientes |

Documentos relacionados en la raíz:

- [../README.md](../README.md) — presentación y inicio rápido
- [../AUDITORIA.md](../AUDITORIA.md) — informe de auditoría (seguridad, código, pruebas)

## Convenciones de esta documentación

- **Idioma:** español, coherente con los mensajes al usuario y los comentarios del código.
- **Diagramas:** Mermaid (se renderizan en GitHub, VS Code, etc.).
- **Comandos:** PowerShell en Windows, ejecutados desde la raíz del proyecto con el
  entorno virtual `.venv`.
- **Verificación:** el esquema de BD y la cadena de migraciones fueron verificados contra
  la base real (`ad_support.db`) y `alembic history/current` el 2026-10-13.
- **Plataforma actual:** Windows. La migración a Ubuntu está en fase de validación y se
  documenta en [06-migracion-a-linux.md](06-migracion-a-linux.md).

# Cloud DB

Proyecto integrador: FastAPI, cuatro colecciones MongoDB Atlas, PostgreSQL Neon y React + Vite. Un único proceso API ejecuta el control de bots, SSE y el scheduler cada 180 segundos.

## Arranque en Windows

El ETL se ejecuta dentro de FastAPI. Arranca un único proceso API con un worker; conserva las credenciales en `.env`.

Desde `C:\Users\crsly\cloud-db`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci --prefix frontend
npm run build --prefix frontend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Abre http://127.0.0.1:8000/ y conecta con `CLOUD_DB_API_TOKEN`. El frontend no guarda el token en localStorage ni lo incluye en URLs. Los tokens de aplicación son independientes de los usuarios y roles de las bases.

Si faltan tokens, `python -m app.configure_tokens` genera únicamente los ausentes. `python -m app.check_connections` comprueba conexiones. `python -m app.setup` inspecciona las cuatro colecciones; `--apply` crea solo las estructuras de negocio que falten, sin sobrescribir validadores incompatibles. No hace falta ejecutar setup para refrescar la interfaz.

## Alcance de datos

Únicas colecciones de aplicación en cada clúster: `categories`, `products`, `customers`, `orders`. El acceso normal pasa por una lista blanca. Los nombres se conservan en minúsculas; setup y limpieza se detienen si detectan variantes como `Categories`.

Neon conserva el esquema existente: cinco tablas de negocio (`categories`, `customers`, `products`, `orders`, `order_items`) y las tablas existentes `sync_runs`, `sync_receipts`, `system_logs` para resultados y trazabilidad del ETL. Las migraciones 001/002 se aplican una vez sobre una base vacía; no deben ejecutarse sobre un esquema existente. `003_roles.sql` se conserva como referencia y no forma parte de este arranque.

Los borrados son lógicos. La sincronización conserva esos registros y sus relaciones. Un ciclo de bot ejecuta cuatro creaciones, una lectura, una actualización y cuatro borrados lógicos. La pausa se aplica entre solicitudes. Los ciclos pueden ser finitos (1–20) o continuos (`rounds: null`). Detener un bot interrumpe las próximas operaciones; no deshace las escrituras ya confirmadas.

## API

Datos, controles, métricas y SSE requieren `Authorization: Bearer <token>`. Solo el operador puede controlar bots, disparar ETL y abrir SSE.

| Método | Ruta | Función |
|---|---|---|
| GET / POST | `/categories`, `/products`, `/customers`, `/orders` | Listar / crear |
| GET / PUT / DELETE | Cada recurso seguido de `/{entity_id}` | Consultar / actualizar / borrar lógicamente |
| GET | `/estadisticas`, `/metrics` | Métricas de Mongo y Neon |
| GET | `/clusters`, `/usuarios`, `/health` | Disponibilidad y bots gestionados |
| GET / POST | `/sincronizacion` | Estado y últimas ejecuciones / sincronizar ahora |
| GET / PUT | `/bots` | Estado / ajustar cantidad de 0 a 9 |
| POST | `/bots/{number}/start`, `/bots/{number}/stop` | Iniciar / detener un bot, 1 a 9 |
| POST | `/bots/stop` | Detener todos |
| GET | `/events` | Stream SSE autenticado |

POST/PUT/DELETE CRUD requieren `Idempotency-Key` UUID. PUT/DELETE también requieren `If-Match` con la versión leída. Los listados aceptan `limit` y `offset`. Para un resultado incierto reintenta con el mismo cuerpo y la misma clave; para un conflicto de versión, vuelve a consultar antes de modificar.

Ejemplo de `PUT /bots`: `{"count":6,"interval_seconds":1,"rounds":3}`. Los bots ya activos mantienen su configuración; los nuevos usan la enviada. El límite también se valida en el servidor. Los controles de la pantalla son la vía de lanzamiento; el administrador mantiene el conteo y las asignaciones de los bots gestionados.

Las nuevas creaciones se distribuyen entre grupos de hasta tres bots. Se ordenan los bots activos por número: los primeros tres usan el clúster 1, los siguientes tres el 2 y los últimos tres el 3. Con 6 bots el reparto es 3+3; con 9 es 3+3+3. Funciona también con IDs no consecutivos (por ejemplo, solo el bot 9 usa el clúster 1). Los bots que se están deteniendo conservan su lugar hasta terminar. Al iniciar o terminar un bot se recalcula la asignación para las siguientes solicitudes; una solicitud ya autenticada conserva su destino. `/bots`, `/usuarios` y el snapshot SSE exponen `assignments` y `cluster_loads`.

Las creaciones manuales del operador usan el clúster 1. Si se utiliza un token de bot directamente fuera del administrador, su destino se determina por su ID en grupos 1–3/4–6/7–9; esos clientes externos no forman parte del conteo del dashboard. Las consultas, actualizaciones y borrados de documentos existentes encuentran su clúster original. No hay migración ni copia automática de documentos al cambiar la carga. El ETL sigue leyendo los tres clústeres y sincroniza secuencialmente hacia la misma base Neon, conservando `source_cluster`.

## Tiempo real y sincronización

SSE emite `bot_id`, `operation`, `collection`, `cluster_id`, `status`, `message`, `timestamp`, un ID y el tramo observado. CRUD utiliza INSERT/READ/UPDATE/DELETE; los cambios de bots y ETL se identifican con BOT/ETL. `status` es sent/success/deleted/error. `bot_id` y `collection` pueden ser nulos para eventos generales de ETL.

El navegador usa fetch streaming para enviar el encabezado Bearer. Reanuda con Last-Event-ID, recibe un snapshot si el cursor expiró y conserva las últimas 20 operaciones. FastAPI retiene hasta 500 eventos en memoria; se pierden al reiniciar. Las animaciones representan observaciones, no mediciones de paquetes de red. Los reintentos de una versión ya cargada no animan una nueva entrega a Neon.

Las métricas se consultan cada 10 segundos y muestran la hora de observación. El conteo de bots se actualiza por SSE. Documentos sincronizados significa misma identidad, origen y versión en Neon. Clústeres activos significa clústeres accesibles; no significa que los tres reciban nuevas escrituras al mismo tiempo. Tablas con datos cuenta las cinco tablas de negocio no vacías; errores de carga cuenta errores ETL registrados en Neon.

## Mantenimiento opcional de MongoDB

La utilidad `app.cleanup_mongo` se ejecuta por separado y no forma parte del arranque. Inspecciona colecciones técnicas conocidas; antes de aplicar una eliminación, confirma que no están en uso y que no necesitas conservar sus datos.

```powershell
.\.venv\Scripts\python.exe -m app.cleanup_mongo
```

Por defecto solo inspecciona. Enumera las siete colecciones técnicas conocidas y sus cantidades; protege las cuatro de negocio, las bases de sistema y las colecciones desconocidas. Para eliminar únicamente nombres revisados:

```powershell
.\.venv\Scripts\python.exe -m app.cleanup_mongo --apply --confirm-database cloud_db --collections sync_outbox audit_events applied_commands entity_directory operation_requests mutation_locks client_sessions
```

Usa el nombre real de `CLOUD_DB_MONGO_DATABASE` si no es `cloud_db`. La eliminación de esas colecciones destruye su historial técnico; no borra documentos de negocio. Si aparecen colecciones desconocidas o variantes de mayúsculas, revísalas antes de decidir su destino. La utilidad conserva las cuatro colecciones de negocio y no modifica usuarios ni roles.

## Desarrollo y verificación

Para editar React, deja FastAPI en 8000 y ejecuta `npm run dev --prefix frontend` en otra terminal. El proxy de Vite envía REST y SSE a FastAPI.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
npm test --prefix frontend
npm run build --prefix frontend
```

Las pruebas PostgreSQL requieren `CLOUD_DB_TEST_POSTGRES_URI` apuntando exclusivamente a una base local desechable y vacía. El fixture se niega a sobrescribir un esquema existente. Ver [arquitectura](docs/architecture.md), [verificación](docs/verification.md) y [despliegue](docs/deployment.md).

## Estructura para estudiar el proyecto

| Ruta | Contenido |
|---|---|
| `app/main.py`, `app/api.py` | Arranque FastAPI, registro de rutas y CRUD. |
| `app/models.py`, `app/service.py`, `app/database.py` | Validación, reglas de negocio y conexiones. |
| `app/bots.py`, `app/simulation.py` | Cliente de simulación y administrador de bots. |
| `app/worker.py`, `app/projection.py` | Scheduler, ciclo ETL y proyección a PostgreSQL. |
| `app/runtime.py`, `app/events.py`, `app/monitoring.py` | Estado en memoria, SSE y métricas. |
| `app/security.py`, `app/core/` | Autenticación y configuración. |
| `app/setup.py`, `app/mongo_collections.py` | Creación e inspección de colecciones, validadores e índices. |
| `app/check_connections.py`, `app/configure_tokens.py`, `app/cleanup_mongo.py` | Diagnóstico, tokens y mantenimiento opcional. |
| `frontend/src/`, `frontend/dist/` | Fuentes React y compilación que sirve FastAPI. |
| `migrations/`, `deploy/` | Esquemas y roles PostgreSQL; servicio y proxy para Xubuntu. |
| `tests/`, `frontend/tests/` | Pruebas automatizadas y auxiliares de aceptación. |
| `docs/` | Arquitectura, seguridad, despliegue y verificación. |

Para seguir una operación, comienza por `app/main.py` y `app/api.py`, continúa en `app/service.py` y revisa después `app/worker.py` y `app/projection.py`. El nombre `worker.py` corresponde al ETL activo integrado en FastAPI.

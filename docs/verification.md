# Verificación de la refactorización

## Evidencia obtenida

- Actualización de reparto por clúster: 53 pruebas Python aprobadas y cinco omitidas por no configurar una base PostgreSQL de prueba. Las cinco de integración se habían ejecutado contra PostgreSQL 18.6 local desechable durante la refactorización anterior; no se repitieron en esta actualización. No se utilizó Neon de trabajo para estas pruebas.
- Pruebas de todos los conteos 0–9, grupos de hasta tres, IDs no consecutivos, parada en curso y reasignación. Solicitudes REST autenticadas de 3/6/9 bots verifican el destino escrito en Mongo simulado y los eventos SSE. CRUD conserva el origen anterior tras reasignar, además del control de versión, replay tras respuesta incierta y lista blanca.
- Prueba de orquestación ETL con conexiones simuladas: los tres orígenes se leen aun con cero bots, se mantiene el orden de dependencias y cada evento conserva su clúster. La proyección y el scheduler no se modificaron para el reparto.
- SSE: campos de eventos, separación sent/success/error, buffer acotado, cursor, snapshot y cierre por desconexión.
- ETL real PostgreSQL: no duplicación al repetir, rechazo de un hash alterado sin subir versión, FK y rollback, normalización de items, conservación de precio histórico, colisión de origen y no resurrección de borrados.
- Cinco pruebas Node aprobadas para parser SSE fragmentado, heartbeat, historial acotado y rutas CRUD/ETL para cada clúster independientemente del ID del bot.
- Build de producción React + Vite completado.
- Revisión en navegador de la refactorización anterior: pantalla clara, conexión, inicio de tres bots, carga manual a PostgreSQL, inicio de seis bots, detención individual y detención total. Esa prueba utilizó Mongo en memoria y PostgreSQL local real mediante `tests/ui_server.py`; no fue una prueba de Atlas real ni una validación visual del nuevo reparto entre clústeres.

## Pruebas reproducibles

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
npm test --prefix frontend
npm run build --prefix frontend
```

Para las cinco pruebas PostgreSQL, fija `CLOUD_DB_TEST_POSTGRES_URI` a una base vacía desechable. El fixture instala 001/002 y se niega a sobrescribir un esquema existente. Las demás pruebas no requieren conexión a proveedores.

`tests/ui_server.py` solo admite entorno test y la URI local desechable especificada en el propio archivo. Sustituye el acceso Mongo exclusivamente en ese servidor de pruebas, nunca desde `app.main` en producción.

## Pendientes que requieren los servicios reales

- Inventario y validadores de Atlas. El intento de inspección de esta sesión terminó con ConfigurationError; no se eliminaron colecciones ni se confirmaron sus nombres remotos.
- Prueba de nueve bots con Atlas y Neon reales y permisos actuales.
- Limpieza explícita de las colecciones técnicas antiguas después de detener los procesos anteriores.
- Despliegue y revisión en Xubuntu. Revisión visual móvil pendiente; el CSS contiene los puntos de adaptación, pero no se afirma una prueba móvil completada.

Las pruebas locales no sustituyen estas comprobaciones conectadas.

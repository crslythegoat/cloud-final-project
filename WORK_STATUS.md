# Estado actual

Refactorización de backend y frontend implementada según el alcance autorizado.

- Solo cuatro colecciones en el acceso normal a Mongo. Se retiraron outbox, directorio, sesiones, comandos persistidos y prototipos.
- CRUD conserva validaciones, referencias, versiones, borrados lógicos y reintentos sin duplicar creaciones.
- Distribución de nuevas creaciones entre bots activos ordenados por ID, hasta tres por clúster (6 → 3+3; 9 → 3+3+3); documentos existentes conservan origen. Asignación capturada por solicitud y publicada en REST/SSE; interfaz resalta todos los destinos asignados.
- Control individual y total de bots, límite 0–9, SSE con eventos reales y memoria acotada.
- ETL por snapshot, scheduler de 180 segundos dentro de FastAPI y sincronización manual. Tablas/roles existentes preservados.
- React + Vite, tema claro, diagrama SSE, métricas, controles y últimas 20 operaciones.
- Script `app/cleanup_mongo.py` preparado; no se ejecutó eliminación en Atlas.

Ver README.md para arranque y docs/verification.md para evidencia y pendientes. Detener el worker anterior: esta versión solo necesita el proceso FastAPI, con `--workers 1`.

No se modificaron credenciales ni se crearon commits. El repositorio local sigue sin archivos versionados y la metadata Git no se alteró.

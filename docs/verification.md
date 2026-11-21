# Verificación del sistema

## Validaciones locales

Desde la raíz del repositorio y con las dependencias instaladas, ejecuta:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
npm test --prefix frontend
npm run build --prefix frontend
.\.venv\Scripts\python.exe -c "from app.main import app; print(app.title)"
```

Cada comando debe terminar con código 0. Registra por separado las pruebas aprobadas, fallidas y omitidas. Una importación correcta comprueba la carga del módulo; no demuestra conexión con las bases ni ejecuta el ciclo de vida del servidor.

Las pruebas Python comprueban CRUD, autenticación, versiones, idempotencia, reparto de bots, SSE y ETL. Las pruebas Node revisan el parser SSE, el historial acotado y las rutas de las animaciones. La compilación debe generar `frontend/dist/`, que FastAPI necesita para servir la interfaz.

## Integración PostgreSQL

Configura `CLOUD_DB_TEST_POSTGRES_URI` en el entorno del proceso hacia una base PostgreSQL local, vacía y desechable. El fixture de `tests/conftest.py` instala las migraciones 001/002 y se niega a sobrescribir un esquema `cloud_db` existente. No uses la base de trabajo de Neon.

Sin esta variable, cinco pruebas de `tests/test_projection.py` se omiten. Con una base de prueba preparada, repite `pytest` y verifica que esos casos se ejecuten: repetición de entregas, hash y versión, restricciones y rollback, líneas de pedidos y precios históricos, origen y borrados.

## Comprobación con Atlas y Neon

1. Ejecuta `python -m app.check_connections` con la configuración del proyecto. Revisa que los tres orígenes y Neon respondan.
2. Ejecuta `python -m app.setup` sin `--apply` para inspeccionar colecciones, validadores e índices.
3. Arranca FastAPI y consulta `/health` con el token de operador. Prueba CRUD usando `Idempotency-Key` y, en cambios o borrados, `If-Match`.
4. Inicia 3, 6 y 9 bots. Comprueba `assignments` y `cluster_loads` en `/bots` y los destinos de SSE: los grupos contienen hasta tres bots por clúster. Prueba también detener un bot y operar sobre un documento que conserve su origen.
5. Solicita `POST /sincronizacion`, consulta su estado y contrasta identidad, `source_cluster` y `source_version` con Neon. Repite la carga y comprueba que no duplica datos. Verifica borrados lógicos y relaciones.
6. Deja transcurrir el intervalo de 180 segundos y comprueba la ejecución automática en `sync_runs`. Revisa `system_logs` si hay errores.

## Interfaz y auxiliares de pruebas

Comprueba conexión, controles de bots, métricas, últimas veinte operaciones y reanudación de SSE. Una animación de entrega a Neon debe corresponder a un cambio confirmado; una versión repetida no representa una nueva inserción. Revisa también la distribución en una pantalla móvil.

`tests/ui_server.py` permite aceptación visual con Mongo en memoria y PostgreSQL local desechable. Solo acepta `CLOUD_DB_ENVIRONMENT=test` y la URI local fijada en ese archivo. La base debe prepararse antes de arrancarlo; no se usa como entrada de producción ni sustituye la verificación conectada.

`tests/socketless_loop.py` es un auxiliar optativo para entornos sin acceso a sockets. Se activa con `CLOUD_DB_TEST_SOCKETLESS=1`; no admite tráfico de red y no interviene en producción.

## Xubuntu y red local

Sigue [despliegue](deployment.md). Verifica el estado de `cloud-db-api.service`, abre el dashboard desde otro equipo de la misma red y prueba una operación y una sincronización. Reinicia el servicio y confirma que los datos persistentes siguen presentes, mientras los bots gestionados y los eventos en memoria se reinician. Registra la evidencia obtenida en el equipo real; las pruebas locales por sí solas no acreditan el despliegue.

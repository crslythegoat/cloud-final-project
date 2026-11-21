# Arranque y despliegue

## Windows

Desde la raíz del repositorio, conserva `.env`, instala las dependencias y compila la interfaz:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci --prefix frontend
npm run build --prefix frontend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Abre http://127.0.0.1:8000/. FastAPI sirve `frontend/dist/` y ejecuta el administrador de bots y el scheduler ETL. Usa un solo worker. Si cambias el puerto, ajusta `CLOUD_DB_API_BASE_URL` para los clientes internos; el proxy de Vite presupone el puerto 8000.

## Xubuntu

Esta sección describe el procedimiento de instalación; no acredita una prueba completada en el equipo. Comprueba Python >=3.11, una versión Node compatible con Vite 8, espacio y conectividad a Atlas y Neon.

Prepara el entorno Python en `/opt/cloud-db/current/.venv`, instala `requirements.txt` y compila el frontend con `npm ci --prefix frontend` y `npm run build --prefix frontend`. Crea el usuario de sistema `cloud-db` y guarda las credenciales en `/etc/cloud-db/cloud-db.env`, accesibles solo para el usuario del servicio.

Revisa que las rutas de `deploy/cloud-db-api.service` coincidan con la instalación. El servicio arranca un único worker de FastAPI:

```bash
sudo cp deploy/cloud-db-api.service /etc/systemd/system/cloud-db-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now cloud-db-api.service
sudo systemctl status cloud-db-api.service
```

`deploy/nginx.conf` es la plantilla HTTPS para la red local. Ajusta nombre del host y certificado, instala la configuración y valida Nginx antes de activarla. El proxy tiene buffering desactivado para SSE. FastAPI escucha en loopback y Nginx proporciona el acceso de red. No se necesita un servicio separado para el ETL.

## Verificación de la instalación

Sigue [verificación](verification.md): consulta `/health` con autorización, inicia y detén un bot, observa CRUD por SSE y solicita sincronización manual. Comprueba después el ciclo automático de 180 segundos. Al reiniciar el servicio, los datos de las bases permanecen y el historial de eventos en memoria comienza de nuevo.

Para una actualización, detén el servicio antes de reemplazar archivos y recompila el frontend. No reconstruyas esquemas ni ejecutes migraciones de roles como parte del arranque. La utilidad opcional `app.cleanup_mongo` tiene un procedimiento independiente descrito en el [README](../README.md).

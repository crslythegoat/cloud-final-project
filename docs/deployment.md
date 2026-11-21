# Arranque y despliegue

## Windows

Desde la raíz del repositorio, conserva `.env`, detén API/worker antiguos, ejecuta `npm ci --prefix frontend` y `npm run build --prefix frontend`, y arranca:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Abrir http://127.0.0.1:8000/. El ETL ahora forma parte de la API; no abrir una segunda terminal para `app.worker`. Si cambias el puerto, ajusta `CLOUD_DB_API_BASE_URL` para los clientes internos. El proxy de desarrollo de Vite presupone puerto 8000.

## Xubuntu

No se ha desplegado esta versión en el equipo Xubuntu. Requiere Python >=3.11 y una versión Node compatible con Vite 8 (esta entrega se compiló con Node 24.19.0). Revisa primero `python3 --version`, `node --version`, espacio y conectividad a Atlas/Neon.

Prepara el entorno Python en `/opt/cloud-db/current/.venv` e instala `requirements.txt`. Compila el frontend con `npm ci --prefix frontend && npm run build --prefix frontend`. Conserva las credenciales en `/etc/cloud-db/cloud-db.env`, legibles únicamente por el usuario del servicio.

Antes de reemplazar una instalación anterior:

```bash
sudo systemctl disable --now cloud-db-worker.service
sudo systemctl stop cloud-db-api.service
```

El primer comando aplica solo si estaba instalado ese servicio. Copia la versión nueva y utiliza `deploy/cloud-db-api.service` con un solo worker; requiere que exista el usuario de sistema `cloud-db` y que las rutas sean correctas. No ejecutes migraciones de roles ni reconstruyas esquemas existentes.

```bash
sudo cp deploy/cloud-db-api.service /etc/systemd/system/cloud-db-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now cloud-db-api.service
sudo systemctl status cloud-db-api.service
```

`deploy/nginx.conf` contiene la plantilla HTTPS para la red local: configura nombre del host, certificado y rutas del equipo. El proxy mantiene el buffering desactivado para SSE. La API escucha en loopback; Nginx sirve el acceso de red. No hay servicio separado de ETL.

## Cambio de versión y limpieza

Primero confirma que no quedan procesos antiguos. La limpieza es independiente del arranque; `python -m app.cleanup_mongo` inspecciona sin borrar. El README detalla cómo seleccionar las colecciones antiguas que se eliminarán. Setup y cleanup nunca alteran roles/usuarios.

Tras instalar, comprobar `/health` autenticado, iniciar un bot, observar INSERT/READ/UPDATE/DELETE vía SSE, detenerlo, sincronizar manualmente y verificar los conteos y un siguiente ciclo a los 180 segundos. Reiniciar el servicio y confirmar que los datos siguen en las bases mientras la lista efímera de eventos empieza de nuevo.

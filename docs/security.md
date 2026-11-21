# Acceso y seguridad

Las URI y credenciales de Atlas y Neon se configuran en `.env` para desarrollo y en el archivo de entorno del servicio para despliegue. No deben incluirse en el frontend, los logs ni el repositorio. La aplicación no crea ni amplía permisos de base de datos automáticamente.

REST y SSE usan Bearer tokens de aplicación. El operador puede controlar bots, iniciar ETL y abrir el stream; los tokens de bot permiten CRUD y monitoreo. Los tokens se comparan con `compare_digest`. El frontend conserva el token solo en memoria y envía Authorization, nunca un parámetro en la URL. No contiene credenciales de bases.

El despliegue debe usar un único proceso API y HTTPS al acceder por red. El gestor solo admite los nueve identificadores de bot y valida los límites en el servidor. El frontend consume las mismas rutas de control, sin llamadas directas a Mongo o PostgreSQL.

Los roles existentes deben permitir las operaciones sobre las cuatro colecciones y las tablas ya existentes usadas por la aplicación. Si falta un privilegio, la operación falla y se informa; el programa no intenta ampliar permisos automáticamente. `migrations/003_roles.sql` contiene la configuración de roles PostgreSQL. Su aplicación es una tarea explícita del administrador, previa revisión del destino y los privilegios; no forma parte del arranque de la API.

El script de limpieza pide nombre de base y nombres explícitos de colecciones para aplicar. Inspecciona los tres destinos antes de borrar y se niega a trabajar en admin/config/local o con alias de mayúsculas de las cuatro colecciones protegidas. Antes de aplicar una eliminación, confirma que las colecciones seleccionadas no están en uso. Las pruebas de esta utilidad están en `tests/test_runtime.py`.

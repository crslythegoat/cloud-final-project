# Acceso y configuración existente

Se preservan las URI y los usuarios de Atlas y Neon de `.env`. No se ejecutaron CREATE ROLE, ALTER ROLE, GRANT ni REVOKE sobre esos servicios durante la refactorización. Las migraciones 001/002/003 no fueron modificadas.

REST y SSE usan Bearer tokens de aplicación. El operador puede controlar bots, iniciar ETL y abrir el stream; los tokens de bot permiten CRUD y monitoreo. Los tokens se comparan con `compare_digest`. El frontend conserva el token solo en memoria y envía Authorization, nunca un parámetro en la URL. No contiene credenciales de bases.

El despliegue debe usar un único proceso API y HTTPS al acceder por red. El gestor solo admite los nueve identificadores de bot y valida los límites en el servidor. El frontend consume las mismas rutas de control, sin llamadas directas a Mongo o PostgreSQL.

Los roles existentes deben permitir las operaciones sobre las cuatro colecciones y las tablas ya existentes usadas por la aplicación. Si falta un privilegio, la operación falla y se informa; el programa no intenta ampliar permisos automáticamente. `migrations/003_roles.sql` es material conservado de la versión anterior, no un paso de instalación de esta refactorización.

El script de limpieza pide nombre de base y nombres explícitos de colecciones para aplicar. Inspecciona los tres destinos antes de borrar y se niega a trabajar en admin/config/local o con alias de mayúsculas de las cuatro colecciones protegidas. No ejecutar procesos de la versión anterior después de limpiar.

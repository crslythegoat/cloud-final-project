# Arquitectura ajustada al integrador

## Componentes

FastAPI sirve REST, SSE y el build React. El administrador de bots abre hasta nueve clientes HTTP dentro del mismo proceso. El scheduler lanza una sincronización cada 180 segundos y comparte la misma exclusión de ejecución con el botón manual. No se ejecuta un worker separado.

MongoDB solo contiene categorías, productos, clientes y pedidos: `categories`, `products`, `customers`, `orders`. `database.collection()` rechaza cualquier otro nombre antes de abrir la base. La única excepción de mantenimiento es `cleanup_mongo.py`, que inspecciona o elimina explícitamente las colecciones antiguas conocidas.

## Consistencia y enrutamiento

Los bots activos en memoria se ordenan por ID y se distribuyen en grupos de hasta tres por clúster: 6 bots → 3+3; 9 → 3+3+3. Se incluyen bots que están terminando una solicitud. El destino se captura al autenticar cada solicitud y se comparte entre la escritura y su evento SSE; cambiar la carga no cambia el destino de una solicitud ya autenticada. Los snapshots publican las asignaciones por bot y la carga de cada clúster. Al disminuir la carga se compacta la asignación para las nuevas creaciones, sin mover datos anteriores: un UUID se busca en los tres orígenes para consultar o modificar el documento real. Encontrar el mismo UUID en varios orígenes produce conflicto, no una elección silenciosa. Las escrituras manuales del operador van al clúster 1; los tokens de bots usados fuera del administrador tienen una asignación por ID y no cuentan como bots gestionados.

Un bloqueo de proceso serializa validación de referencias, unicidad entre orígenes y modificación. Cada documento se escribe atómicamente; actualización/borrado utiliza compare-and-swap por versión. Esto requiere un único proceso de API y que las escrituras pasen por él. No constituye una transacción distribuida entre clústeres, ni protege contra modificaciones directas simultáneas fuera de la API.

Las creaciones usan un UUID determinista derivado del actor y la clave de idempotencia. El documento conserva únicamente la identidad/hash de su última solicitud, sin cola ni historial de telemetría embebido. Hay hasta 1000 respuestas de replay en memoria. Tras reiniciar, repetir la última solicitud puede recuperarse del documento; una creación antigua que ya cambió responde conflicto. No se promete replay indefinido de todas las versiones.

## ETL

Se captura el estado actual de las cuatro colecciones de los tres orígenes, manteniendo la exclusión de las escrituras de la API durante la lectura. Se proyectan categorías, clientes, productos y pedidos, en ese orden. Los items se normalizan en `order_items`; se mantienen precios históricos.

Cada documento/versión usa una identidad de recibo determinista. El upsert y el recibo se confirman en la misma transacción PostgreSQL. Una entrega repetida compara su hash y no añade filas; una versión antigua no sustituye una nueva. Las claves primarias, únicas y foráneas de Neon siguen activas. Un fallo se registra en `system_logs` y se reintentará al volver a leer el origen en el próximo ciclo.

El bloqueo advisory de PostgreSQL impide dos ciclos nuevos simultáneos. Debe detenerse el worker antiguo al actualizar, porque ejecuta un protocolo diferente. El scheduler no dispara el primer ciclo hasta transcurridos 180 segundos; el botón permite ejecutarlo antes.

El ETL sincroniza el estado más reciente, incluyendo borrados lógicos; no conserva cada cambio intermedio ocurrido entre dos ciclos. Los recibos trazan versiones observadas y las ejecuciones quedan en Neon. SSE es telemetría efímera, no un registro durable de auditoría CRUD.

El laboratorio usa un escaneo completo de sus colecciones; no está diseñado para millones de documentos. Se mantiene así para evitar colas auxiliares, cursores frágiles y escrituras de confirmación innecesarias en Mongo.

## Eventos y estado visible

SSE usa un buffer acotado de 500 eventos y snapshots de 20. Incluye cursor de instancia y secuencia, reanudación, heartbeat y cancelación por desconexión. Reiniciar FastAPI apaga los bots y reinicia la memoria de telemetría. El historial ETL permanece en Neon.

La interfaz anima solicitudes recibidas por la API y sus confirmaciones posteriores. Los errores aparecen en rojo en el punto observado; una entrega a Neon solo se anima si se confirmó un cambio nuevo. Los recibos repetidos aparecen en el visor sin representar nuevas inserciones. Las animaciones tienen duración visual fija y no miden latencia real.

El indicador distingue sincronizado, ejecución y error; también muestra sin observación o pendiente cuando todavía no hay evidencia para afirmar que los datos están sincronizados. Las métricas indican su hora de observación y nunca convierten una conexión fallida en un cero confirmado.

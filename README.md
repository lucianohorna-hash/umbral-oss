# Umbral · código abierto

**Versión completa actual (incluye corrección del formato JSON):** [Descargar código y pruebas](umbral-source-durable-20261028-v2.zip). Para esta versión utiliza el ZIP, que contiene su manifiesto completo; el árbol anterior se conserva como referencia.

## Continuar, recordar y recuperar

La interfaz muestra el estado de guardado y respuesta. Retomar mi historia resume
el canon conocido por el protagonista sin revelar secretos ni usar un modelo.
Los nombres registrados en la conversación abren consultas del archivo. Las
correcciones conservan el original y crean una propuesta para revisión; no vuelven
a ejecutar los efectos materiales de escenas ya aceptadas. Ritmo, extensión,
tono y diálogo se guardan por campaña con control de versión.

Cada escritura narrativa actualiza una copia transaccional privada del universo.
Se conservan los estados más recientes de hasta siete horas con actividad. La
recuperación comprueba su integridad y crea un universo separado, sin sobrescribir
el original. Excluye credenciales y permisos del conector. Las copias automáticas
residen en la misma base: conserva también una descarga SQLite independiente.
No añaden servicios de pago. Al abrir una campaña, la conversación muestra el
final; al leer mensajes anteriores, un botón permite volver sin perder el orden.

## Conversación, dictado y lectura

«Enviar y narrar» guarda la decisión y pide la respuesta con un solo botón cuando
hay un proveedor conectado. «Reintentar respuesta» permite recuperar un envío
interrumpido. «Dictar» usa el reconocimiento del navegador si está disponible;
también puedes usar el micrófono del teclado del teléfono. Revisa el texto antes
de enviarlo. «Escuchar» lee una respuesta con las voces del dispositivo y ofrece
pausa y detención. La disponibilidad depende del navegador y de sus permisos;
el reconocimiento puede necesitar internet. Umbral no almacena el audio ni añade
una API de voz de pago. Las pruebas de interfaz no sustituyen la prueba física
del micrófono y los altavoces del teléfono.

Motor de universos narrativos persistentes. Crea campañas desde cero, conserva
personajes, fuentes y conversaciones, y revisa las propuestas antes de cambiar
el canon. La importación de historias anteriores es opcional.

Se distribuye con licencia MIT, incluida en `LICENSE`, por decisión expresa
del propietario. La publicación del código no demuestra por sí sola que esta
instalación tenga habilitado el uso del plan ChatGPT en un servidor remoto.

## Ejecutar localmente

Requiere Python 3.11 o posterior. En Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,chatgpt-plan]"
.\.venv\Scripts\python.exe -m narrative.cli serve --chatgpt --port 8002
```

Abre http://127.0.0.1:8002 y crea tu universo y campaña. No necesitas importar
datos ni contratar una API. El modo sin proveedor permite guardar y editar
manualmente. Para narración con el plan ChatGPT se necesita una cuenta elegible,
autorizar los permisos y elegir un modelo disponible. Se aplican los límites de
ese plan; la aplicación no activa automáticamente una API de pago.

La demostración es ficticia y usa otra base:

```powershell
.\.venv\Scripts\python.exe -m narrative.cli demo --db data/demo.db
.\.venv\Scripts\python.exe -m narrative.cli serve --db data/demo.db --port 8001
```

## Arquitectura y continuidad

- `narrative/service.py`: operaciones del dominio y canon versionado.
- `db.py`, `schemas.py`, `migrations/`: SQLite o PostgreSQL, estructuras y migraciones.
- `continuity.py`, `identity.py`: verificaciones y anclajes de identidad.
- `memory.py`, `recall.py`: consulta de detalles y citas históricas con procedencia.
- `narration.py`, `providers.py`, `chatgpt_plan.py`: coordinación y adaptadores de IA.
- `self_hosted_plan.py`, `runtime_credentials.py`, `plan_handoff.py`: sesión personal
  persistente del servidor y traspaso cifrado, separados del canon.
- `simulation.py`, `importer.py`: avance fuera de escena e importación revisable.
- `api.py`, `security.py`, `remote_bridge.py`: transporte y límites de acceso.
- `static/`: interfaz conversacional e instalación como PWA.

Una escena generada es una propuesta. El historial original no se convierte
automáticamente en canon o conocimiento de un personaje. Las correcciones
requieren decisión explícita, quedan versionadas y admiten reversión. Los
reintentos recuperan respuestas ya guardadas. No se garantiza cero errores
semánticos: la prosa libre requiere revisión y la recuperación histórica es
literal y limitada. Las coincidencias entre símbolos no prueban una conexión.

## iPhone y servidor personal de código abierto

Instalar la PWA no incorpora un modelo al iPhone. Para recibir respuestas con
el computador apagado hace falta un proveedor autorizado ejecutándose en el
servidor, credenciales persistentes protegidas y una prueba real desde el teléfono.
`render.yaml` prepara alojamiento manual con PostgreSQL. Para la instalación
personal de código abierto, `NARRATIVE_PERSONAL_OSS_PLAN=true` habilita el adaptador
del servidor, inicialmente sin sesión. Esta opción requiere HTTPS y la protección
de propietario del backend; rechaza proveedores alternativos configurados.

La guía oficial para aplicaciones de código abierto en máquinas virtuales propias
describe autorizar localmente la misma herramienta/cuenta/espacio de trabajo,
transferir una sesión protegida, conservar un host propio y dejar la renovación
al servidor. Cada instalación debe completar la comprobación real de la cuenta
y del dispositivo.
Esto no equivale a una autorización para ofrecer un producto comercial alojado
con los planes de otras personas ni para compartir una cuenta entre usuarios.

El propietario inicia sesión en su instalación local con el mismo cliente,
cuenta y espacio de trabajo que utilizará en el servidor. En el panel del
servidor prepara su archivo de conexión; en el computador selecciona ese archivo
y prepara el paquete cifrado para el origen HTTPS mostrado. La sesión local
deja de renovar y conserva únicamente el paquete protegido para los reintentos.
Después carga ese paquete en el servidor y verifica el catálogo del plan.
Este paso transfiere credenciales: no debe hacerse sin autorización específica
del propietario para el servidor de destino. La preparación del descriptor no
transfiere la sesión por sí sola.

Los paquetes caducan a los 15 minutos. Los reintentos de una importación ya
confirmada recuperan su recibo y no reemplazan tokens renovados. El servidor
conserva su propio host; el paquete no incluye su clave privada. Su sesión se
almacena cifrada en PostgreSQL, incluso si el alojamiento pierde archivos al
reiniciarse. La renovación tiene un único responsable; no se debe ejecutar
otra copia del perfil original en paralelo. Si se pierde la clave del servidor,
la aplicación bloquea la sesión en lugar de sustituirla silenciosamente.

Tras elegir un modelo disponible, las decisiones escritas en Umbral pueden
recibir respuesta automática dentro de la misma PWA. Una instalación todavía
sin sesión o con un catálogo que no se pudo verificar mantiene desactivada esa
respuesta y conserva la intervención. La verificación final requiere probarlo
desde el iPhone con el computador apagado; las pruebas simuladas no la acreditan.

Referencias:

- https://developers.openai.com/siwc/token-sharing-open-source
- https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms

## Datos y credenciales

Este paquete no contiene campañas, conversaciones, bases, perfiles ChatGPT,
contraseñas, historial de Git, registros ni configuración del despliegue personal.
Los enlaces al conector privado del propietario se quitaron de esta copia.
Para usar un conector propio hace falta desplegarlo y conceder acceso por separado.

Los datos se crean en `data/`. El perfil local de ChatGPT queda en
`chatgpt-plan/connection.bin`, protegido con DPAPI en Windows y permisos 0600 en
Unix. No se debe copiar el archivo DPAPI directamente a Linux. Los datos de
canon y las copias no incluyen esa sesión. Las claves del servidor nunca deben
ponerse en el frontend, en el repositorio o en una URL.

## Verificación

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Las pruebas normales usan datos ficticios y transportes de IA simulados. Las
pruebas PostgreSQL reales son optativas y requieren una base privada de pruebas;
consulta su advertencia antes de configurarlas. No apuntes pruebas a tus campañas.
El manifiesto enumera los archivos distribuidos y sus huellas SHA-256; no incluye
su propia huella. La lista de exclusión y la búsqueda de patrones son controles
de preparación, no una garantía universal de ausencia de secretos.


## Narración persistente y campañas largas

El navegador solicita un trabajo guardado en la base y consulta su estado. Cerrar
la PWA no cancela la respuesta en el servidor. Al volver, incluso desde otro
dispositivo, se recupera el recibo de la última intervención pendiente. Un reinicio
brusco del servidor puede interrumpir una generación: el sistema lo indica y exige
reintentar, en vez de gastar otra generación sin avisar. Nunca garantiza ejecución
exactamente una vez si el proveedor y el servidor se interrumpen a la vez.

Jugar carga inicialmente los últimos cinco mensajes. «Ver conversaciones anteriores»
solicita otros cinco; no se ocultan conversaciones completas ya descargadas. El
narrador conserva acceso al archivo independiente de esta ventana visual. Las
vistas de revisión e historial solicitan su información completa al abrirlas.

La respuesta de IA incluye prosa y un contrato estructurado: identidades, rasgos,
conocimientos utilizados y candidatos citados literalmente. Se contrastan antes de
mostrar la escena. Las extracciones siguen pendientes de revisión. Estos controles
no garantizan cero errores semánticos: dependen también de lo que declare el modelo
sobre su prosa. La evolución del carácter y los cambios de canon siguen trazados.

Los tiempos guardados distinguen memoria, proveedor, verificación y guardado. La
recuperación automática usa una base por hora y cambios incrementales encadenados,
evita reescribir toda la historia por cada mensaje y verifica su integridad antes
de recuperar. Sigue estando en la misma base de datos.

La copia independiente cifrada está preparada en
`integrations/backup/github-actions.yml`, desactivada por defecto. Requiere una
clave pública y una autorización exclusiva para descargar archivos cifrados; no
habilita acceso al canon sin cifrar. La recuperación offline está en
`tools/recover_encrypted_backup.py`. Nunca publicar la clave privada. Las cuotas de
GitHub y el presupuesto de gasto deben revisarse antes de habilitar la programación.

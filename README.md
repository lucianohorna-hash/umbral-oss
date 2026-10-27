# Umbral · código abierto

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
- `simulation.py`, `importer.py`: avance fuera de escena e importación revisable.
- `api.py`, `security.py`, `remote_bridge.py`: transporte y límites de acceso.
- `static/`: interfaz conversacional e instalación como PWA.

Una escena generada es una propuesta. El historial original no se convierte
automáticamente en canon o conocimiento de un personaje. Las correcciones
requieren decisión explícita, quedan versionadas y admiten reversión. Los
reintentos recuperan respuestas ya guardadas. No se garantiza cero errores
semánticos: la prosa libre requiere revisión y la recuperación histórica es
literal y limitada. Las coincidencias entre símbolos no prueban una conexión.

## iPhone y servidor propio: pendiente de habilitación

Instalar la PWA no incorpora un modelo al iPhone. Para recibir respuestas con
el computador apagado hace falta un proveedor autorizado ejecutándose en el
servidor, credenciales persistentes protegidas y una prueba real desde el teléfono.
El flujo ChatGPT incluido es personal y local; no está habilitado como proveedor
remoto. `render.yaml` prepara alojamiento manual con PostgreSQL, no activa por sí
solo la narración del plan ChatGPT ni acredita una aprobación del producto.

La guía oficial para aplicaciones de código abierto en máquinas virtuales propias
describe autorizar localmente la misma herramienta/cuenta/espacio de trabajo,
transferir una sesión protegida, conservar un host propio y dejar la renovación
al servidor. No autoriza a compartir la cuenta entre usuarios. Aún falta adaptar
y verificar ese recorrido para esta edición, y confirmar que el alojamiento
elegido cumple los requisitos de persistencia y protección.

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

# Agente Fantasy

Asistente local para analizar tu equipo de LALIGA Fantasy. Reúne plantilla, mercado, actividad de la liga y datos de FutbolFantasy para ayudarte a valorar fichajes, ventas y alineaciones con criterios visibles.

> **Estado:** en desarrollo. El asesor recomienda; no realiza pujas, ventas, clausulazos ni cambios de alineación en el juego.

**Descripción corta para GitHub:** Asistente local para LALIGA Fantasy: analiza tu plantilla, el mercado y tus rivales, y combina precios y posibles alineaciones de FutbolFantasy para ofrecer recomendaciones explicables.

## Qué incluye

- **Panel web local** con resumen del equipo, actividad reciente, posición, mercado activo y búsqueda de jugadores, clubes y managers.
- **Constructor de plantilla** con formaciones, capitán, reservas, sugerencia de XI y vistas de precio, puntos y rentabilidad estimada. Los cambios del constructor se guardan como borrador local.
- **Mercado y radar de rivales** con precios, tendencias, cláusulas visibles y rangos orientativos de puja basados en operaciones observadas.
- **Ficha e historial de jugadores** con precios publicados por FutbolFantasy y observaciones locales recopiladas por la aplicación.
- **Partidos y posibles alineaciones** para consultar la probabilidad de titularidad y el contexto de la jornada.
- **Favoritos locales** para seguir jugadores y cambios de precio en este navegador. No se sincronizan con los favoritos de LALIGA Fantasy.
- **Configuración opcional de OpenRouter** desde la web. El asesor local sigue disponible sin configurar un modelo.

## Requisitos

- Windows 10/11 recomendado; el uso de Windows Credential Manager para proteger credenciales requiere un backend de `keyring` disponible.
- Python 3.11 o posterior.
- Una cuenta de LALIGA Fantasy para consultar los datos privados de tu liga. El acceso se realiza mediante el flujo OAuth local; no necesitas entregar tu contraseña a esta aplicación.
- Conexión a internet para consultar LALIGA Fantasy y FutbolFantasy.
- Opcional: una clave de OpenRouter si quieres usar el análisis con IA.

## Instalación y primer inicio

Abre PowerShell en la carpeta del proyecto:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
fantasy-advisor web
```

La aplicación abre un panel privado en `http://127.0.0.1:8765`. Desde la opción **Conectar** inicia sesión en la página oficial de LALIGA y selecciona tu liga. No compartas con nadie la URL de retorno OAuth que aparece durante el proceso.

Para actualizar primero los datos públicos de FutbolFantasy desde la terminal:

```powershell
fantasy-advisor sync --prices-only
```

Para detener el servidor, vuelve a la terminal y pulsa `Ctrl+C`.

## Comandos

| Comando | Función |
| --- | --- |
| `fantasy-advisor status` | Estado de conexión y sincronizaciones |
| `fantasy-advisor connect` | Conectar LALIGA Fantasy con OAuth local |
| `fantasy-advisor disconnect` | Eliminar la sesión local guardada |
| `fantasy-advisor sync` | Sincronizar precios y posibles alineaciones |
| `fantasy-advisor sync --prices-only` | Sincronizar solo precios |
| `fantasy-advisor sync --force` | Ignorar la caché local al sincronizar |
| `fantasy-advisor prices "nombre"` | Consultar precios e historial de un jugador |
| `fantasy-advisor league` | Consultar plantilla, saldo y rivales |
| `fantasy-advisor recommend` | Generar recomendaciones de solo lectura |
| `fantasy-advisor web` | Iniciar el panel web local |

Usa `fantasy-advisor --help` o `fantasy-advisor <comando> --help` para ver las opciones disponibles.

## Fuentes y tratamiento de datos

### LALIGA Fantasy

La aplicación consulta mediante OAuth la cuenta seleccionada, la plantilla, la alineación, el mercado, los managers, la actividad y el catálogo de jugadores. El cliente se basa en una API **no oficial y no documentada públicamente**; LALIGA puede cambiarla sin aviso.

### FutbolFantasy

El conector obtiene precios, cambios publicados y posibles alineaciones de las páginas públicas de FutbolFantasy. Respeta `robots.txt`, usa caché y conserva observaciones locales para construir un historial propio. El histórico local empieza en la primera sincronización; no puede reconstruir observaciones que nunca se guardaron.

La base de datos se guarda en:

```text
%LOCALAPPDATA%\LaligaFantasyAdvisor\advisor.sqlite3
```

Las credenciales OAuth y la clave opcional de OpenRouter se guardan en el almacén de credenciales del sistema mediante `keyring`; no se guardan en SQLite ni en este repositorio. Si OpenRouter está habilitado, la información seleccionada para el análisis se envía a ese proveedor. El token OAuth de LALIGA no se envía al modelo.

## Cómo interpretar las recomendaciones

- Los rangos de puja son orientativos y se calculan con operaciones observadas y señales de precio/rendimiento; no garantizan ganar una subasta.
- Las pujas de otros managers son secretas. La actividad disponible no revela necesariamente el importe ni el sentido de cada traspaso.
- El saldo real de otros managers no está disponible en los datos consultados.
- El coste de compra y el beneficio o pérdida pueden inferirse de la actividad; si no hay una operación atribuible con suficiente confianza, se mostrarán como desconocidos.
- Las posibles alineaciones y tendencias de valor pueden cambiar. Revisa la fecha de actualización y confirma los datos importantes en el juego.

## Seguridad y límites

- La web escucha solo en `127.0.0.1`; no la expongas directamente a internet ni a una red compartida sin añadir autenticación y protección adecuadas.
- Nunca guardes contraseñas, códigos OAuth, tokens o claves API en el código, README, capturas o incidencias públicas.
- Los borradores de alineación y favoritos de la web son locales y no modifican tu cuenta de LALIGA.
- Esta versión **no ejecuta acciones dentro del juego**. Las recomendaciones requieren revisión y confirmación del usuario en LALIGA Fantasy.
- Este proyecto es independiente y no está afiliado ni respaldado por LALIGA o FutbolFantasy. Revisa las condiciones de los servicios antes de utilizarlos.

## Desarrollo

Instalación editable:

```powershell
python -m pip install -e .
```

Ejecutar las pruebas del proyecto:

```powershell
python -m unittest discover -s tests -v
```

## Estructura

```text
src/fantasy_advisor/
├── advisor.py       # Reglas y recomendaciones
├── auth.py          # OAuth local y almacenamiento de credenciales
├── cli.py           # Comandos de terminal
├── decisions.py     # Cálculos de puja y rentabilidad
├── sources/         # Conectores de LALIGA Fantasy y FutbolFantasy
├── storage.py       # Historial y datos locales en SQLite
├── web.py           # Servidor HTTP local y rutas
└── web/             # Interfaz HTML, CSS y JavaScript
tests/               # Pruebas automatizadas
docs/                # Arquitectura y notas de fuentes de datos
```

## Licencia

No se ha definido todavía una licencia de distribución. Hasta que se añada una, no asumas que el código puede reutilizarse o redistribuirse.

# Fuentes y frescura de datos

## LALIGA Fantasy

El conector implementa OAuth Authorization Code + PKCE para el login local y consultas GET autenticadas a los endpoints observados en el cliente de referencia. No hay acciones de escritura en el módulo `sources/laliga.py`.

Consultas previstas: usuario, ligas, plantilla propia, alineación, mercado, managers de la liga, actividad paginada y catálogo de jugadores. El contrato no es público/documentado; la primera autenticación local debe comprobar qué datos permite leer la sesión actual.

Tokens: Windows Credential Manager mediante `keyring`. Nunca se escriben en SQLite ni en archivos del proyecto. La URL de retorno contiene un código temporal; se valida el parámetro `state` y se canjea localmente con el verificador PKCE.

## FutbolFantasy

- Mercado de LaLiga Fantasy: una fila por jugador con valor actual, valores publicados para 1/2/3/7/14/30 días, variación porcentual, tendencia y aceleración.
- Posibles alineaciones: páginas del índice y de equipos con probabilidad de titularidad, lesión, sanción y disponibilidad.
- `robots.txt` se consulta y respeta. El cliente limita el tamaño de respuesta, separa solicitudes por al menos 1,2 segundos y reintenta 429 respetando `Retry-After`.
- Se aplica una caché local de 30 minutos. El sitio no proporciona un histórico ilimitado en esta tabla; se conservan ventanas hasta 30 días y se guardan observaciones locales desde el primer uso.

## Comparación de importes de actividad

Los rangos de puja comparan importes visibles de actividad con precios diarios fechados obtenidos de LALIGA. Las observaciones de FutbolFantasy se guardan con su fuente identificada y no se usan como si fueran el precio histórico de la liga. Los registros antiguos cuyo origen no se puede verificar quedan marcados como desconocidos y no alimentan el cálculo.

La actividad no identifica de forma segura todas las compras, ventas ni pujas privadas. Por eso el cálculo describe importes observados por encima del valor de mercado, no un porcentaje exacto de pujas ganadoras. Cuando faltan comparables, el asesor no aplica un sobreprecio por defecto.

Un fallo de parseo no se convierte silenciosamente en una lista vacía válida: se informa para evitar crear una falsa recomendación con datos antiguos.

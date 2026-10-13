# Arquitectura inicial

```text
LALIGA Fantasy (lectura) ─┐
                          ├─> adaptadores ─> modelo normalizado ─> motor de recomendaciones ─> informe
FutbolFantasy (lectura) ──┘
```

## Reglas del diseño

- Los adaptadores obtienen y normalizan datos; no deciden ni ejecutan movimientos.
- El motor produce propuestas con razones, fuente, frescura y confianza.
- Una recomendación nunca se presenta como certeza cuando depende de una alineación probable o una estimación de valor.
- Los errores de autenticación, scraping, nombres sin correspondencia y datos antiguos quedan visibles en el informe.
- No habrá endpoints ni comandos de escritura contra LALIGA en la primera versión.

## Próxima decisión técnica

Antes de implementar autenticación, confirmar en una prueba local que el inicio de sesión actual permite consultar la liga del usuario y documentar exactamente qué datos devuelve. No se almacenarán credenciales en el repositorio ni se enviarán al modelo de lenguaje.

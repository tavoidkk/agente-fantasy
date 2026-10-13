from __future__ import annotations

import argparse
import secrets
import sys
import webbrowser
from datetime import datetime, timezone

from . import __version__, auth, config, storage
from .advisor import build_report
from .sources import futbolfantasy
from .sources.laliga import LaligaClient, LaligaError, league_id_and_team_id


def _money(value) -> str:
    if not isinstance(value, (int, float)):
        return "n/d"
    if abs(value) >= 1_000_000:
        return f"€{value / 1_000_000:,.2f} M"
    return f"€{value:,.0f}"


def _sync_prices(force: bool = False) -> tuple[int, str, bool]:
    last = storage.latest_price_sync()
    if last and not force:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if age < config.FF_CACHE_SECONDS:
                return len(storage.latest_ff_players()), last, True
        except ValueError:
            pass
    players, fetched_at = futbolfantasy.fetch_market()
    count = storage.save_ff_players(players, fetched_at)
    return count, fetched_at, False


def _sync_lineups(force: bool = False) -> tuple[int, str, bool]:
    last = storage.latest_lineup_sync()
    if last and not force:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if age < config.FF_CACHE_SECONDS and storage.latest_ff_fixtures() and any(row.get("starter") for row in storage.latest_ff_lineups()):
                return len(storage.latest_ff_lineups()), last, True
        except ValueError:
            pass
    players, fixtures, fetched_at = futbolfantasy.fetch_probable_lineups_and_fixtures()
    count = storage.save_ff_lineups(players, fetched_at)
    storage.save_ff_fixtures(fixtures, fetched_at)
    return count, fetched_at, False


def command_status(_args) -> int:
    try:
        auth.load_tokens()
        connected = True
    except auth.AuthError:
        connected = False
    selected = storage.get_setting("selected_league_id")
    print(f"LALIGA Fantasy: {'conectado' if connected else 'desconectado'}")
    print(f"Liga seleccionada: {selected or 'ninguna'}")
    price_sync = storage.latest_price_sync()
    lineup_sync = storage.latest_lineup_sync()
    print(f"FutbolFantasy precios: {price_sync or 'sin sincronizar'}")
    print(f"FutbolFantasy alineaciones: {lineup_sync or 'sin sincronizar'}")
    print("Modo: solo lectura")
    return 0


def command_connect(_args) -> int:
    # Check that an OS-backed credential manager is available before opening sign-in.
    auth._keyring()
    verifier, challenge = auth.make_pkce()
    expected_state = secrets.token_urlsafe(24)
    login_url = auth.authorization_url(challenge, expected_state)
    print("Se abrirá el inicio de sesión de LALIGA. En esa página elige Apple si esa es tu cuenta.")
    print("No escribas tu contraseña aquí; inicia sesión únicamente en la página abierta por LALIGA.")
    opened = webbrowser.open(login_url)
    if not opened:
        print("No pude abrir el navegador automáticamente. Abre esta URL:")
        print(login_url)
    print(
        "\nCuando termine, pega aquí la URL completa authredirect://com.lfp.laligafantasy"
        " que aparezca en el retorno del navegador."
    )
    print("Si el navegador dice que no puede abrirla, copia esa solicitud de retorno desde su panel Network.")
    callback_url = input("URL de retorno (se procesa localmente y no se guarda): ").strip()
    tokens = auth.redeem_authorization_code(callback_url, expected_state, verifier)
    client = LaligaClient()
    client.me()  # validate the token with a read-only request
    leagues = client.leagues()
    if not leagues:
        raise LaligaError("La cuenta conectó, pero no tiene ligas Fantasy disponibles.")
    print("\nLigas disponibles:")
    for index, league in enumerate(leagues, 1):
        name = league.get("leagueName") or league.get("name") or f"Liga {league.get('id', '?')}"
        print(f"  {index}. {name}")
    if len(leagues) == 1:
        selected = leagues[0]
    else:
        choice = input("Elige el número de la liga que quieres analizar: ").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(leagues):
            raise LaligaError("Selección de liga inválida. Vuelve a ejecutar connect.")
        selected = leagues[int(choice) - 1]
    league_id, team_id = league_id_and_team_id(leagues, str(selected.get("id")))
    storage.set_setting("selected_league_id", league_id)
    storage.set_setting("selected_team_id", team_id)
    print("\nCuenta conectada y liga seleccionada. Solo se han hecho consultas de lectura.")
    print("Los tokens están en el almacén de credenciales de Windows, no en el repositorio.")
    return 0


def command_disconnect(_args) -> int:
    auth.delete_tokens()
    storage.delete_setting("selected_league_id")
    storage.delete_setting("selected_team_id")
    print("Tokens locales eliminados y liga desconectada.")
    return 0


def command_web(args) -> int:
    from .web import run_server
    run_server(port=args.port, open_browser=not args.no_browser)
    return 0


def command_sync(args) -> int:
    price_count, price_time, price_cached = _sync_prices(force=args.force)
    state = "(caché local)" if price_cached else "(guardado en historial local)"
    print(f"FutbolFantasy: {price_count} jugadores de mercado {state}; {price_time}.")
    if not args.prices_only:
        lineup_count, lineup_time, lineup_cached = _sync_lineups(force=args.force)
        state = "(caché local)" if lineup_cached else "(guardado localmente)"
        print(f"Posibles alineaciones: {lineup_count} jugadores {state}; {lineup_time}.")
    return 0


def command_prices(args) -> int:
    players = storage.latest_ff_players()
    if not players:
        print("Aún no hay precios guardados. Ejecuta `fantasy-advisor sync` primero.")
        return 0
    if not args.player:
        field = f"pct_{args.window}d"
        ranked = [p for p in players if p.get(field) is not None]
        ranked.sort(key=lambda player: player[field], reverse=True)
        print(f"Mayores variaciones FutbolFantasy en {args.window} días:")
        for player in ranked[:args.limit]:
            print(f"{player['name']:<25} {_money(player['current_value']):>13} "
                  f"{player[field]:+6.1f}%  (valor {args.window}d: {_money(player.get(f'value_{args.window}d'))})")
        return 0

    try:
        player = storage.find_ff_player(args.player)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not player:
        print(f"No encontré a {args.player!r}. Prueba con nombre y apellido.")
        return 1
    print(f"{player['name']} · {player.get('position') or 'posición n/d'}")
    print(f"Valor actual: {_money(player.get('current_value'))} · fuente: FutbolFantasy · actualizado: {player['fetched_at']}")
    for days in (1, 2, 3, 7, 14, 30):
        print(f"  {days:>2}d: {_money(player.get(f'value_{days}d')):>13}  "
              f"variación {_format_pct(player.get(f'pct_{days}d'))}")
    history = storage.price_history(player["player_id"], args.history_limit)
    print(f"\nObservaciones guardadas localmente: {len(history)}")
    for snapshot in history:
        print(f"  {snapshot['observed_at']}  {_money(snapshot.get('current_value'))}")
    return 0


def _format_pct(value) -> str:
    return "n/d" if value is None else f"{value:+.2f}%"


def _league_context(client: LaligaClient) -> tuple[str, str, dict, list[dict]]:
    league_id = storage.get_setting("selected_league_id")
    if not league_id:
        raise LaligaError("No hay liga seleccionada. Ejecuta `fantasy-advisor connect`.")
    leagues = client.leagues()
    league_id, team_id = league_id_and_team_id(leagues, league_id)
    league = next(item for item in leagues if str(item.get("id")) == league_id)
    team = client.team(league_id, team_id)
    return league_id, team_id, team, leagues


def command_league(_args) -> int:
    client = LaligaClient()
    league_id, _team_id, team, leagues = _league_context(client)
    selected_league = next((item for item in leagues if str(item.get("id")) == league_id), {})
    league_name = (selected_league.get("leagueName") or selected_league.get("name")
                   or team.get("leagueName") or team.get("name") or f"Liga {league_id}")
    print(f"Liga: {league_name}")
    print(f"Saldo: {_money(team.get('teamMoney'))}")
    roster = team.get("players") or []
    player_count = sum((player.get("playerMaster") or {}).get("positionId") in (1, 2, 3, 4)
                       for player in roster)
    coach_count = sum((player.get("playerMaster") or {}).get("positionId") == 5
                      for player in roster)
    print(f"Plantilla: {player_count} jugadores" + (f" y {coach_count} entrenadores" if coach_count else ""))
    for player in roster:
        master = player.get("playerMaster") or {}
        name = master.get("nickname") or master.get("name") or "Jugador sin nombre"
        position = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL", 5: "ENT"}.get(master.get("positionId"), "?")
        print(f"  {position:>3}  {name:<24} {_money(master.get('marketValue')):>13}  "
              f"estado: {master.get('playerStatus') or 'n/d'}")
    try:
        league_teams = client.league_teams(league_id)
        if isinstance(league_teams, list):
            print(f"\nManagers visibles en la liga: {len(league_teams)}")
            for rival in league_teams:
                if str(rival.get("id")) == str(team.get("id")):
                    continue
                print(f"  {rival.get('manager', {}).get('managerName') or rival.get('name') or 'Rival'} · "
                      f"saldo {_money(rival.get('teamMoney'))}")
    except LaligaError as exc:
        print(f"Aviso: no pude leer todos los equipos de la liga: {exc}")
    return 0


def command_recommend(_args) -> int:
    client = LaligaClient()
    league_id, _team_id, team, _leagues = _league_context(client)
    price_count, price_time, price_cached = _sync_prices()
    lineup_count, lineup_time, lineup_cached = _sync_lineups()
    ff_players = storage.latest_ff_players()
    probable = storage.latest_ff_lineups()
    market = client.market(league_id)
    report = build_report(team, ff_players, probable, market if isinstance(market, list) else [])

    print(f"RECOMENDACIONES · {report['created_at']}")
    print(f"Saldo: {_money(report['balance'])} · jugadores propios: {len(report['roster'])}")
    print(f"FutbolFantasy: {price_count} precios ({'caché' if price_cached else 'actualizado'} {price_time}); "
          f"{lineup_count} alineaciones ({'caché' if lineup_cached else 'actualizadas'} {lineup_time})")
    lineup = report.get("lineup")
    if lineup and lineup.get("formation"):
        d, m, f = lineup["formation"]
        print(f"\nXI sugerido por probabilidad de titularidad · {d}-{m}-{f}:")
        for item in lineup["players"]:
            info = "?% (sin dato)" if item["probability"] is None else f"{item['probability']}%"
            if not item["available"]:
                info += " · no disponible/duda"
            print(f"  {item['position']:<10} {item['name']:<24} {info}")
        if not lineup["complete"]:
            print("  AVISO: plantilla incompleta para una formación estándar.")
    else:
        print("\nNo hay datos suficientes para proponer alineación; sincroniza FutbolFantasy.")

    if report["rising"]:
        print("\nJugadores propios con subida >=5% en 7 días (señal para mantener/vigilar):")
        for item in sorted(report["rising"], key=lambda row: row["ff"]["pct_7d"], reverse=True):
            print(f"  {item['name']:<24} {_money(item['ff'].get('current_value'))} · {_format_pct(item['ff'].get('pct_7d'))}")
    if report["falling"]:
        print("\nJugadores propios con bajada <=-5% en 7 días (revisar; no vender automáticamente):")
        for item in sorted(report["falling"], key=lambda row: row["ff"]["pct_7d"]):
            print(f"  {item['name']:<24} {_money(item['ff'].get('current_value'))} · {_format_pct(item['ff'].get('pct_7d'))}")
    if report["market_opportunities"]:
        print("\nJugadores del mercado con precio al menos 5% bajo el valor FF (revisar puja):")
        for item in report["market_opportunities"]:
            print(f"  {item['name']:<24} pide {_money(item['asking_price'])}; "
                  f"FF {_money(item['ff_value'])}; diferencia {item['gap_pct']:.1f}%")
    no_match = [item["name"] for item in report["roster"] if not item["ff"]]
    if no_match:
        print(f"\nSin correspondencia FutbolFantasy ({len(no_match)}): {', '.join(no_match[:12])}")
    print("\nNotas:")
    for warning in report["warnings"]:
        print(f"  - {warning}")
    print("\nEste informe solo recomienda; no ejecuta acciones en LALIGA Fantasy.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fantasy-advisor",
        description="Asistente de solo lectura para LALIGA Fantasy.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="muestra el estado de las conexiones y sincronizaciones")
    commands.add_parser("connect", help="conecta tu cuenta de LALIGA Fantasy con OAuth local")
    commands.add_parser("disconnect", help="borra el token local y desconecta la cuenta")

    sync = commands.add_parser("sync", help="sincroniza precios e indicios de titularidad de FutbolFantasy")
    sync.add_argument("--prices-only", action="store_true", help="sincroniza solo la tabla de precios")
    sync.add_argument("--force", action="store_true", help="omite la caché local de 30 minutos")

    prices = commands.add_parser("prices", help="consulta precios y el historial local de un jugador")
    prices.add_argument("player", nargs="?", help="nombre del jugador; sin nombre lista mayores subidas")
    prices.add_argument("--window", type=int, choices=(1, 2, 3, 7, 14, 30), default=7)
    prices.add_argument("--limit", type=int, default=20)
    prices.add_argument("--history-limit", type=int, default=30)

    commands.add_parser("league", help="lee tu plantilla, saldo y rivales")
    commands.add_parser("recommend", help="genera recomendaciones de solo lectura")
    web = commands.add_parser("web", help="abre el panel local de recomendaciones y plantilla")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--no-browser", action="store_true", help="no abre el navegador automáticamente")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "status": command_status,
        "connect": command_connect,
        "disconnect": command_disconnect,
        "sync": command_sync,
        "prices": command_prices,
        "league": command_league,
        "recommend": command_recommend,
        "web": command_web,
    }
    try:
        return handlers[args.command](args)
    except (auth.AuthError, LaligaError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nOperación cancelada; no se ejecutó ninguna acción en la liga.", file=sys.stderr)
        return 130

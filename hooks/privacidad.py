#!/usr/bin/env python3
"""Hook PreToolUse de blindaje: frena identificadores personales antes de que salgan.

Lee la llamada a herramienta (JSON por stdin) y la bloquea (exit 2) si:
  - contiene un identificador [siempre] (p. ej. tu correo) en cualquier herramienta;
  - manda un identificador [externo] (clientes, proyectos, tu nombre) a terceros:
    WebFetch/WebSearch, herramientas MCP, comandos de red en Bash o cabeceras
    User-Agent/Referer escritas en archivos;
  - intenta modificar los archivos de blindaje (la lista, el hook, la política);
  - intenta saltarse los hooks de git con --no-verify.

Los dominios de [propios] se quitan antes de comparar (deploy a tu servidor, etc.).
git y gh no cuentan como tráfico externo: el código va a tus repos privados.

Uso: privacidad.py --lista RUTA [--proteger RUTA ...] [--estricto]
--estricto: si el hook falla por un error interno, bloquea en vez de dejar pasar.
"""
import json
import os
import re
import sys
from urllib.parse import unquote

SECCIONES = ("siempre", "externo", "propios")

RED_BASH = re.compile(
    r"\b(curl|wget|aria2c|httpie|xh|nc|ncat|telnet|ssh|scp|sftp|rsync|ftp|lynx|w3m)\b"
    r"|https?://|\brequests\.|\burllib|\bhttpx\b|\bfetch\(|\baxios\b|user.?agent",
    re.I,
)
CABECERA = re.compile(r"user.?agent|referer|['\"]from['\"]\s*:", re.I)
RUTA_LOCAL = re.compile(r"(?<![\w:/.~-])(?:~|\.{1,2})?/[^\s'\"`;|&()<>]*")
SOLO_LECTURA = re.compile(r"^\s*(cat|ls|head|tail|less|grep|rg|diff|stat|file|wc|md5sum|sha256sum)\b")
ESCRITURA_SHELL = re.compile(r">|\btee\b|\b(rm|mv|cp|ln|chmod|chown|truncate|install|dd)\b|sed\s+-i|perl\s+-i")


def cargar_lista(ruta):
    secciones = {s: [] for s in SECCIONES}
    actual = None
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            m = re.fullmatch(r"\[(\w+)\]", linea)
            if m:
                actual = m.group(1).lower()
            elif actual in secciones:
                secciones[actual].append(linea)
    return secciones


def patron(identificador):
    """'Mi Proyecto' casa con 'mi-proyecto', 'MiProyecto', etc."""
    palabras = [re.escape(p) for p in re.split(r"[\W_]+", identificador) if p]
    if not palabras:
        return None
    return re.compile(r"(?<![a-z0-9])" + r"[\W_]*".join(palabras) + r"(?![a-z0-9])", re.I)


def buscar(texto, identificadores):
    for variante in {texto, unquote(texto)}:
        for ident in identificadores:
            p = patron(ident)
            if p and p.search(variante):
                return ident
    return None


def quitar_propios(texto, dominios):
    for d in dominios:
        texto = re.sub(r"[\w.-]*" + re.escape(d), " ", texto, flags=re.I)
    return texto


def bloquear(motivo, lista):
    print(
        f"blindaje: bloqueado — {motivo}. No lo reintentes por otra vía; "
        f"si de verdad hace falta, explícaselo al usuario (solo él puede cambiar {lista}).",
        file=sys.stderr,
    )
    sys.exit(2)


def revisar(evento, lista_ruta, protegidos):
    herramienta = evento.get("tool_name", "")
    entrada = evento.get("tool_input") or {}
    todo = json.dumps(entrada, ensure_ascii=False)

    # 1. Autoprotección: nadie (ni Claude) toca la lista, el hook ni la política.
    ruta = str(entrada.get("file_path") or entrada.get("notebook_path") or "")
    if herramienta in ("Write", "Edit", "MultiEdit", "NotebookEdit") and any(
        ruta.startswith(p) for p in protegidos
    ):
        bloquear(f"{ruta} es un archivo de protección", lista_ruta)
    comando = str(entrada.get("command") or "") if herramienta == "Bash" else ""
    if comando:
        if any(p in comando or p.replace(_home(), "~", 1) in comando for p in protegidos):
            if not SOLO_LECTURA.search(comando) or ESCRITURA_SHELL.search(comando):
                bloquear("el comando modifica archivos de protección", lista_ruta)
        if re.search(r"\bgit\b.*--no-verify", comando):
            bloquear("saltarse los hooks de git (--no-verify)", lista_ruta)

    try:
        lista = cargar_lista(lista_ruta)
    except FileNotFoundError:
        return  # sin lista no hay nada que comparar; 'instalar.sh --check' lo avisa

    # 2. [siempre]: en ninguna herramienta.
    hallado = buscar(todo, lista["siempre"])
    if hallado:
        bloquear(f"contiene un identificador personal protegido ('{hallado}')", lista_ruta)

    # 3. [externo]: solo cuando sale hacia terceros.
    externos, propios = lista["externo"], lista["propios"]
    if not externos:
        return
    if herramienta == "WebFetch":
        hallado = buscar(quitar_propios(str(entrada.get("url", "")), propios), externos)
    elif herramienta == "WebSearch" or herramienta.startswith("mcp__"):
        hallado = buscar(quitar_propios(todo, propios), externos)
    elif comando:
        limpio = RUTA_LOCAL.sub(" ", quitar_propios(comando, propios))
        hallado = buscar(limpio, externos) if RED_BASH.search(limpio) else None
    elif herramienta in ("Write", "Edit", "MultiEdit"):
        texto = "\n".join(
            str(entrada.get(k, "")) for k in ("content", "new_string")
        ) + "\n" + "\n".join(str(e.get("new_string", "")) for e in entrada.get("edits") or [])
        lineas = [quitar_propios(l, propios) for l in texto.splitlines() if CABECERA.search(l)]
        hallado = buscar("\n".join(lineas), externos)
    else:
        hallado = None
    if hallado:
        bloquear(
            f"enviaría '{hallado}' a un servicio externo; usa un identificador neutro "
            f"(p. ej. User-Agent 'image-fetch/1.0') y quita nombres del pedido",
            lista_ruta,
        )


def _home():
    return os.path.expanduser("~")


def main():
    args = sys.argv[1:]
    lista, protegidos, estricto = None, [], "--estricto" in args
    for i, a in enumerate(args):
        if a == "--lista":
            lista = args[i + 1]
        elif a == "--proteger":
            protegidos.append(args[i + 1])
    try:
        evento = json.load(sys.stdin)
        revisar(evento, lista or "", protegidos)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        if estricto:
            print(f"blindaje: error interno del hook ({e}); bloqueado por precaución", file=sys.stderr)
            sys.exit(2)
        print(f"blindaje: error interno del hook ({e})", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

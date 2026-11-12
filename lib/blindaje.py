#!/usr/bin/env python3
"""Núcleo de blindaje. Lo llama instalar.sh; también se puede usar directo:

  blindaje.py aplicar --perfil normal [--permitir artifacts,docs]
  blindaje.py aplicar --perfil maxima            (como root)
  blindaje.py revisar
  blindaje.py desinstalar --perfil normal|maxima
  blindaje.py opciones                           (para el menú)
  blindaje.py lista                              (crea la lista de identificadores)

Perfil normal: combina la regla con ~/.claude/settings.json y anota en
~/.config/blindaje/estado.json qué agregó, para poder cambiar opciones o
desinstalar sin tocar lo demás. Nunca reemplaza el archivo entero.

Perfil máxima: escribe /etc/claude-code/managed-settings.d/50-blindaje.json
(todo bloqueado). Las reglas deny y los hooks de ahí se suman a los de cualquier
otro nivel y ningún proyecto ni el usuario puede quitarlos.

Para probar sin tocar el sistema: BLINDAJE_HOME=/tmp/x BLINDAJE_ETC=/tmp/y.
"""
import argparse
import copy
import hashlib
import json
import os
import pwd
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUSENTE = "__ausente__"
MARCA_MD = ("<!-- blindaje:inicio -->", "<!-- blindaje:fin -->")
MARCA_IGNORE = ("# blindaje:inicio", "# blindaje:fin")
HOOKS_GIT = [
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit",
    "pre-merge-commit", "prepare-commit-msg", "commit-msg", "post-commit",
    "pre-rebase", "post-checkout", "post-merge", "pre-push", "pre-auto-gc",
    "post-rewrite", "push-to-checkout",
]


# ── rutas ──────────────────────────────────────────────────────────────────

def usuario():
    """Dueño real: con sudo, quien lo llamó; si no, el usuario actual."""
    nombre = os.environ.get("SUDO_USER") if os.geteuid() == 0 else None
    return pwd.getpwnam(nombre) if nombre else pwd.getpwuid(os.getuid())


def rutas():
    u = usuario()
    home = os.environ.get("BLINDAJE_HOME") or u.pw_dir
    etc = os.environ.get("BLINDAJE_ETC") or "/etc/claude-code"
    return {
        "home": home,
        "settings": os.path.join(home, ".claude", "settings.json"),
        "claude_md": os.path.join(home, ".claude", "CLAUDE.md"),
        "config": os.path.join(home, ".config", "blindaje"),
        "lista": os.path.join(home, ".config", "blindaje", "identificadores"),
        "estado": os.path.join(home, ".config", "blindaje", "estado.json"),
        "datos": os.path.join(home, ".local", "share", "blindaje"),
        "etc": etc,
        "etc_dir": os.path.join(etc, "blindaje"),
        "etc_lista": os.path.join(etc, "blindaje", "identificadores"),
        "etc_politica": os.path.join(etc, "managed-settings.d", "50-blindaje.json"),
        "etc_managed": os.path.join(etc, "managed-settings.json"),
        "etc_claude_md": os.path.join(etc, "CLAUDE.md"),
    }


# ── utilidades ─────────────────────────────────────────────────────────────

def leer_json(ruta, defecto=None):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(defecto)


def escribir(ruta, texto, modo=0o644, dueno_usuario=True):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".blindaje-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    os.chmod(tmp, modo)
    os.replace(tmp, ruta)
    if dueno_usuario:
        dar_al_usuario(ruta)


def escribir_json(ruta, datos, **kw):
    escribir(ruta, json.dumps(datos, indent=2, ensure_ascii=False) + "\n", **kw)


def dar_al_usuario(ruta):
    """Si corre como root dentro del home del usuario, devuélvele el archivo."""
    if os.geteuid() != 0 or os.environ.get("BLINDAJE_HOME"):
        return
    u = usuario()
    if ruta.startswith(u.pw_dir):
        p = ruta
        while p.startswith(u.pw_dir) and p != u.pw_dir:
            try:
                st = os.stat(p)
                if st.st_uid == 0:
                    os.chown(p, u.pw_uid, u.pw_gid)
            except FileNotFoundError:
                pass
            p = os.path.dirname(p)


def respaldar(ruta):
    if os.path.exists(ruta):
        destino = os.path.join(rutas()["config"], "respaldos")
        os.makedirs(destino, exist_ok=True)
        nombre = ruta.strip("/").replace("/", "__")
        primero = os.path.join(destino, nombre + ".original")
        if not os.path.exists(primero):  # el original de antes de blindaje, una sola vez
            shutil.copy2(ruta, primero)
            dar_al_usuario(primero)


def sha(ruta):
    try:
        with open(ruta, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return None


def git_global(*args):
    r = rutas()
    env = dict(os.environ, HOME=r["home"], XDG_CONFIG_HOME=os.path.join(r["home"], ".config"))
    cmd = ["git", "config", "--global", *args]
    if os.geteuid() == 0 and os.environ.get("SUDO_USER") and not os.environ.get("BLINDAJE_HOME"):
        cmd = ["sudo", "-u", usuario().pw_name, "env", f"HOME={r['home']}", *cmd]
    return subprocess.run(cmd, env=env, capture_output=True, text=True, check=False).stdout.strip()


def bloque(texto, contenido, marcas):
    """Reemplaza (o agrega) el bloque entre marcas; contenido None lo quita."""
    ini, fin = marcas
    if ini in texto and fin in texto:
        antes, resto = texto.split(ini, 1)
        despues = resto.split(fin, 1)[1]
        texto = antes.rstrip("\n") + ("\n" if antes.strip() else "") + despues.lstrip("\n")
    if contenido is None:
        return texto
    nuevo = f"{ini}\n{contenido.rstrip()}\n{fin}\n"
    return (texto.rstrip("\n") + "\n\n" if texto.strip() else "") + nuevo


def editar_bloque(ruta, contenido, marcas, **kw):
    actual = open(ruta, encoding="utf-8").read() if os.path.exists(ruta) else ""
    nuevo = bloque(actual, contenido, marcas)
    if nuevo != actual:
        if not nuevo.strip() and contenido is None:
            os.remove(ruta)
        else:
            escribir(ruta, nuevo, **kw)


# ── reglas ─────────────────────────────────────────────────────────────────

def opciones():
    return leer_json(os.path.join(REPO, "reglas", "opciones.json"))


def fusionar(destino, fragmento):
    for k, v in fragmento.items():
        if isinstance(v, dict) and isinstance(destino.get(k), dict):
            fusionar(destino[k], v)
        elif isinstance(v, list) and isinstance(destino.get(k), list):
            destino[k] += [x for x in v if x not in destino[k]]
        else:
            destino[k] = copy.deepcopy(v)
    return destino


def comando_hook(dir_hook, lista, protegidos, estricto):
    partes = ["python3", "-I", os.path.join(dir_hook, "privacidad.py"), "--lista", lista]
    for p in protegidos:
        partes += ["--proteger", p]
    if estricto:
        partes.append("--estricto")
    return " ".join(f"'{p}'" if " " in p else p for p in partes)


def regla(bloqueadas, dir_hook, lista, protegidos, estricto):
    base = leer_json(os.path.join(REPO, "reglas", "base.json"))
    h = base["hooks"]["PreToolUse"][0]["hooks"][0]
    h["command"] = comando_hook(dir_hook, lista, protegidos, estricto)
    if estricto:
        h["onFailure"] = "block"
    ops = opciones()
    for clave in bloqueadas:
        fusionar(base, ops[clave]["bloqueo"])
    return base


# ── combinar con estado (perfil normal) ────────────────────────────────────

def aplanar(fragmento, prefijo=()):
    for k, v in fragmento.items():
        ruta = prefijo + (k,)
        if isinstance(v, dict):
            yield from aplanar(v, ruta)
        else:
            yield ruta, v


def obtener(d, ruta):
    for k in ruta:
        if not isinstance(d, dict) or k not in d:
            return AUSENTE
        d = d[k]
    return d


def poner(d, ruta, valor):
    for k in ruta[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[ruta[-1]] = valor


def quitar(d, ruta):
    padres = []
    for k in ruta[:-1]:
        if not isinstance(d.get(k), dict):
            return
        padres.append((d, k))
        d = d[k]
    d.pop(ruta[-1], None)
    for padre, k in reversed(padres):  # limpia contenedores vacíos que quedaron
        if padre[k] == {}:
            del padre[k]


def aplicar_con_estado(settings, fragmento):
    cambios = []
    for ruta, valor in aplanar(fragmento):
        actual = obtener(settings, ruta)
        if isinstance(valor, list):
            base = actual if isinstance(actual, list) else []
            nuevos = [x for x in valor if x not in base]
            if nuevos:
                poner(settings, ruta, base + nuevos)
                cambios.append({"ruta": list(ruta), "lista": nuevos})
        elif actual != valor:
            poner(settings, ruta, copy.deepcopy(valor))
            cambios.append({"ruta": list(ruta), "valor": valor, "antes": actual})
    return cambios


def revertir(settings, cambios):
    for c in reversed(cambios):
        ruta = tuple(c["ruta"])
        actual = obtener(settings, ruta)
        if "lista" in c:
            if isinstance(actual, list):
                resto = [x for x in actual if x not in c["lista"]]
                if resto:
                    poner(settings, ruta, resto)
                else:
                    quitar(settings, ruta)
        elif actual == c["valor"]:  # si el usuario lo cambió después, se respeta
            if c["antes"] == AUSENTE:
                quitar(settings, ruta)
            else:
                poner(settings, ruta, c["antes"])


# ── lista de identificadores ───────────────────────────────────────────────

def crear_lista(ruta):
    print("\nLista de identificadores (no va al repo; se guarda en " + ruta + ").")
    print("Escribe uno por línea; línea vacía para pasar a la siguiente sección.\n")
    secciones = [
        ("siempre", "Correos u otros datos que NUNCA deben aparecer (comandos, archivos, web)"),
        ("externo", "Tu nombre, clientes y proyectos que no deben salir hacia terceros"),
        ("propios", "Tus dominios, donde sí pueden aparecer (deploy, staging)"),
    ]
    plantilla = open(os.path.join(REPO, "identificadores.example"), encoding="utf-8").read()
    cabecera = "\n".join(l for l in plantilla.splitlines() if l.startswith("#") and "[" not in l)
    salida = [cabecera.split("\n\n")[0], ""]
    for clave, ayuda in secciones:
        print(f"[{clave}] {ayuda}:")
        salida.append(f"[{clave}]")
        while True:
            valor = input("  > ").strip()
            if not valor:
                break
            salida.append(valor)
        salida.append("")
    escribir(ruta, "\n".join(salida), modo=0o600)
    print("Guardada. Puedes editarla después a mano (tú, no Claude).")


def contar_lista(ruta):
    try:
        conteo, sec = {}, None
        for linea in open(ruta, encoding="utf-8"):
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            if linea.startswith("[") and linea.endswith("]"):
                sec = linea[1:-1]
                conteo.setdefault(sec, 0)
            elif sec:
                conteo[sec] = conteo.get(sec, 0) + 1
        return conteo
    except (FileNotFoundError, PermissionError):
        return None


# ── instalar piezas comunes ────────────────────────────────────────────────

def instalar_archivos(destino, como_root):
    os.makedirs(destino, exist_ok=True)
    hooks_git = os.path.join(destino, "git-hooks")
    os.makedirs(hooks_git, exist_ok=True)
    for origen, final in [
        ("hooks/privacidad.py", os.path.join(destino, "privacidad.py")),
        ("git/pre-commit.py", os.path.join(hooks_git, "pre-commit.py")),
        ("git/despachador", os.path.join(hooks_git, "despachador")),
    ]:
        escribir(final, open(os.path.join(REPO, origen), encoding="utf-8").read(),
                 modo=0o755, dueno_usuario=not como_root)
    for nombre in HOOKS_GIT:
        enlace = os.path.join(hooks_git, nombre)
        if os.path.lexists(enlace):
            os.remove(enlace)
        os.symlink("despachador", enlace)
    if not como_root:
        dar_al_usuario(hooks_git)
    return hooks_git


def configurar_git(hooks_git):
    actual = git_global("--get", "core.hooksPath")
    if actual and "blindaje" not in actual:
        print(f"  ! core.hooksPath ya apunta a {actual}; no lo toco (el pre-commit no queda activo)")
    else:
        git_global("core.hooksPath", hooks_git)
    ignore = git_global("--get", "core.excludesfile") or os.path.join(rutas()["home"], ".config", "git", "ignore")
    ignore = os.path.expanduser(ignore.replace("~", rutas()["home"], 1))
    respaldar(ignore)
    editar_bloque(ignore, open(os.path.join(REPO, "git", "ignore"), encoding="utf-8").read(), MARCA_IGNORE)


def quitar_git(hooks_git):
    if git_global("--get", "core.hooksPath") == hooks_git:
        git_global("--unset", "core.hooksPath")
    ignore = git_global("--get", "core.excludesfile") or os.path.join(rutas()["home"], ".config", "git", "ignore")
    editar_bloque(os.path.expanduser(ignore), None, MARCA_IGNORE)


def claude_md():
    return open(os.path.join(REPO, "claude", "CLAUDE.md"), encoding="utf-8").read()


# ── perfiles ───────────────────────────────────────────────────────────────

def aplicar_normal(permitidas):
    r = rutas()
    ops = opciones()
    desconocidas = set(permitidas) - set(ops)
    if desconocidas:
        sys.exit(f"Opciones desconocidas: {', '.join(sorted(desconocidas))}. Válidas: {', '.join(ops)}")
    if not os.path.exists(r["lista"]):
        crear_lista(r["lista"])

    bloqueadas = [k for k in ops if k not in permitidas]
    instalar_archivos(r["datos"], como_root=False)
    fragmento = regla(bloqueadas, r["datos"], r["lista"], [r["datos"], r["config"]], estricto=False)

    respaldar(r["settings"])
    settings = leer_json(r["settings"], {})
    estado = leer_json(r["estado"], {})
    revertir(settings, estado.get("cambios", []))  # quita lo de la vez anterior
    cambios = aplicar_con_estado(settings, fragmento)
    escribir_json(r["settings"], settings)
    escribir_json(r["estado"], {"perfil": "normal", "permitidas": sorted(permitidas), "cambios": cambios}, modo=0o600)

    respaldar(r["claude_md"])
    editar_bloque(r["claude_md"], claude_md(), MARCA_MD)
    configurar_git(os.path.join(r["datos"], "git-hooks"))

    print("\nSeguridad normal aplicada en", r["settings"])
    for k in ops:
        print(f"  {'permitido' if k in permitidas else 'bloqueado'}  {ops[k]['titulo']}")
    print("Abre una sesión nueva de Claude Code para que tome los cambios.")


def aplicar_maxima():
    r = rutas()
    if os.geteuid() != 0 and not os.environ.get("BLINDAJE_ETC"):
        sys.exit("La máxima privacidad escribe en /etc/claude-code: corre con sudo.")
    if not os.path.exists(r["etc_lista"]):
        if not os.path.exists(r["lista"]):
            crear_lista(r["lista"])
        os.makedirs(r["etc_dir"], exist_ok=True)
        shutil.copy2(r["lista"], r["etc_lista"])
    os.chmod(r["etc_lista"], 0o640)
    if os.geteuid() == 0 and not os.environ.get("BLINDAJE_ETC"):
        os.chown(r["etc_lista"], 0, usuario().pw_gid)  # el usuario la lee (pre-commit), solo root la cambia

    instalar_archivos(r["etc_dir"], como_root=True)
    politica = regla(list(opciones()), r["etc_dir"], r["etc_lista"],
                     [r["etc"], r["datos"], r["config"]], estricto=True)
    escribir_json(r["etc_politica"], politica, dueno_usuario=False)
    if not os.path.exists(r["etc_managed"]):
        escribir_json(r["etc_managed"], {}, dueno_usuario=False)
    editar_bloque(r["etc_claude_md"], claude_md(), MARCA_MD, dueno_usuario=False)
    configurar_git(os.path.join(r["etc_dir"], "git-hooks"))

    print("\nMáxima privacidad aplicada en", r["etc_politica"])
    print("Todo bloqueado; ningún proyecto ni usuario puede reactivarlo (solo root, quitando el archivo).")
    print(f"La lista ahora vive en {r['etc_lista']}: para cambiarla, sudo.")
    print("Abre una sesión nueva de Claude Code y revisa /status (Setting sources).")


def desinstalar(perfil):
    r = rutas()
    if perfil == "normal":
        estado = leer_json(r["estado"])
        if not estado:
            print("El perfil normal no está instalado.")
            return
        settings = leer_json(r["settings"], {})
        revertir(settings, estado.get("cambios", []))
        escribir_json(r["settings"], settings)
        os.remove(r["estado"])
        editar_bloque(r["claude_md"], None, MARCA_MD)
        quitar_git(os.path.join(r["datos"], "git-hooks"))
        shutil.rmtree(r["datos"], ignore_errors=True)
        print("Perfil normal quitado. La lista sigue en", r["lista"], "(bórrala tú si quieres).")
    else:
        if os.geteuid() != 0 and not os.environ.get("BLINDAJE_ETC"):
            sys.exit("Quitar la máxima privacidad requiere sudo.")
        for p in (r["etc_politica"],):
            if os.path.exists(p):
                os.remove(p)
        editar_bloque(r["etc_claude_md"], None, MARCA_MD)
        quitar_git(os.path.join(r["etc_dir"], "git-hooks"))
        shutil.rmtree(r["etc_dir"], ignore_errors=True)
        print("Máxima privacidad quitada.")


# ── revisar ────────────────────────────────────────────────────────────────

def revisar():
    r = rutas()
    fallas = 0

    def linea(ok, texto, aviso=False):
        nonlocal fallas
        if not ok and not aviso:
            fallas += 1
        print(f"  [{'x' if ok else ('!' if aviso else ' ')}] {texto}")

    estado = leer_json(r["estado"])
    maxima = os.path.exists(r["etc_politica"])
    if not estado and not maxima:
        print("blindaje no está instalado. Corre ./instalar.sh")
        return 1

    if maxima:
        print("Máxima privacidad (irrevocable)")
        esperado = regla(list(opciones()), r["etc_dir"], r["etc_lista"],
                         [r["etc"], r["datos"], r["config"]], estricto=True)
        linea(leer_json(r["etc_politica"]) == esperado, "política al día con el repo")
        linea(sha(os.path.join(r["etc_dir"], "privacidad.py")) == sha(os.path.join(REPO, "hooks/privacidad.py")),
              "hook de privacidad al día")
        n = contar_lista(r["etc_lista"])
        linea(bool(n and n.get("siempre")), f"lista de identificadores {n or 'no legible'}")
        linea(MARCA_MD[0] in (open(r["etc_claude_md"]).read() if os.path.exists(r["etc_claude_md"]) else ""),
              "reglas en " + r["etc_claude_md"])
        hooks_git = os.path.join(r["etc_dir"], "git-hooks")

    if estado:
        print("Seguridad normal" + (" (además)" if maxima else ""))
        ops = opciones()
        permitidas = estado.get("permitidas", [])
        esperado = regla([k for k in ops if k not in permitidas], r["datos"], r["lista"],
                         [r["datos"], r["config"]], estricto=False)
        settings = leer_json(r["settings"], {})
        faltan = [".".join(c[0]) for c in aplanar(esperado)
                  if not (isinstance(c[1], list) and isinstance(obtener(settings, c[0]), list)
                          and all(x in obtener(settings, c[0]) for x in c[1]))
                  and not (not isinstance(c[1], list) and obtener(settings, c[0]) == c[1])]
        linea(not faltan, "settings con la regla completa" + (f" (falta: {', '.join(faltan)})" if faltan else ""))
        linea(sha(os.path.join(r["datos"], "privacidad.py")) == sha(os.path.join(REPO, "hooks/privacidad.py")),
              "hook de privacidad al día")
        n = contar_lista(r["lista"])
        linea(bool(n and n.get("siempre")), f"lista de identificadores {n or 'no existe'}")
        linea(MARCA_MD[0] in (open(r["claude_md"]).read() if os.path.exists(r["claude_md"]) else ""),
              "reglas en " + r["claude_md"])
        for k in ops:
            print(f"      {'permitido' if k in permitidas else 'bloqueado'}  {ops[k]['titulo']}")
        if not maxima:
            hooks_git = os.path.join(r["datos"], "git-hooks")

    print("Git")
    linea(git_global("--get", "core.hooksPath") == hooks_git, "pre-commit global contra secretos")
    ignore = git_global("--get", "core.excludesfile") or os.path.join(r["home"], ".config", "git", "ignore")
    ignore = os.path.expanduser(ignore)
    linea(MARCA_IGNORE[0] in (open(ignore).read() if os.path.exists(ignore) else ""), "gitignore global")
    linea(bool(shutil.which("gitleaks")), "gitleaks instalado (opcional, mejora el pre-commit)", aviso=True)

    print("\nTodo en orden." if not fallas else f"\n{fallas} punto(s) por corregir: vuelve a correr ./instalar.sh")
    return 1 if fallas else 0


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="blindaje")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("aplicar")
    a.add_argument("--perfil", choices=["normal", "maxima"], required=True)
    a.add_argument("--permitir", default="")
    d = sub.add_parser("desinstalar")
    d.add_argument("--perfil", choices=["normal", "maxima"], required=True)
    sub.add_parser("revisar")
    sub.add_parser("opciones")
    sub.add_parser("lista")
    args = p.parse_args()

    if args.cmd == "aplicar":
        if args.perfil == "normal":
            aplicar_normal([x.strip() for x in args.permitir.split(",") if x.strip()])
        else:
            aplicar_maxima()
    elif args.cmd == "desinstalar":
        desinstalar(args.perfil)
    elif args.cmd == "revisar":
        sys.exit(revisar())
    elif args.cmd == "opciones":
        estado = leer_json(rutas()["estado"], {})
        permitidas = estado.get("permitidas", [])
        for k, v in opciones().items():
            print(f"{k}\t{v['titulo']}\t{v['descripcion']}\t{1 if k in permitidas else 0}")
    elif args.cmd == "lista":
        crear_lista(rutas()["lista"])


if __name__ == "__main__":
    main()

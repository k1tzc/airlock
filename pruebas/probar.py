#!/usr/bin/env python3
"""Pruebas de blindaje. No tocan el sistema: usan un HOME y un /etc falsos.

  python3 pruebas/probar.py
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "privacidad.py")
fallos = 0


def ok(cond, texto):
    global fallos
    print(f"  {'✓' if cond else '✗'} {texto}")
    if not cond:
        fallos += 1


def hook(lista, herramienta, entrada, protegidos=()):
    args = ["python3", "-I", HOOK, "--lista", lista]
    for p in protegidos:
        args += ["--proteger", p]
    evento = {"tool_name": herramienta, "tool_input": entrada, "hook_event_name": "PreToolUse"}
    r = subprocess.run(args, input=json.dumps(evento), capture_output=True, text=True)
    return r.returncode


def probar_hook(tmp):
    print("Hook de privacidad")
    lista = os.path.join(tmp, "lista")
    with open(lista, "w") as f:
        f.write("[siempre]\npersona.prueba@correo.test\n[externo]\nCliente Ficticio\nAgencia Demo\n"
                "[propios]\nagenciademo.test\n")
    prot = os.path.join(tmp, "protegido")
    casos = [
        # (descripción, herramienta, entrada, debe_bloquear)
        ("correo en un comando", "Bash", {"command": "echo persona.prueba@correo.test"}, True),
        ("correo url-encoded en WebFetch", "WebFetch", {"url": "https://x.test/?m=persona.prueba%40correo.test"}, True),
        ("correo escrito en un archivo", "Write", {"file_path": "/tmp/a.txt", "content": "contacto: Persona.Prueba@correo.test"}, True),
        ("cliente en User-Agent de curl", "Bash", {"command": "curl -A 'bot (Cliente Ficticio)' https://api.test"}, True),
        ("cliente con guiones en URL externa", "WebFetch", {"url": "https://maps.test/search?q=cliente-ficticio"}, True),
        ("proyecto en búsqueda web", "WebSearch", {"query": "AgenciaDemo reseñas"}, True),
        ("proyecto en payload MCP", "mcp__canva__create", {"title": "Logo Agencia Demo"}, True),
        ("User-Agent con cliente en un script", "Write", {"file_path": "/tmp/s.py", "content": "H = {'User-Agent': 'fetch/1.0 Cliente Ficticio'}"}, True),
        ("editar la lista protegida", "Write", {"file_path": prot + "/identificadores", "content": ""}, True),
        ("borrar la lista con Bash", "Bash", {"command": f"rm {prot}/identificadores"}, True),
        ("git commit --no-verify", "Bash", {"command": "git commit --no-verify -m x"}, True),
        # lo que debe pasar
        ("cliente en el código local", "Write", {"file_path": "/tmp/t.ts", "content": "const titulo = 'Agencia Demo';"}, False),
        ("cliente en commit (repo privado)", "Bash", {"command": "git commit -m 'itinerario de Cliente Ficticio'"}, False),
        ("ruta local con el nombre + curl", "Bash", {"command": "curl -A image-fetch/1.0 -o /home/u/agencia-demo/x.jpg https://commons.test/x.jpg"}, False),
        ("ssh/rsync a dominio propio", "Bash", {"command": "rsync -a . deploy@staging.agenciademo.test:/srv/"}, False),
        ("WebFetch al dominio propio", "WebFetch", {"url": "https://agenciademo.test/tours", "prompt": "revisa Agencia Demo"}, False),
        ("User-Agent neutro", "Write", {"file_path": "/tmp/s.py", "content": "H = {'User-Agent': 'image-fetch/1.0'}"}, False),
        ("leer la lista protegida", "Bash", {"command": f"cat {prot}/identificadores"}, False),
        ("comando normal", "Bash", {"command": "npm run build"}, False),
    ]
    for desc, herr, entrada, bloquear in casos:
        codigo = hook(lista, herr, entrada, [prot])
        ok((codigo == 2) == bloquear, f"{'bloquea' if bloquear else 'deja pasar'}: {desc} (exit {codigo})")
    ok(hook(os.path.join(tmp, "no-existe"), "Bash", {"command": "ls"}) == 0, "sin lista no rompe nada")


def correr(env, *args, entrada=None):
    return subprocess.run(["python3", "-I", os.path.join(REPO, "lib", "blindaje.py"), *args],
                          env=env, input=entrada, capture_output=True, text=True)


def probar_instalacion(tmp):
    print("Instalación (HOME y /etc falsos)")
    home, etc = os.path.join(tmp, "home"), os.path.join(tmp, "etc")
    os.makedirs(os.path.join(home, ".claude"))
    os.makedirs(os.path.join(home, ".config", "git"))
    previo = {"theme": "dark", "env": {"MI_VAR": "1"}, "permissions": {"deny": ["Read(~/.ssh/**)"], "allow": ["Bash(ls)"]}}
    ajustes = os.path.join(home, ".claude", "settings.json")
    json.dump(previo, open(ajustes, "w"))
    open(os.path.join(home, ".config", "git", "ignore"), "w").write("mis-cosas/\n")
    env = dict(os.environ, BLINDAJE_HOME=home, BLINDAJE_ETC=etc, HOME=home)
    entrada = "persona.prueba@correo.test\n\nCliente Ficticio\n\n\n"

    r = correr(env, "aplicar", "--perfil", "normal", "--permitir", "artifacts", entrada=entrada)
    ok(r.returncode == 0, "aplica perfil normal" + ("" if r.returncode == 0 else f": {r.stderr}"))
    s = json.load(open(ajustes))
    ok(s["theme"] == "dark" and s["env"]["MI_VAR"] == "1" and "Bash(ls)" in s["permissions"]["allow"],
       "conserva los ajustes propios del usuario")
    ok("enableArtifact" not in s and "Artifact" not in s["permissions"]["deny"], "Artifacts permitido")
    ok("mcp__claude_ai_Claude_Docs" in s["permissions"]["deny"], "Docs bloqueado")
    ok(s["permissions"]["deny"].count("Read(~/.ssh/**)") == 1, "no duplica reglas que ya existían")
    ok(oct(os.stat(os.path.join(home, ".config/blindaje/identificadores")).st_mode)[-3:] == "600", "lista con permisos 600")
    ok(correr(env, "revisar").returncode == 0, "auditoría en verde")

    r = correr(env, "aplicar", "--perfil", "normal")
    s = json.load(open(ajustes))
    ok(s.get("enableArtifact") is False and "Artifact" in s["permissions"]["deny"], "re-aplicar cambia opciones (bloquea Artifacts)")
    ok(len(s["hooks"]["PreToolUse"]) == 1, "no duplica el hook al re-aplicar")

    s["permissions"]["deny"].remove("Read(~/.aws/**)")
    json.dump(s, open(ajustes, "w"))
    ok(correr(env, "revisar").returncode == 1, "auditoría detecta una regla borrada")

    git_cfg = os.path.join(home, ".gitconfig")
    ok("blindaje" in open(git_cfg).read(), "core.hooksPath configurado")

    r = correr(env, "aplicar", "--perfil", "maxima")
    ok(r.returncode == 0, "aplica perfil máxima" + ("" if r.returncode == 0 else f": {r.stderr}"))
    pol = json.load(open(os.path.join(etc, "managed-settings.d", "50-blindaje.json")))
    ok(pol.get("enableArtifact") is False and pol.get("disableClaudeAiConnectors") is True, "máxima bloquea todo")
    ok("--estricto" in pol["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "máxima usa hook estricto")
    ok(os.path.exists(os.path.join(etc, "blindaje", "identificadores")), "lista copiada a /etc")

    correr(env, "desinstalar", "--perfil", "maxima")
    r = correr(env, "desinstalar", "--perfil", "normal")
    s = json.load(open(ajustes))
    esperado = dict(previo)
    esperado["permissions"] = {"deny": ["Read(~/.ssh/**)"], "allow": ["Bash(ls)"]}
    ok(s == esperado, "desinstalar deja settings como estaban" + ("" if s == esperado else f": {s}"))
    ok(open(os.path.join(home, ".config", "git", "ignore")).read().strip() == "mis-cosas/", "gitignore como estaba")
    ok("hookspath" not in open(git_cfg).read().lower(), "core.hooksPath quitado")


def probar_precommit(tmp):
    print("Pre-commit")
    home, repo = os.path.join(tmp, "h2"), os.path.join(tmp, "repo")
    os.makedirs(os.path.join(home, ".config", "blindaje"))
    open(os.path.join(home, ".config", "blindaje", "identificadores"), "w").write("[siempre]\npersona.prueba@correo.test\n")
    env = dict(os.environ, HOME=home, GIT_CONFIG_GLOBAL=os.path.join(home, ".gitconfig"))
    hooks = os.path.join(tmp, "hooks")
    os.makedirs(hooks)
    for n in ("pre-commit.py", "despachador"):
        dst = os.path.join(hooks, n)
        open(dst, "w").write(open(os.path.join(REPO, "git", n)).read())
        os.chmod(dst, 0o755)
    os.symlink("despachador", os.path.join(hooks, "pre-commit"))
    g = lambda *a: subprocess.run(["git", "-C", repo, *a], env=env, capture_output=True, text=True)
    subprocess.run(["git", "init", "-q", repo], env=env)
    g("config", "core.hooksPath", hooks)
    g("config", "user.name", "t"); g("config", "user.email", "t@t.test")

    def intento(nombre, contenido):
        open(os.path.join(repo, nombre), "w").write(contenido)
        g("add", nombre)
        r = g("commit", "-qm", "x")
        if r.returncode != 0:
            g("reset", "-q", nombre)
            os.remove(os.path.join(repo, nombre))
        return r.returncode == 0

    ok(intento("a.txt", "hola\n"), "commit normal pasa")
    ok(not intento("b.txt", "key=AKIAABCDEFGHIJKLMNOP\n"), "frena clave de AWS")
    ok(not intento(".env", "DB=x\n"), "frena .env")
    ok(intento(".env.example", "DB=\n"), "deja pasar .env.example")
    ok(not intento("c.txt", "escribe a persona.prueba@correo.test\n"), "frena el correo protegido")
    propio = os.path.join(repo, ".git", "hooks", "pre-commit")
    open(propio, "w").write("#!/bin/sh\necho propio > \"$(git rev-parse --show-toplevel)/.marca\"\n")
    os.chmod(propio, 0o755)
    intento("d.txt", "ok\n")
    ok(os.path.exists(os.path.join(repo, ".marca")), "también corre el hook propio del repo")


with tempfile.TemporaryDirectory() as tmp:
    probar_hook(tmp)
    probar_instalacion(tmp)
    probar_precommit(tmp)

print(f"\n{'Todo bien' if not fallos else f'{fallos} fallo(s)'}")
sys.exit(1 if fallos else 0)

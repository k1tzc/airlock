#!/usr/bin/env python3
"""Pre-commit global de airlock: frena secretos e identificadores [siempre] en lo que se commitea.

Revisa solo las líneas añadidas del staging. Si gitleaks está instalado, también lo corre.
Si es un falso positivo, quien commitea (una persona) puede usar 'git commit --no-verify'.
"""
import os
import re
import shutil
import subprocess
import sys

LISTAS = [
    os.path.expanduser("~/.config/airlock/identificadores"),
    "/etc/claude-code/airlock/identificadores",
]

SECRETOS = [
    ("clave privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("clave de AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("token de GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    ("token de Slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("clave de Stripe", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}\b")),
    ("clave de Google", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("clave de Anthropic/OpenAI", re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{32,}\b")),
    ("URL con contraseña", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s:/@]{6,}@", re.I)),
]
ENV = re.compile(r"(^|/)\.env(\.[\w-]+)?$")
ENV_PLANTILLA = re.compile(r"\.(example|sample|template|dist)$")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout


def identificadores_siempre():
    for ruta in LISTAS:
        try:
            with open(ruta, encoding="utf-8") as f:
                seccion, salida = None, []
                for linea in f:
                    linea = linea.strip()
                    if not linea or linea.startswith("#"):
                        continue
                    if re.fullmatch(r"\[\w+\]", linea):
                        seccion = linea[1:-1].lower()
                    elif seccion == "siempre":
                        salida.append(linea)
                return salida
        except (FileNotFoundError, PermissionError):
            continue
    return []


def main():
    problemas = []
    for archivo in git("diff", "--cached", "--name-only", "--diff-filter=AM").splitlines():
        if ENV.search(archivo) and not ENV_PLANTILLA.search(archivo):
            problemas.append(f"{archivo}: es un .env (credenciales); no se commitea")

    siempre = [i.lower() for i in identificadores_siempre()]
    archivo = "?"
    for linea in git("diff", "--cached", "-U0", "--no-color").splitlines():
        if linea.startswith("+++ "):
            archivo = linea[6:] if linea.startswith("+++ b/") else linea[4:]
            continue
        if not linea.startswith("+"):
            continue
        texto = linea[1:]
        for nombre, patron in SECRETOS:
            if patron.search(texto):
                problemas.append(f"{archivo}: parece una {nombre}")
        for ident in siempre:
            if ident in texto.lower():
                problemas.append(f"{archivo}: contiene un identificador personal protegido")

    if shutil.which("gitleaks"):
        r = subprocess.run(["gitleaks", "protect", "--staged", "--no-banner", "--redact"], check=False)
        if r.returncode != 0:
            problemas.append("gitleaks encontró secretos (detalle arriba)")

    if problemas:
        print("airlock: commit frenado\n  " + "\n  ".join(sorted(set(problemas))), file=sys.stderr)
        print("Si es un falso positivo y lo revisaste tú: git commit --no-verify", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

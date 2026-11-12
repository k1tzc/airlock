# blindaje

Reglas de seguridad y privacidad para Claude Code y git, reutilizables en cualquier máquina y proyecto.

```bash
git clone <este repo> ~/blindaje && cd ~/blindaje
./instalar.sh            # menú
```

## Perfiles

| | Máxima privacidad | Seguridad normal |
|---|---|---|
| Dónde | `/etc/claude-code/managed-settings.d/50-blindaje.json` | `~/.claude/settings.json` (se combina, no se reemplaza) |
| Funciones opcionales | todas bloqueadas | tú eliges cuáles permitir |
| ¿Se puede deshacer desde un proyecto o sesión? | **No.** Las reglas `deny` y los hooks de los managed settings se suman a los de los otros niveles y nadie los quita; solo root, borrando el archivo | Sí, editando los settings o volviendo a correr el instalador |
| Lista de identificadores | `/etc/claude-code/blindaje/identificadores` (solo root la cambia) | `~/.config/blindaje/identificadores` |
| Hook | estricto: si falla o tarda demasiado, bloquea | si falla, deja pasar |
| Requiere | `sudo` | nada |

Se pueden tener los dos a la vez: lo más estricto gana.

```bash
./instalar.sh --perfil normal --permitir artifacts,docs   # sin menú
sudo ./instalar.sh --perfil maxima
./instalar.sh --check                                      # auditoría; sale con 1 si falta algo
./instalar.sh --desinstalar normal                         # deja todo como estaba
```

## Qué va siempre (los dos perfiles)

- **Telemetría, reporte de errores, encuestas y `/feedback`: apagados.**
- **Lectura de credenciales bloqueada:** `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube`, `.git-credentials`, `.npmrc`, `.netrc`, gh, gcloud, docker.
- **Hook de privacidad** (`hooks/privacidad.py`), que revisa cada herramienta antes de que se ejecute:
  - `[siempre]` (tu correo): bloqueado en todo (comandos, archivos, web, MCP).
  - `[externo]` (tu nombre, clientes, proyectos): bloqueado solo hacia terceros, es decir, WebFetch, WebSearch, herramientas MCP, comandos de red en Bash (`curl`, `wget`, `ssh`, `requests`...) y cabeceras `User-Agent`/`Referer` escritas en archivos. Sí pueden aparecer en tu código, en tus commits y en rutas locales.
  - `[propios]` (tus dominios): se ignoran, para que el deploy o el ssh a tu servidor no se bloqueen.
  - También bloquea modificar los archivos de blindaje y usar `git ... --no-verify`.
- **Git:** pre-commit global que frena `.env`, claves privadas, tokens (AWS, GitHub, Stripe, Slack, Google, Anthropic/OpenAI), URLs con contraseña y tus identificadores `[siempre]`. Si `gitleaks` está instalado, también lo corre. El hook propio de cada repo se sigue ejecutando. Además, un gitignore global para `.env` y claves.
- **Reglas de comportamiento** para Claude en `CLAUDE.md` (`~/.claude/` o `/etc/claude-code/`).

## Funciones opcionales (perfil normal)

`artifacts` · `docs` (Claude Docs) · `conectores` (Canva, Sheets... incluye Docs) · `remoto` (Remote Control) · `push` (notificaciones) · `trafico` (tráfico no esencial; si lo bloqueas, actualiza con `claude update`).

Las opciones están en `reglas/opciones.json`. Agregar una es añadir una entrada con su `bloqueo`.

## La lista de identificadores no va en el repo

La lista de nombres a proteger ya es información sensible. El repo solo trae `identificadores.example`, y el instalador te pide los valores la primera vez. El archivo real queda con permisos `600` y Claude no puede modificarlo.

## Límites (léelos)

- El hook compara texto: frena los errores comunes (un nombre en un User-Agent o en una URL), pero no a alguien que codifique el dato a propósito (base64, por ejemplo). El objetivo es evitar fugas por descuido.
- Si un repo define su propio `core.hooksPath` (husky), git usa ese y no el pre-commit global.
- `cleanupPeriodDays: 7` borra los transcripts locales a la semana.

## Pruebas

```bash
python3 pruebas/probar.py    # usa un HOME y un /etc falsos; no toca tu sistema
```

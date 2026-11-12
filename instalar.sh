#!/usr/bin/env bash
# blindaje — reglas de seguridad y privacidad para Claude Code y git.
#
#   ./instalar.sh                                  menú
#   ./instalar.sh --perfil normal [--permitir artifacts,docs]
#   sudo ./instalar.sh --perfil maxima
#   ./instalar.sh --check                          auditar (sale con 1 si falta algo)
#   ./instalar.sh --desinstalar normal|maxima
set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
NUCLEO=(python3 -I "$DIR/lib/blindaje.py")

maxima() {
  if [[ $EUID -ne 0 && -z "${BLINDAJE_ETC:-}" ]]; then
    echo "La máxima privacidad escribe en /etc/claude-code; pido sudo."
    exec sudo "$DIR/instalar.sh" --perfil maxima
  fi
  "${NUCLEO[@]}" aplicar --perfil maxima
}

normal_menu() {
  local claves=() titulos=() descripciones=() permitido=()
  while IFS=$'\t' read -r clave titulo desc perm; do
    claves+=("$clave"); titulos+=("$titulo"); descripciones+=("$desc"); permitido+=("$perm")
  done < <("${NUCLEO[@]}" opciones)

  while true; do
    echo
    echo "  Seguridad normal — elige qué funciones PERMITIR (lo demás queda bloqueado)"
    echo "  La base (telemetría off, credenciales, hook de privacidad, git) va siempre."
    echo
    for i in "${!claves[@]}"; do
      local marca="[ ]"; [[ ${permitido[$i]} == 1 ]] && marca="[x]"
      printf '   %d) %s %-24s %s\n' "$((i + 1))" "$marca" "${titulos[$i]}" "${descripciones[$i]}"
    done
    echo
    read -rp "  Número para cambiar, Enter para aplicar, q para salir: " r
    case "$r" in
      "") break ;;
      q | Q) exit 0 ;;
      *[!0-9]*) ;;
      *) if ((r >= 1 && r <= ${#claves[@]})); then
           local j=$((r - 1)); permitido[$j]=$((1 - permitido[$j]))
         fi ;;
    esac
  done

  local lista=()
  for i in "${!claves[@]}"; do [[ ${permitido[$i]} == 1 ]] && lista+=("${claves[$i]}"); done
  "${NUCLEO[@]}" aplicar --perfil normal --permitir "$(IFS=,; echo "${lista[*]:-}")"
}

menu() {
  cat <<'EOF'

  blindaje — seguridad y privacidad

   1) Máxima privacidad   todo bloqueado, irrevocable (managed settings, pide sudo)
   2) Seguridad normal    base segura + tú eliges qué funciones permitir
   3) Auditar             qué está activo y qué falta
   4) Desinstalar
   q) Salir

EOF
  read -rp "  Elige: " op
  case "$op" in
    1) maxima ;;
    2) normal_menu ;;
    3) "${NUCLEO[@]}" revisar ;;
    4) read -rp "  ¿Cuál? (normal/maxima): " p
       if [[ $p == maxima && $EUID -ne 0 && -z "${BLINDAJE_ETC:-}" ]]; then
         exec sudo "$DIR/instalar.sh" --desinstalar maxima
       fi
       "${NUCLEO[@]}" desinstalar --perfil "$p" ;;
    *) exit 0 ;;
  esac
}

case "${1:-}" in
  "") menu ;;
  --check) exec "${NUCLEO[@]}" revisar ;;
  --perfil)
    case "${2:-}" in
      maxima) maxima ;;
      normal) shift 2; permitir=""
              [[ "${1:-}" == --permitir ]] && permitir="${2:-}"
              "${NUCLEO[@]}" aplicar --perfil normal --permitir "$permitir" ;;
      *) echo "Perfil: normal o maxima" >&2; exit 2 ;;
    esac ;;
  --desinstalar) "${NUCLEO[@]}" desinstalar --perfil "${2:?normal o maxima}" ;;
  -h | --help) sed -n 2,8p "$0" ;;
  *) echo "Opción desconocida: $1 (usa --help)" >&2; exit 2 ;;
esac

#!/usr/bin/env bash
#
# fix_line_endings.sh
#
# Normaliza terminadores de linha CRLF -> LF em ficheiros de texto/script
# já existentes no repositório (o .gitattributes só se aplica a partir
# do momento em que é adicionado; isto corrige o que já está commitado).
#
# Uso:
#   ./fix_line_endings.sh              # corrige a partir da raiz do repo git
#   ./fix_line_endings.sh /caminho/dir # corrige a partir de um diretório específico
#
set -euo pipefail

TARGET_DIR="${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
EXTENSIONS=("py" "sh" "bash" "cfg" "conf" "ini" "yml" "yaml" "env")

echo "A normalizar terminadores de linha (CRLF -> LF) em: ${TARGET_DIR}"
echo

fixed_count=0

fix_file() {
    local file="$1"
    if file "$file" | grep -q "CRLF"; then
        sed -i 's/\r$//' "$file"
        echo "  corrigido: $file"
        fixed_count=$((fixed_count + 1))
    fi
}

# Ficheiros por extensão conhecida
for ext in "${EXTENSIONS[@]}"; do
    while IFS= read -r -d '' file; do
        fix_file "$file"
    done < <(find "$TARGET_DIR" -type f -name "*.${ext}" \
             -not -path "*/.git/*" -print0)
done

# Dockerfiles (sem extensão, ou Dockerfile.algo)
while IFS= read -r -d '' file; do
    fix_file "$file"
done < <(find "$TARGET_DIR" -type f \( -name "Dockerfile" -o -name "Dockerfile.*" \) \
         -not -path "*/.git/*" -print0)

# Scripts sem extensão mas com shebang (ex: entrypoint, boot.sh renomeado)
while IFS= read -r -d '' file; do
    if head -c 2 "$file" 2>/dev/null | grep -q '#!'; then
        fix_file "$file"
    fi
done < <(find "$TARGET_DIR" -type f ! -name "*.*" \
         -not -path "*/.git/*" -print0)

echo
echo "Concluído. ${fixed_count} ficheiro(s) corrigido(s)."

if [ "$fixed_count" -gt 0 ] && git -C "$TARGET_DIR" rev-parse --git-dir > /dev/null 2>&1; then
    echo
    echo "Este diretório é um repositório git. Recomenda-se:"
    echo "  1. Garantir que o .gitattributes já está commitado."
    echo "  2. Correr: git -C \"$TARGET_DIR\" add --renormalize ."
    echo "  3. Rever e fazer commit das alterações."
fi
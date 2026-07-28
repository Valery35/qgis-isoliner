#!/bin/sh
# Сборка шпаргалок Isoliner в PDF: pandoc + xelatex, без оглавления.
# Оформление (поля, шрифты, кегль) задано шапкой YAML в самих .md, поэтому
# ключей форматирования здесь нет. Готовые PDF кладутся в site/ и в плагин
# не входят.
#
# Запуск из папки manual:  ./build_cheatsheet.sh [папка_выхода]
set -e
OUT_DIR="${1:-../site}"
mkdir -p "$OUT_DIR"
for src in cheatsheet_*.md; do
    base=$(basename "$src" .md | sed 's/^cheatsheet_//')
    case "$base" in
        *_ru) name="isoliner_${base%_ru}_cheatsheet_ru" ;;
        *_en) name="isoliner_${base%_en}_cheatsheet_en" ;;
        *)    name="isoliner_${base}" ;;
    esac
    echo "== $src -> $name.pdf"
    pandoc "$src" --from=markdown --pdf-engine=xelatex \
        -o "$OUT_DIR/$name.pdf" 2>/dev/null
done
ls -la "$OUT_DIR"/isoliner_*cheatsheet*.pdf

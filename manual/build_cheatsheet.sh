#!/usr/bin/env bash
# Сборка шпаргалки по топографии: RU и EN, по одной странице A4.
# Запускать из manual/. Готовые PDF кладутся в ../site/ (уходят на лендинг,
# в плагин не входят).
set -euo pipefail
cd "$(dirname "$0")"
OUT=../site
for L in ru en; do
  pandoc "cheatsheet_topo_$L.md" -o "_cheat_$L.pdf" --pdf-engine=xelatex -V lang=$L
  gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.5 -dPDFSETTINGS=/ebook \
     -dNOPAUSE -dQUIET -dBATCH \
     -sOutputFile="$OUT/isoliner_topo_cheatsheet_$L.pdf" "_cheat_$L.pdf"
  rm -f "_cheat_$L.pdf"
done
echo "Готово: $OUT/isoliner_topo_cheatsheet_ru.pdf, $OUT/isoliner_topo_cheatsheet_en.pdf"

#!/bin/sh
# Сборка руководств Isoliner в PDF: pandoc + xelatex, затем сжатие картинок
# Ghostscript до 150 dpi. Результат кладётся в grid_isolines/doc/.
#
# Заголовок, язык и название оглавления берутся из шапки самих manual.md и
# manual_en.md, задавать их ключами не нужно: -M title поверх шапки ломает
# кириллицу в титуле.
#
# Пакет float здесь намеренно не подключается. Прижатие картинок к месту
# через floatplacement{figure}{H} разносит документ на две лишние страницы
# и рвёт заполнение полос.
#
# Запуск из папки manual:  ./build_pdf.sh [папка_выхода]
set -e

OUT_DIR="${1:-../grid_isolines/doc}"
mkdir -p "$OUT_DIR"

build() {
    src="$1"
    dst="$2"
    echo "== $src -> $dst"
    pandoc "$src" \
        --from=markdown \
        --pdf-engine=xelatex \
        --toc --toc-depth=3 \
        -V geometry:margin=2cm \
        -V mainfont="DejaVu Serif" \
        -V monofont="DejaVu Sans Mono" \
        -V monofontoptions="Scale=0.8" \
        -V colorlinks=true \
        -o /tmp/_isoliner_raw.pdf
    gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.5 \
       -dDownsampleColorImages=true -dColorImageResolution=150 \
       -dDownsampleGrayImages=true -dGrayImageResolution=150 \
       -dDownsampleMonoImages=true -dMonoImageResolution=150 \
       -dNOPAUSE -dBATCH -dQUIET \
       -sOutputFile="$OUT_DIR/$dst" /tmp/_isoliner_raw.pdf
    rm -f /tmp/_isoliner_raw.pdf
}

# Дерево инструментов вставляется в руководства генератором из кода:
# скриншот панели при полусотне инструментов нечитаем и устаревает каждый
# релиз, а сгенерированный список не расходится с плагином никогда.
python3 gen_tree.py ../grid_isolines/algorithms.py ru > /tmp/_tree_ru.md || true
python3 gen_tree.py ../grid_isolines/algorithms.py en > /tmp/_tree_en.md || true

build manual.md Isoliner.pdf
build manual_en.md Isoliner_en.pdf

ls -la "$OUT_DIR"

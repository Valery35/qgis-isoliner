# Isoliner 5.13.61 - тексты для площадок

Ссылка на выпуск:
https://github.com/Valery35/qgis-isoliner/releases/tag/5.13.61

## Русский (ВК, Facebook)

Isoliner 5.13.61 - четвёртый источник ЦМР, GEM-15 с ячейкой 15 метров

Обновил Isoliner, в выпуске один крупный шаг.

- **2.01 Загрузка ЦМР** получила четвёртый источник, GEM-15 от Google Research. Ячейка 15 метров, высоты от EGM96, лицензия CC BY 4.0, набор помечен экспериментальным. Каналы DTM и DSM выбираются в дополнительных параметрах, по умолчанию DTM. Список плиток берётся из каталога STAC набора по градусным ячейкам, чтение идёт через /vsicurl/ и общий варп, потому что соседние плитки лежат в разных зонах UTM. Защита max_tiles осталась общей для всех источников.
- Рамка вне покрытия даёт понятный отказ с советом взять GLO-30. Частичное покрытие даёт предупреждение с долей заполненных ячеек, чтобы человек знал, чего ждать от рельефа.
- Атрибуция «This dataset is produced by Google» пишется в журнал.
- Руководство на русском и английском, шпаргалка по топографии, переводы справки и сообщений обновлены.

Живая проверка шла на четырёх рамках. Шымкент дал 554 на 596 ячеек по 15 метров, заполнено 99,9 процента. Актау у берега дал 71,9 процента и предупреждение. Две плитки в зонах UTM 42 и 43 сшиты без шва. Москва получила отказ, как и задумано.

Версия ждёт проверки в каталоге QGIS. Архив лежит на GitHub и ставится через «Установить из ZIP».

Выпуск: https://github.com/Valery35/qgis-isoliner/releases/tag/5.13.61

Вопрос к читателям (опрос ВК). Какой источник рельефа вы берёте для инженерных задач?
- SRTM или GLO-30
- GEM-15
- Своя съёмка
- Что даёт заказчик

## English (LinkedIn)

Isoliner 5.13.61 - a fourth DEM source, GEM-15 with 15 metre cells

- **2.01 Load a DEM** now offers GEM-15 from Google Research, a 15 metre cell with heights above EGM96, CC BY 4.0, marked experimental. DTM and DSM channels are chosen in the advanced parameters, DTM by default. The tile list comes from the dataset STAC catalog by degree cells, tiles are read through /vsicurl/ with the shared warp, because neighbouring tiles sit in different UTM zones. The max_tiles guard stays shared with the other sources.
- A frame outside the coverage returns a clear refusal that suggests GLO-30. Partial coverage returns a warning with the share of filled cells, so the user knows what to expect from the relief.
- The attribution «This dataset is produced by Google» goes to the log.

Checked live on four frames. Shymkent gave 554 by 596 cells of 15 metres with 99.9 percent filled. Aktau near the coast gave 71.9 percent and a warning. Two tiles in UTM zones 42 and 43 joined without a seam. Moscow refused, as intended.

The version awaits review in the QGIS plugin repository. The archive is on GitHub, install it with Install from ZIP.

Release: https://github.com/Valery35/qgis-isoliner/releases/tag/5.13.61

#QGIS #GIS #Geology #MiningGeology #OpenSource #DEM

## X

Isoliner 5.13.61 - GEM-15, a fourth DEM source, 15 m cells. DTM and DSM, tiles by degree cell, seams across UTM zones joined. Works inside 2.01 Load a DEM. https://github.com/Valery35/qgis-isoliner/releases/tag/5.13.61

## Mastodon (English)

Isoliner 5.13.61 adds GEM-15 from Google Research as the fourth DEM source. A 15 metre cell with heights above EGM96, CC BY 4.0, experimental. DTM and DSM channels, the tile list comes from the dataset STAC catalog, neighbouring tiles in different UTM zones are joined by the shared warp. A frame outside the coverage returns a refusal that suggests GLO-30, partial coverage reports the share of filled cells.

https://github.com/Valery35/qgis-isoliner/releases/tag/5.13.61

#QGIS #GIS #DEM

# Isoliner 5.13.47 - тексты для площадок

Выпуск объединяет 5.13.46 и 5.13.47. Ссылка на выпуск:
https://github.com/Valery35/qgis-isoliner/releases/tag/5.13.47

## Русский (ВК, Facebook)

Isoliner 5.13.47 - разрез в GeoPackage без потерь и английский интерфейс без русского текста

Обновил Isoliner, в выпуске две правки.

- **4.07 Проекция объектов на разрез** больше не теряет объекты при записи в файл GeoPackage. Если в определении несколько разрезов, объект выносится на каждый. До сих пор на втором и следующих разрезах объекты в файл не попадали, на демо с тремя разрезами записывалось 27 объектов из 81, и окно об этом не сообщало. Во временный слой всё писалось верно.
- Поле fid исходного слоя теперь переходит в результат под именем fid_2. Так сделано во всех инструментах, которые переносят поля исходного слоя, поэтому номер объекта сохраняется, а запись в GeoPackage проходит.
- Английский интерфейс больше не показывает русский текст. Слои окна «Подоснова» и их стили названы по-английски (ESRI World Imagery, OpenTopoMap и другие). Переведены сообщения в журнале, подписи на разрезах и в отчёте створа, имена в демо-данных. Индексы пластов остаются как в стратиграфии.

Если вы сохраняли результат 4.07 в GeoPackage по определению с несколькими разрезами, пересчитайте его. Расчёты, параметры и стили слоёв не изменились.

Версия ждёт проверки в каталоге QGIS. Архив уже на GitHub и ставится через «Установить из ZIP».

Выпуск: https://github.com/Valery35/qgis-isoliner/releases/tag/5.13.47

Вопрос к читателям (опрос ВК). Куда вы обычно сохраняете результаты инструментов?
- Во временные слои
- В GeoPackage
- В Shapefile
- По-разному

## English (LinkedIn)

Isoliner 5.13.47 - section projection to GeoPackage without losses, an English interface without Russian text

- **4.07 Project objects onto the section** no longer loses objects when writing to a GeoPackage file. With several sections in the definition, each object is placed on every section. Until now the objects of the second and further sections did not reach the file, 27 of 81 in the demo with three sections, and the dialog said nothing. Temporary layers were always complete.
- The fid field of the source layer now goes to the output as fid_2, in every tool that carries source fields over. The object number is kept and the GeoPackage write goes through.
- The English interface no longer shows Russian text. Basemap layers and their styles have English names (ESRI World Imagery, OpenTopoMap and others). Log messages, labels on sections and in the gauging report, and demo data are translated. Bed codes stay as stratigraphic indices.

If you saved 4.07 results to a GeoPackage from a definition with several sections, run it again. Calculations, parameters and layer styles are unchanged.

The version is awaiting review in the QGIS plugin repository. The archive is on GitHub, install it with Install from ZIP.

Release: https://github.com/Valery35/qgis-isoliner/releases/tag/5.13.47

#QGIS #GIS #Geology #MiningGeology #OpenSource #GeoPackage

## X

Isoliner 5.13.47 for #QGIS: section projection 4.07 no longer drops objects when saving to GeoPackage (27 of 81 reached the file before), the source fid is kept as fid_2. The English interface is fully English, basemaps included. https://github.com/Valery35/qgis-isoliner/releases/tag/5.13.47

## Reddit (r/QGIS, публикует автор)

Заголовок: Isoliner 5.13.47: section projection no longer drops objects in GeoPackage output

Isoliner is a free QGIS plugin for geologists, mine surveyors and topographers (kriging and contours, relief and hydrology, sections).

This release fixes a silent data loss. Tool 4.07 "Project objects onto the section" places each object on every section of the definition. When the output was a GeoPackage file, the copies for the second and further sections were rejected because they carried the same fid as the first copy (UNIQUE constraint failed), and only the GDAL log showed it. In the demo with three sections, 27 of 81 objects reached the file. The source fid now goes to the output as fid_2, in every tool that copies source fields.

The English interface also no longer shows Russian text, the basemap layer names included.

Release and archive: https://github.com/Valery35/qgis-isoliner/releases/tag/5.13.47

# Взаимные ссылки трёх плагинов

В Isoliner ссылки уже стоят: в `about` каталога (обе половины, EN и RU) и
в обоих README после первого абзаца. Ниже готовые куски для Isoliner3D и
Topoliner - их исходники в этой сессии недоступны, поэтому текст
подготовлен, но не вставлен.

Формула одна на все три: назвать два других плагина, сказать одной
фразой, что делает каждый, и дать ссылку. Порядок работы называется
явно - Topoliner на исходных контурах, Isoliner на интерполяции,
Isoliner3D на показе.

## Isoliner3D, `metadata.txt`

Сейчас в `about` стоит «Companion to the Isoliner kriging toolset» и
«Спутник набора кригинга Isoliner»: Topoliner не назван, ссылок нет.
Заменить эти две фразы на такие.

EN, перед «Developed with the support»:

    Two companion plugins work alongside it: Isoliner
    (https://github.com/Valery35/qgis-isoliner) builds the grids,
    isolines and belts that this viewer shows, and Topoliner
    (https://github.com/Valery35/topoliner) puts the topology of polygon
    and line layers in order before the interpolation.

RU, перед «Разработано при поддержке»:

    Рядом работают два плагина того же набора: Isoliner
    (https://github.com/Valery35/qgis-isoliner) строит гриды, изолинии и
    пояса, которые этот просмотрщик показывает, а Topoliner
    (https://github.com/Valery35/topoliner) приводит в порядок топологию
    полигональных и линейных слоёв перед интерполяцией.

## Topoliner, `metadata.txt`

В `about` сейчас нет ни одного упоминания соседей. Дописать в конец
каждой половины.

EN:

    Two companion plugins work alongside it: Isoliner
    (https://github.com/Valery35/qgis-isoliner) interpolates point data
    and builds isolines and contour polygons over the outlines cleaned
    here, and Isoliner3D
    (https://github.com/Valery35/qgis-isoliner3d) shows the surfaces and
    the bodies in a 3D scene.

RU:

    Рядом работают два плагина того же набора: Isoliner
    (https://github.com/Valery35/qgis-isoliner) интерполирует точечные
    данные и строит изолинии и контурные полигоны по вычищенным здесь
    контурам, а Isoliner3D
    (https://github.com/Valery35/qgis-isoliner3d) показывает поверхности
    и тела в 3D-сцене.

## README обоих плагинов

Тем же абзацем после первого описания, ссылками в разметке markdown.
В Isoliner он выглядит так, для остальных двух меняются только имена и
порядок:

    **Рядом работают два плагина того же набора.**
    [Isoliner3D](https://github.com/Valery35/qgis-isoliner3d) показывает
    поверхности, тела пластов и скважины в отдельной 3D-сцене и считает
    запасы по блочной модели.
    [Topoliner](https://github.com/Valery35/topoliner) приводит в порядок
    топологию полигональных и линейных слоёв: узлы, висячие концы,
    перехлёсты, упрощение с сохранением общих границ. Порядок обычный -
    сначала Topoliner на исходных контурах, потом Isoliner на
    интерполяции, потом Isoliner3D на показе.

## Две проверки перед выкладкой

Адрес Topoliner - `github.com/Valery35/topoliner`, без приставки `qgis-`,
в отличие от двух других. Проверено запросом: `qgis-topoliner` отдаёт
404.

Каталог проверяет ссылки из metadata и валит загрузку на неотвечающем
адресе. Все три ссылки отвечают, но при следующей выкладке стоит
взглянуть на журнал загрузки: у ссылок в `about` та же проверка, что и у
`tracker`.

# mods-to-blitz-legacy

Моды танков для World of Tanks Blitz (legacy). Каждый мод лежит в отдельной
папке `mods/` и повторяет структуру игровых ресурсов:

```
mods/<название мода>/
├── assets/tanks/     модель, текстуры, иконка (+ .import Godot)
└── tanks_mod/        .tres с описанием танка
```

## Список модов

| Мод | Танк | Нация / класс | Описание |
|---|---|---|---|
| [poland_60tp](mods/poland_60tp) | 60TP Lewandowskiego | Польша, тяжёлый, X ур. | Новая модель 60TP: пересобранная иерархия узлов, физика, броневые плиты |
| [germany_waffentrager_e110](mods/germany_waffentrager_e110) | Waffenträger E 110 | Германия, ПТ, X ур. | Задняя рубка на шасси E 100: 8 катков на борт, 12,8 cm Kw.K. 44 L/55 WM |
| [other_lv1300](mods/other_lv1300) | ЛВ-1300 Уран | Сборная нация, тяжёлый, X ур. | «Ледяной Великан». В исходной модели нет UV, поэтому текстура сгенерирована заново |
| [ussr_ms11](mods/ussr_ms11) | МС-11 | СССР, средний, X ур. | Коллекционный танк с барабаном на 11 снарядов (формат `burst_info` взят из файлов игры) |
| [germany_mauses](mods/germany_mauses) | Maus | Германия, тяжёлый, X ур. | 12,8 cm Kw.K. 44 L/55 |
| [germany_tashenratte](mods/germany_tashenratte) | Tashenratte | Германия, тяжёлый, X ур. | 13,2 cm Kw.K. L/66 |
| [sturmtiger](mods/sturmtiger) | Sturmtiger | Германия, ПТ, VIII ур. | 38 cm RaketenWerfer 61 |
| [russian_object279](mods/russian_object279) | Объект 279 | СССР, тяжёлый, X ур. | 130 мм М-65М |

Установка описана в README каждого мода.

## Инструменты

[`tools/resviewer.py`](tools) — просмотр файлов игры без распаковки: `resources.assets.resS`,
`.assets` (Unity), `.pck` / `.exe` (Godot). Показывает, что внутри, ищет слова (кнопка «Барабан»),
выводит строки и hex. Описание — в [`tools/README.md`](tools/README.md).

[`tools/scgviewer/`](tools/scgviewer) — просмотр 3D-моделей WoT Blitz `.sc2` / `.scg` (и сжатых `.dvpl`):
`scgviewer.html` открывается двойным щелчком в браузере, в него перетаскивается папка с моделью — видно
детали, LOD, материалы и текстуры, есть экспорт в GLB / OBJ. `scgtool.py` — то же из командной строки
(сводка, дерево, экспорт OBJ, распаковка `.dvpl`). Описание — в [`tools/scgviewer/README.md`](tools/scgviewer/README.md).

[`docs/tres_fields.md`](docs/tres_fields.md) — что известно о полях `.tres` танка: номера наций, новые поля,
барабан.

## Исходники

`tank1/` — исходные файлы 60TP (модель, текстуры, иконка), из которых собирается
мод `mods/poland_60tp`.

`tank2/` — исходные файлы Waffenträger E 110 (модель, текстуры, иконка), из
которых собирается мод `mods/germany_waffentrager_e110`.

`tank3/` — исходные файлы ЛВ-1300 Уран (модель без UV-развёртки, атлас
`Oth46_LV1300.dx11.png`, иконки), из которых собирается мод `mods/other_lv1300`.

`tank4/` — исходные файлы МС-11 (модель с развёрткой и вшитыми текстурами,
текстуры корпуса и трака, иконка), из которых собирается мод `mods/ussr_ms11`.

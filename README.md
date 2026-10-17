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
| [germany_mauses](mods/germany_mauses) | Maus | Германия, тяжёлый, X ур. | 12,8 cm Kw.K. 44 L/55 |
| [germany_tashenratte](mods/germany_tashenratte) | Tashenratte | Германия, тяжёлый, X ур. | 13,2 cm Kw.K. L/66 |
| [sturmtiger](mods/sturmtiger) | Sturmtiger | Германия, ПТ, VIII ур. | 38 cm RaketenWerfer 61 |
| [russian_object279](mods/russian_object279) | Объект 279 | СССР, тяжёлый, X ур. | 130 мм М-65М |

Установка описана в README каждого мода.

## Исходники

`tank1/` — исходные файлы 60TP (модель, текстуры, иконка), из которых собирается
мод `mods/poland_60tp`.

`tank2/` — исходные файлы Waffenträger E 110 (модель, текстуры, иконка), из
которых собирается мод `mods/germany_waffentrager_e110`.

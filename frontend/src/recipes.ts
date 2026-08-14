/**
 * Чтение справочника вольта на стороне клиента.
 *
 * Ровно одна задача: ответить, **что делается на этом станке**. Список
 * рецептов приходит из `build/recipes.json`, поэтому новый станок или рецепт
 * появляется в интерфейсе сам, без правки клиента (D-090, D-133).
 */

/** Канон имени станка: «Печь» и «Плавильная печь» — одно и то же. */
function канон(книга: any, имя: string | null): string | null {
  if (imя_пусто(имя)) return null;
  const синонимы: Record<string, string> = книга?.synonyms ?? {};
  return синонимы[имя as string] ?? (имя as string);
}

const imя_пусто = (имя: string | null) => имя === null || имя === "Руками";

/** Что игрок может сделать на этом станке (`null` — руками). */
export function craftableAt(
  книга: any,
  станок: string | null,
  знает: string[],
): string[] {
  if (!книга) return [];

  //: У блюда и монеты своя дверь: котёл считает роли, монетный двор — пробу.
  //: Признак — из данных вольта (`roles`, `kind`), а не из списка имён.
  const особые = new Set<string>(
    (книга.recipes ?? [])
      .filter((р: any) => р.roles || р.kind === "money")
      .map((р: any) => р.name),
  );

  const рецепты = (книга.recipes ?? [])
    .filter(
      (р: any) =>
        канон(книга, р.station) === станок &&
        !особые.has(р.name) &&
        знает.includes(р.name),
    )
    .map((р: any) => р.name as string);

  //: Операции без рецепта умеет каждый: плавка — граница между «добыл» и
  //: «сделал», и запирать её за знанием значило бы остановить экономику.
  const операции = (книга.operations ?? [])
    .filter(
      (o: any) =>
        (o.consumes ?? []).length > 0 &&
        (o.requires ?? []).some((чем: string) => канон(книга, чем) === станок),
    )
    .flatMap((o: any) => o.gives as string[]);

  return [...new Set([...рецепты, ...операции])]
    .filter((имя) => !особые.has(имя))
    .sort();
}

/** На каком станке делается эта вещь. Нужен, чтобы чинить у своего станка. */
export function stationOf(книга: any, имя: string): string | null {
  const рецепт = (книга?.recipes ?? []).find((р: any) => р.name === имя);
  return рецепт ? канон(книга, рецепт.station) : null;
}

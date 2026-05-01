"""
Примеры использования парсера 2ГИС.
"""

import asyncio
from parser_2gis import GisParser
from config_2gis import PARSING_CONFIG, PROXY_CONFIG


async def example_basic():
    """Пример 1: Базовый парсинг без прокси."""
    print("=" * 60)
    print("Пример 1: Базовый парсинг")
    print("=" * 60)
    
    # Отключаем прокси для примера
    PROXY_CONFIG["enabled"] = False
    
    # Парсим автосервисы в Казани
    url = "https://2gis.ru/kazan/search/автосервис"
    
    parser = GisParser(url)
    await parser.run()
    
    print(f"\nСобрано {len(parser.companies)} компаний")
    if parser.companies:
        print("\nПервая компания:")
        for key, value in parser.companies[0].items():
            print(f"  {key}: {value}")


async def example_with_proxy():
    """Пример 2: Парсинг с прокси."""
    print("=" * 60)
    print("Пример 2: Парсинг с прокси")
    print("=" * 60)
    
    # Включаем прокси
    PROXY_CONFIG["enabled"] = True
    
    # Если прокси не добавлены, выводим сообщение
    if not PROXY_CONFIG["proxies"]:
        print("⚠️  Прокси не добавлены в конфигурацию!")
        print("Добавьте прокси в config_2gis.py:")
        print('  PROXY_CONFIG["proxies"] = [')
        print('      "http://user:pass@proxy.com:8080",')
        print('  ]')
        return
    
    # Парсим кафе в Москве
    url = "https://2gis.ru/moscow/search/кафе"
    
    parser = GisParser(url)
    await parser.run()
    
    print(f"\nСобрано {len(parser.companies)} компаний")


async def example_limited():
    """Пример 3: Парсинг с ограничениями."""
    print("=" * 60)
    print("Пример 3: Парсинг с ограничениями")
    print("=" * 60)
    
    # Отключаем прокси
    PROXY_CONFIG["enabled"] = False
    
    # Ограничиваем количество компаний и страниц
    PARSING_CONFIG["max_companies"] = 10
    PARSING_CONFIG["max_pages"] = 1
    PARSING_CONFIG["open_detail_page"] = False  # Не открываем детальные страницы
    
    # Парсим парикмахерские в СПб
    url = "https://2gis.ru/spb/search/парикмахерская"
    
    parser = GisParser(url)
    await parser.run()
    
    print(f"\nСобрано {len(parser.companies)} компаний")


async def example_different_categories():
    """Пример 4: Парсинг разных категорий."""
    print("=" * 60)
    print("Пример 4: Парсинг разных категорий")
    print("=" * 60)
    
    # Отключаем прокси
    PROXY_CONFIG["enabled"] = False
    
    # Ограничиваем для примера
    PARSING_CONFIG["max_companies"] = 5
    PARSING_CONFIG["max_pages"] = 1
    PARSING_CONFIG["open_detail_page"] = False
    
    categories = [
        ("https://2gis.ru/kazan/search/аптека", "Аптеки в Казани"),
        ("https://2gis.ru/moscow/search/ресторан", "Рестораны в Москве"),
        ("https://2gis.ru/spb/search/отель", "Отели в СПб"),
    ]
    
    for url, description in categories:
        print(f"\nПарсинг: {description}")
        print("-" * 40)
        
        parser = GisParser(url)
        await parser.run()
        
        print(f"Собрано {len(parser.companies)} компаний")


async def main():
    """Запуск примеров."""
    print('CLI пример: python run_2gis_parser.py --url "https://2gis.ru/spb/search/автосервис" --max-companies 10')
    print("\n" + "=" * 60)
    print("ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ ПАРСЕРА 2ГИС")
    print("=" * 60 + "\n")
    
    print("Выберите пример для запуска:")
    print("1. Базовый парсинг (без прокси)")
    print("2. Парсинг с прокси")
    print("3. Парсинг с ограничениями")
    print("4. Парсинг разных категорий")
    print("0. Выход")
    
    try:
        choice = input("\nВыбор (0-4): ").strip()
        
        if choice == "1":
            await example_basic()
        elif choice == "2":
            await example_with_proxy()
        elif choice == "3":
            await example_limited()
        elif choice == "4":
            await example_different_categories()
        elif choice == "0":
            print("Выход...")
        else:
            print("Неверный выбор")
    
    except KeyboardInterrupt:
        print("\n\nПрограмма прервана пользователем")
    except Exception as e:
        print(f"\nОшибка: {e}")


if __name__ == "__main__":
    asyncio.run(main())
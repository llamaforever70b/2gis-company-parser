"""
Скрипт для запуска парсера 2ГИС с параметрами командной строки.
"""

import asyncio
import argparse
import sys
from pathlib import Path

from parser_2gis import GisParser


def parse_arguments():
    """Парсит аргументы командной строки."""
    parser = argparse.ArgumentParser(
        description="Парсер 2ГИС для сбора информации о компаниях",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  python run_2gis_parser.py --url "https://2gis.ru/kazan/search/автосервис"
  python run_2gis_parser.py --url "https://2gis.ru/moscow/search/кафе" --max-companies 50
  python run_2gis_parser.py --url "https://2gis.ru/spb/search/парикмахерская" --max-pages 3
  python run_2gis_parser.py --url "https://2gis.ru/kazan/search/автосервис" --proxy http://user:pass@proxy.com:8080
  python run_2gis_parser.py --url "https://2gis.ru/kazan/search/автосервис" --resume
  python run_2gis_parser.py --url "https://2gis.ru/kazan/search/автосервис" --parallel 3 --resume
        """,
    )

    parser.add_argument(
        "--url",
        type=str,
        required=True,
        help="URL категории на 2ГИС (например, https://2gis.ru/kazan/search/автосервис)",
    )

    parser.add_argument(
        "--max-companies",
        type=int,
        default=0,
        help="Максимальное количество компаний для сбора (0 = без ограничений, по умолчанию: 0)",
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Максимальное количество страниц (0 = без ограничений, по умолчанию: 0)",
    )

    parser.add_argument(
        "--no-detail",
        action="store_true",
        help="Не открывать детальные страницы компаний (быстрее, но меньше данных)",
    )

    parser.add_argument(
        "--output",
        type=str,
        default="output_2gis",
        help="Директория для сохранения результатов",
    )

    parser.add_argument(
        "--proxy",
        type=str,
        action="append",
        help="Прокси в формате http://user:pass@host:port (можно указать несколько раз)",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Продолжить парсинг с места остановки (использует сохранённый прогресс)",
    )

    parser.add_argument(
        "--parallel",
        type=int,
        metavar="N",
        help="Запустить N параллельных worker'ов для ускорения сбора (например, --parallel 3)",
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=["full", "quick", "detail"],
        default="full",
        help="Режим сбора: full (всё сразу), quick (только карточки), detail (добрать детали)",
    )

    return parser.parse_args()


async def main():
    """Основная функция."""
    args = parse_arguments()

    # Проверяем URL
    if not args.url.startswith("https://2gis.ru"):
        print("Ошибка: URL должен быть с сайта 2gis.ru")
        sys.exit(1)

    # Проверяем наличие openpyxl
    try:
        import openpyxl
        print("✓ openpyxl установлен")
    except ImportError:
        print("✗ Ошибка: openpyxl не установлен")
        print("Установите: pip install openpyxl")
        sys.exit(1)

    # Обновляем конфигурацию
    from config_2gis import PARSING_CONFIG, OUTPUT_CONFIG, PROXY_CONFIG
    import os

    PARSING_CONFIG["max_companies"] = args.max_companies
    PARSING_CONFIG["max_pages"] = args.max_pages
    PARSING_CONFIG["open_detail_page"] = not args.no_detail
    OUTPUT_CONFIG["output_dir"] = args.output

    # Настройка прокси из аргументов командной строки или переменной окружения
    if args.proxy:
        PROXY_CONFIG["enabled"] = True
        PROXY_CONFIG["proxies"] = args.proxy
        print(f"✓ Используются прокси из аргументов командной строки: {len(args.proxy)} шт.")
    elif os.environ.get("PROXY_LIST"):
        # Поддержка переменной окружения PROXY_LIST (прокси через запятую)
        proxy_list = [p.strip() for p in os.environ["PROXY_LIST"].split(",") if p.strip()]
        if proxy_list:
            PROXY_CONFIG["enabled"] = True
            PROXY_CONFIG["proxies"] = proxy_list
            print(f"✓ Используются прокси из переменной окружения PROXY_LIST: {len(proxy_list)} шт.")
    elif os.environ.get("HTTP_PROXY"):
        # Поддержка стандартной переменной HTTP_PROXY
        PROXY_CONFIG["enabled"] = True
        PROXY_CONFIG["proxies"] = [os.environ["HTTP_PROXY"]]
        print(f"✓ Используется прокси из переменной окружения HTTP_PROXY")
    elif not PROXY_CONFIG.get("enabled") or not PROXY_CONFIG.get("proxies"):
        print("⚠️  Прокси не настроены. Для массового сбора рекомендуется использовать прокси.")
        print("   Способы настройки:")
        print("   1. Аргумент --proxy: python run_2gis_parser.py --proxy http://proxy:port")
        print("   2. Переменная окружения: export PROXY_LIST='http://proxy1:port,http://proxy2:port'")
        print("   3. Файл config_2gis.py: PROXY_CONFIG['proxies'] = [...]")

    # Запускаем парсер
    try:
        # Режим Quick (только карточки)
        if args.mode == "quick":
            print(f"\n⚡ Режим QUICK: сбор только из карточек (без детальных страниц)")
            print("Производительность: ~2000 компаний/час")
            
            from collector_quick import QuickCollector
            from playwright.async_api import async_playwright
            import pandas as pd
            
            collector = QuickCollector(output_dir=args.output)
            
            # Запускаем браузер для сбора карточек
            playwright = await async_playwright().start()
            proxy_list = PROXY_CONFIG.get("proxies", []) if PROXY_CONFIG.get("enabled", False) else []
            
            from proxy_manager import ProxyManager
            proxy_manager = ProxyManager(proxy_list)
            proxy = proxy_manager.get_proxy()
            
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
                proxy=proxy,
            )
            
            context = await browser.new_context()
            page = await context.new_page()
            
            # Переходим на категорию
            await page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1)
            
            # Собираем карточки со всех страниц
            page_num = 1
            max_pages = args.max_pages if args.max_pages > 0 else 999
            
            while page_num <= max_pages:
                print(f"Страница {page_num}...")
                results = await collector.collect_from_search_page(page)
                collector.add_results(results)
                
                if args.max_companies > 0 and len(collector.get_results()) >= args.max_companies:
                    break
                
                # Переход на следующую страницу
                page_num += 1
                next_url = args.url.rstrip('/') + f"/page/{page_num}"
                try:
                    await page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(0.5)
                except Exception:
                    break
            
            await browser.close()
            await playwright.stop()
            
            # Сохраняем результаты
            json_path = collector.save_results("quick_results.json")
            
            # Сохраняем в CSV/Excel
            results = collector.get_results()
            if results:
                from pathlib import Path
                output_dir = Path(args.output)
                
                df = pd.DataFrame(results)
                csv_path = output_dir / "companies_2gis_quick.csv"
                df.to_csv(csv_path, index=False, encoding="utf-8-sig")
                print(f"✓ CSV сохранен: {csv_path}")
                
                try:
                    excel_path = output_dir / "companies_2gis_quick.xlsx"
                    df.to_excel(excel_path, index=False, engine="openpyxl")
                    print(f"✓ Excel сохранен: {excel_path}")
                except Exception as e:
                    print(f"⚠️  Ошибка сохранения Excel: {e}")
                
                stats = collector.get_stats()
                print(f"\n✓ Quick-сбор завершен!")
                print(f"Собрано компаний: {stats['total_companies']}")
                print(f"С рейтингом: {stats['with_rating']}")
                print(f"С отзывами: {stats['with_reviews']}")
                print(f"\nДля сбора детальных данных запустите:")
                print(f"python run_2gis_parser.py --url \"{args.url}\" --mode detail")
        
        # Режим Detail (добрать детали)
        elif args.mode == "detail":
            print(f"\n🔍 Режим DETAIL: сбор детальных данных")
            
            from collector_detail import DetailCollector
            from playwright.async_api import async_playwright
            import pandas as pd
            from pathlib import Path
            
            # Ищем файл с quick результатами
            quick_file = Path(args.output) / "quick_results.json"
            if not quick_file.exists():
                print(f"✗ Ошибка: файл {quick_file} не найден")
                print("Сначала запустите режим quick:")
                print(f"python run_2gis_parser.py --url \"{args.url}\" --mode quick")
                sys.exit(1)
            
            collector = DetailCollector(str(quick_file), output_dir=args.output)
            urls = collector.get_urls_to_process()
            
            print(f"Найдено {len(urls)} URL для обработки")
            
            # Запускаем браузер
            playwright = await async_playwright().start()
            proxy_list = PROXY_CONFIG.get("proxies", []) if PROXY_CONFIG.get("enabled", False) else []
            
            from proxy_manager import ProxyManager
            proxy_manager = ProxyManager(proxy_list)
            proxy = proxy_manager.get_proxy()
            
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
                proxy=proxy,
            )
            
            context = await browser.new_context()
            page = await context.new_page()
            
            # Обрабатываем каждый URL
            for i, url in enumerate(urls, 1):
                if args.max_companies > 0 and i > args.max_companies:
                    break
                
                print(f"[{i}/{len(urls)}] {url}")
                detail_data = await collector.collect_detail(page, url)
                collector.enrich_data(url, detail_data)
                
                await asyncio.sleep(0.5)
            
            await browser.close()
            await playwright.stop()
            
            # Сохраняем обогащенные результаты
            json_path = collector.save_enriched_results("full_results.json")
            
            # Сохраняем в CSV/Excel
            results = collector.get_enriched_results()
            if results:
                output_dir = Path(args.output)
                
                df = pd.DataFrame(results)
                csv_path = output_dir / "companies_2gis_full.csv"
                df.to_csv(csv_path, index=False, encoding="utf-8-sig")
                print(f"✓ CSV сохранен: {csv_path}")
                
                try:
                    excel_path = output_dir / "companies_2gis_full.xlsx"
                    df.to_excel(excel_path, index=False, engine="openpyxl")
                    print(f"✓ Excel сохранен: {excel_path}")
                except Exception as e:
                    print(f"⚠️  Ошибка сохранения Excel: {e}")
                
                stats = collector.get_stats()
                print(f"\n✓ Detail-сбор завершен!")
                print(f"Обогащено компаний: {stats['total_enriched']}")
                print(f"С телефонами: {stats['with_phones']}")
                print(f"С сайтами: {stats['with_website']}")
                print(f"С часами работы: {stats['with_hours']}")
        
        # Режим Full или параллельный
        elif args.parallel:
            if args.parallel < 1:
                print("Ошибка: количество worker'ов должно быть >= 1")
                sys.exit(1)
            
            print(f"\n🚀 Запуск в параллельном режиме ({args.parallel} worker'ов)")
            
            from parallel_parser import parallel_main
            import pandas as pd
            
            # Получаем список прокси
            proxy_list = PROXY_CONFIG.get("proxies", []) if PROXY_CONFIG.get("enabled", False) else []
            
            # Запускаем параллельный парсинг
            results = await parallel_main(
                category_url=args.url,
                num_workers=args.parallel,
                enable_resume=args.resume,
                proxy_list=proxy_list,
                output_dir=args.output,
                max_companies=args.max_companies,
            )
            
            # Сохраняем результаты
            if results:
                from pathlib import Path
                output_dir = Path(args.output)
                output_dir.mkdir(parents=True, exist_ok=True)
                
                # CSV
                df = pd.DataFrame(results)
                csv_path = output_dir / "companies_2gis.csv"
                df.to_csv(csv_path, index=False, encoding="utf-8-sig")
                print(f"✓ CSV сохранен: {csv_path}")
                
                # Excel
                try:
                    excel_path = output_dir / "companies_2gis.xlsx"
                    df.to_excel(excel_path, index=False, engine="openpyxl")
                    print(f"✓ Excel сохранен: {excel_path}")
                except Exception as e:
                    print(f"⚠️  Ошибка сохранения Excel: {e}")
                
                print(f"\n✓ Параллельный парсинг завершен!")
                print(f"Собрано компаний: {len(results)}")
                print(f"Результаты сохранены в папке '{args.output}'")
            else:
                print("⚠️  Не удалось собрать данные")
        
        # Обычный режим (full)
        else:
            parser = GisParser(args.url, enable_resume=args.resume)
            await parser.run()
            print(f"\n✓ Парсинг завершен успешно!")
            print(f"Результаты сохранены в папке '{args.output}'")
    except KeyboardInterrupt:
        print("\n\nПарсинг прерван пользователем")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Ошибка: {e}")
        if "IP заблокирован" in str(e) or "blocked" in str(e).lower():
            print("\n⚠️  Похоже, ваш IP заблокирован 2ГИС")
            print("Решение: используйте прокси")
            print("Отредактируйте config_2gis.py и включите PROXY_CONFIG")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

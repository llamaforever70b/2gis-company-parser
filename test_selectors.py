"""
Тестовый скрипт для проверки селекторов 2ГИС.
Открывает страницу и показывает, какие элементы найдены.
"""

import asyncio
from playwright.async_api import async_playwright


async def test_selectors():
    """Тестирует селекторы на реальной странице 2ГИС."""
    
    url = "https://2gis.ru/msk/search/цветочный магазин"
    
    print(f"🔍 Тестирование селекторов на: {url}\n")
    
    playwright = await async_playwright().start()
    
    browser = await playwright.chromium.launch(
        headless=False,  # Видимый режим для отладки
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ]
    )
    
    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    
    page = await context.new_page()
    
    print("📄 Загрузка страницы...")
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    
    # Ждем загрузки контента
    print("⏳ Ожидание загрузки контента (10 сек)...")
    await asyncio.sleep(10)
    
    # Тестируем различные селекторы
    selectors_to_test = {
        "Карточки компаний (основной)": "div[data-qa='search-result-item']",
        "Карточки компаний (класс)": "div._1kf6gff",
        "Карточки компаний (search-result)": "div[class*='search-result']",
        "Карточки компаний (listitem)": "div[role='listitem']",
        "Любые div с data-qa": "div[data-qa]",
        "Любые article": "article",
        "Любые li": "li",
    }
    
    print("\n" + "="*70)
    print("РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ СЕЛЕКТОРОВ")
    print("="*70 + "\n")
    
    for name, selector in selectors_to_test.items():
        try:
            elements = await page.query_selector_all(selector)
            count = len(elements)
            
            if count > 0:
                print(f"✅ {name}")
                print(f"   Селектор: {selector}")
                print(f"   Найдено: {count} элементов")
                
                # Показываем первый элемент
                if count > 0:
                    first_elem = elements[0]
                    text = await first_elem.text_content()
                    text_preview = text[:100] if text else "(пусто)"
                    print(f"   Первый элемент: {text_preview}")
                print()
            else:
                print(f"❌ {name}")
                print(f"   Селектор: {selector}")
                print(f"   Найдено: 0 элементов")
                print()
                
        except Exception as e:
            print(f"⚠️  {name}")
            print(f"   Селектор: {selector}")
            print(f"   Ошибка: {e}")
            print()
    
    # Сохраняем скриншот
    screenshot_path = "test_page_screenshot.png"
    await page.screenshot(path=screenshot_path, full_page=True)
    print(f"📸 Скриншот сохранен: {screenshot_path}")
    
    # Сохраняем HTML
    html_path = "test_page_content.html"
    content = await page.content()
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"💾 HTML сохранен: {html_path}")
    
    print("\n⏸️  Браузер останется открытым 30 секунд для проверки...")
    await asyncio.sleep(30)
    
    await browser.close()
    await playwright.stop()
    
    print("\n✅ Тестирование завершено!")


if __name__ == "__main__":
    asyncio.run(test_selectors())

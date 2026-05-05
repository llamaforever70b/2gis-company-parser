"""
Тестовый скрипт для проверки обновленной системы детекции Яндекс SmartCaptcha.
"""

import asyncio
from playwright.async_api import async_playwright


async def test_captcha_detection():
    """Тест детекции различных вариантов Яндекс SmartCaptcha."""
    
    print("=" * 70)
    print("ТЕСТ: Детекция Яндекс SmartCaptcha")
    print("=" * 70)
    
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        # Тест 1: Страница с селектором .smart-captcha
        print("\n📋 Тест 1: Селектор .smart-captcha")
        await page.set_content("""
            <html>
                <body>
                    <div class="smart-captcha">Капча</div>
                </body>
            </html>
        """)
        
        result1 = await check_captcha(page)
        print(f"   Результат: {'✓ ОБНАРУЖЕНА' if result1 else '✗ НЕ ОБНАРУЖЕНА'}")
        
        # Тест 2: Страница с iframe Яндекс Captcha API
        print("\n📋 Тест 2: iframe с captcha-api.yandex.ru")
        await page.set_content("""
            <html>
                <body>
                    <iframe src="https://captcha-api.yandex.ru/captcha"></iframe>
                </body>
            </html>
        """)
        
        result2 = await check_captcha(page)
        print(f"   Результат: {'✓ ОБНАРУЖЕНА' if result2 else '✗ НЕ ОБНАРУЖЕНА'}")
        
        # Тест 3: Страница с #captcha-container
        print("\n📋 Тест 3: Контейнер #captcha-container")
        await page.set_content("""
            <html>
                <body>
                    <div id="captcha-container">Капча</div>
                </body>
            </html>
        """)
        
        result3 = await check_captcha(page)
        print(f"   Результат: {'✓ ОБНАРУЖЕНА' if result3 else '✗ НЕ ОБНАРУЖЕНА'}")
        
        # Тест 4: Страница с русским текстом капчи
        print("\n📋 Тест 4: Русский текст 'Подтвердите, что вы не робот'")
        await page.set_content("""
            <html>
                <body>
                    <h1>Подтвердите, что вы не робот</h1>
                    <p>Для продолжения работы введите символы с картинки</p>
                </body>
            </html>
        """)
        
        result4 = await check_captcha(page)
        print(f"   Результат: {'✓ ОБНАРУЖЕНА' if result4 else '✗ НЕ ОБНАРУЖЕНА'}")
        
        # Тест 5: Страница с текстом "Подозрительная активность"
        print("\n📋 Тест 5: Русский текст 'Подозрительная активность'")
        await page.set_content("""
            <html>
                <body>
                    <h1>Подозрительная активность</h1>
                    <p>Мы обнаружили необычную активность с вашего IP-адреса</p>
                </body>
            </html>
        """)
        
        result5 = await check_captcha(page)
        print(f"   Результат: {'✓ ОБНАРУЖЕНА' if result5 else '✗ НЕ ОБНАРУЖЕНА'}")
        
        # Тест 6: Обычная страница БЕЗ капчи
        print("\n📋 Тест 6: Обычная страница (без капчи)")
        await page.set_content("""
            <html>
                <body>
                    <h1>Автосервисы в Казани</h1>
                    <div class="company-card">
                        <h2>Автосервис №1</h2>
                        <p>Адрес: ул. Ленина, 1</p>
                    </div>
                </body>
            </html>
        """)
        
        result6 = await check_captcha(page)
        print(f"   Результат: {'✗ НЕ ОБНАРУЖЕНА' if not result6 else '✓ ОБНАРУЖЕНА (ОШИБКА!)'}")
        
        # Тест 7: Страница с Google reCAPTCHA (НЕ должна обнаруживаться как приоритет)
        print("\n📋 Тест 7: Google reCAPTCHA (старая логика)")
        await page.set_content("""
            <html>
                <body>
                    <div class="g-recaptcha">Google Captcha</div>
                    <iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe>
                </body>
            </html>
        """)
        
        result7 = await check_captcha(page)
        print(f"   Результат: {'✓ ОБНАРУЖЕНА (fallback)' if result7 else '✗ НЕ ОБНАРУЖЕНА'}")
        
        await browser.close()
        
        # Итоги
        print("\n" + "=" * 70)
        print("ИТОГИ ТЕСТИРОВАНИЯ")
        print("=" * 70)
        
        tests_passed = sum([result1, result2, result3, result4, result5, not result6])
        total_tests = 6  # Тест 7 не учитываем в итогах
        
        print(f"\nПройдено тестов: {tests_passed}/{total_tests}")
        
        if tests_passed == total_tests:
            print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        else:
            print("⚠️  НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОШЛИ")
        
        print("\nОбновленная система детекции:")
        print("  ✓ Яндекс SmartCaptcha (.smart-captcha)")
        print("  ✓ iframe с captcha-api.yandex.ru")
        print("  ✓ Контейнер #captcha-container")
        print("  ✓ Русский текст капчи")
        print("  ✓ Fallback для общих селекторов")
        print("\n" + "=" * 70)


async def check_captcha(page) -> bool:
    """
    Проверяет наличие Яндекс SmartCaptcha на странице.
    Копия логики из parser_2gis.py для тестирования.
    """
    try:
        # Проверка 1: Селектор Яндекс SmartCaptcha
        if await page.query_selector(".smart-captcha"):
            return True
        
        # Проверка 2: iframe с Яндекс Captcha API
        if await page.query_selector("iframe[src*='captcha-api.yandex.ru']"):
            return True
        
        # Проверка 3: Контейнер капчи по ID
        if await page.query_selector("#captcha-container"):
            return True
        
        # Проверка 4: Общий селектор для любых капч (fallback)
        if await page.query_selector("[class*='captcha'], [id*='captcha']"):
            return True
        
        # Проверка 5: Русский текст капчи в body
        try:
            body_text = await page.inner_text("body")
            if body_text:
                body_lower = body_text.lower()
                
                # Яндекс SmartCaptcha фразы
                captcha_phrases = [
                    "подтвердите, что вы не робот",
                    "подтвердите что вы не робот",
                    "введите символы",
                    "подозрительная активность",
                    "проверка безопасности",
                    "captcha",
                ]
                
                for phrase in captcha_phrases:
                    if phrase in body_lower:
                        return True
        except Exception:
            pass
        
        return False
        
    except Exception:
        return False


if __name__ == "__main__":
    asyncio.run(test_captcha_detection())

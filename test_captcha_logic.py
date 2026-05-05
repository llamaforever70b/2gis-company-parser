"""
Упрощенный тест логики детекции Яндекс SmartCaptcha (без Playwright).
"""


def test_captcha_phrases():
    """Тест проверки фраз капчи."""
    
    print("=" * 70)
    print("ТЕСТ: Логика детекции Яндекс SmartCaptcha")
    print("=" * 70)
    
    # Фразы для детекции
    captcha_phrases = [
        "подтвердите, что вы не робот",
        "подтвердите что вы не робот",
        "введите символы",
        "подозрительная активность",
        "проверка безопасности",
        "captcha",
    ]
    
    # Тестовые случаи
    test_cases = [
        {
            "name": "Яндекс капча - полная фраза",
            "text": "Подтвердите, что вы не робот. Для продолжения работы введите символы.",
            "should_detect": True
        },
        {
            "name": "Яндекс капча - подозрительная активность",
            "text": "Обнаружена подозрительная активность с вашего IP-адреса",
            "should_detect": True
        },
        {
            "name": "Яндекс капча - введите символы",
            "text": "Введите символы с картинки для продолжения",
            "should_detect": True
        },
        {
            "name": "Яндекс капча - проверка безопасности",
            "text": "Проверка безопасности. Пожалуйста, подождите.",
            "should_detect": True
        },
        {
            "name": "Обычная страница - автосервисы",
            "text": "Автосервисы в Казани. Найдено 1234 компании. Рейтинг 4.5",
            "should_detect": False
        },
        {
            "name": "Обычная страница - контакты",
            "text": "Контакты компании. Телефон: +7 (123) 456-78-90. Адрес: ул. Ленина, 1",
            "should_detect": False
        },
        {
            "name": "Капча - слово captcha",
            "text": "Please solve the captcha to continue",
            "should_detect": True
        },
    ]
    
    print("\n📋 Тестирование фраз детекции:\n")
    
    passed = 0
    failed = 0
    
    for i, test_case in enumerate(test_cases, 1):
        text_lower = test_case["text"].lower()
        detected = any(phrase in text_lower for phrase in captcha_phrases)
        
        expected = test_case["should_detect"]
        success = detected == expected
        
        status = "✓" if success else "✗"
        result = "ОБНАРУЖЕНА" if detected else "НЕ ОБНАРУЖЕНА"
        
        print(f"{status} Тест {i}: {test_case['name']}")
        print(f"   Текст: {test_case['text'][:60]}...")
        print(f"   Результат: {result}")
        print(f"   Ожидалось: {'ОБНАРУЖЕНА' if expected else 'НЕ ОБНАРУЖЕНА'}")
        
        if success:
            passed += 1
        else:
            failed += 1
        
        print()
    
    # Итоги
    print("=" * 70)
    print("ИТОГИ ТЕСТИРОВАНИЯ")
    print("=" * 70)
    print(f"\nПройдено тестов: {passed}/{len(test_cases)}")
    print(f"Провалено тестов: {failed}/{len(test_cases)}")
    
    if failed == 0:
        print("\n✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
    else:
        print(f"\n⚠️  {failed} ТЕСТ(ОВ) НЕ ПРОШЛИ")
    
    print("\n" + "=" * 70)
    print("ОБНОВЛЕННАЯ СИСТЕМА ДЕТЕКЦИИ")
    print("=" * 70)
    print("\n🔍 Селекторы:")
    print("  • .smart-captcha")
    print("  • iframe[src*='captcha-api.yandex.ru']")
    print("  • #captcha-container")
    print("  • [class*='captcha'], [id*='captcha'] (fallback)")
    
    print("\n📝 Русские фразы:")
    for phrase in captcha_phrases:
        print(f"  • \"{phrase}\"")
    
    print("\n⚙️  Логика при обнаружении капчи:")
    print("  1. mark_failure('captcha') - отмечаем ошибку прокси")
    print("  2. reinit_browser_with_new_proxy() - пересоздаем браузер")
    print("  3. НЕ вызываем mark_success() - избегаем сбора пустых карточек")
    
    print("\n" + "=" * 70)


if __name__ == "__main__":
    test_captcha_phrases()

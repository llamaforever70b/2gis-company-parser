"""
Тестовый скрипт для проверки исправлений логики работы с прокси.
"""

import asyncio
from proxy_manager import ProxyManager

async def test_proxy_rotation():
    """Тест ротации прокси с worker_id."""
    print("=" * 70)
    print("ТЕСТ 1: Проверка распределения прокси между воркерами")
    print("=" * 70)
    
    # Создаем список тестовых прокси
    test_proxies = [
        "http://user1:pass1@proxy1.example.com:8080",
        "http://user2:pass2@proxy2.example.com:8080",
        "http://user3:pass3@proxy3.example.com:8080",
    ]
    
    proxy_manager = ProxyManager(test_proxies)
    
    # Симулируем 3 воркера, получающих прокси одновременно
    print("\nСимуляция запуска 3 воркеров:")
    worker_proxies = {}
    
    for worker_id in range(1, 4):
        proxy = proxy_manager.get_proxy(worker_id=worker_id)
        worker_proxies[worker_id] = proxy['server'] if proxy else None
        print(f"  Worker {worker_id}: {proxy['server'] if proxy else 'No proxy'}")
    
    # Проверяем, что воркеры получили разные прокси
    unique_proxies = set(worker_proxies.values())
    if len(unique_proxies) == 3:
        print("\n✓ УСПЕХ: Все воркеры получили разные прокси")
    else:
        print(f"\n✗ ОШИБКА: Воркеры получили одинаковые прокси: {worker_proxies}")
    
    print("\n" + "=" * 70)
    print("ТЕСТ 2: Проверка случайного выбора при одинаковой статистике")
    print("=" * 70)
    
    # Создаем новый менеджер
    proxy_manager2 = ProxyManager(test_proxies)
    
    # Получаем прокси несколько раз без worker_id
    print("\nПолучение прокси без worker_id (5 раз):")
    selected_proxies = []
    for i in range(5):
        proxy = proxy_manager2.get_proxy()
        selected_proxies.append(proxy['server'] if proxy else None)
        print(f"  Попытка {i+1}: {proxy['server'] if proxy else 'No proxy'}")
    
    # Проверяем разнообразие
    unique_selected = set(selected_proxies)
    if len(unique_selected) > 1:
        print(f"\n✓ УСПЕХ: Выбраны разные прокси ({len(unique_selected)} уникальных)")
    else:
        print(f"\n⚠️  ВНИМАНИЕ: Все запросы вернули один прокси (может быть случайностью)")
    
    print("\n" + "=" * 70)
    print("ТЕСТ 3: Проверка метода switch_proxy")
    print("=" * 70)
    
    proxy_manager3 = ProxyManager(test_proxies)
    
    # Получаем первый прокси
    proxy1 = proxy_manager3.get_proxy()
    print(f"\nПервый прокси: {proxy1['server'] if proxy1 else 'No proxy'}")
    print(f"Current proxy: {proxy_manager3.current_proxy}")
    
    # Переключаем прокси
    proxy_manager3.switch_proxy("test")
    print(f"\nПосле switch_proxy:")
    print(f"Current proxy: {proxy_manager3.current_proxy}")
    
    # Получаем следующий прокси
    proxy2 = proxy_manager3.get_proxy()
    print(f"\nСледующий прокси: {proxy2['server'] if proxy2 else 'No proxy'}")
    
    if proxy1['server'] != proxy2['server']:
        print("\n✓ УСПЕХ: switch_proxy корректно сбросил текущий прокси")
    else:
        print("\n⚠️  ВНИМАНИЕ: Получен тот же прокси (может быть случайностью)")
    
    print("\n" + "=" * 70)
    print("Все тесты завершены")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(test_proxy_rotation())

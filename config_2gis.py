"""
Конфигурация для парсера 2ГИС.
"""

# ============================================================================
# ПРОКСИ КОНФИГУРАЦИЯ
# ============================================================================
# Формат: http://user:pass@ip:port или http://ip:port
# Без прокси массовый сбор невозможен - 2ГИС блокирует запросы
PROXY_CONFIG = {
    "enabled": True,
    "proxies": [
        # Добавьте ваши прокси в формате:
        # "http://user:pass@proxy1.com:8080",
        # "http://proxy2.com:3128",
        # "http://user:pass@192.168.1.1:8080",
    ],
    "rotate": True,  # Ротация прокси между запросами
    "timeout": 30,  # Таймаут подключения к прокси
}

# ============================================================================
# БРАУЗЕР КОНФИГУРАЦИЯ
# ============================================================================
BROWSER_CONFIG = {
    "headless": False,  # False для отладки, True для production
    "viewport": {"width": 1920, "height": 1080},
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "args": [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-site-isolation-trials",
        "--disable-web-security",
        "--disable-features=VizDisplayCompositor",
    ],
}

# ============================================================================
# ТАЙМАУТЫ И ЗАДЕРЖКИ
# ============================================================================
TIMEOUTS = {
    "page_load": 45000,      # 45 сек (карта 2ГИС тяжелая, +50% от базовых 30s)
    "navigation": 30000,     # 30 сек (переход между страницами, +50% от базовых 20s)
    "element_wait": 20000,   # 20 сек (поиск селектора, +33% от базовых 15s)
    "network_idle": 15000,   # 15 сек (ожидание сети, +50% от базовых 10s)
}

DELAYS = {
    "min_click": 0.5,  # Минимальная задержка между запросами (сек)
    "max_click": 1.5,  # Максимальная задержка между запросами (сек)
    "min_scroll": 0.2,  # Минимальная задержка при скролле (сек)
    "max_scroll": 0.5,  # Максимальная задержка при скролле (сек)
    "min_page": 0.5,  # Минимальная задержка между страницами (сек)
    "max_page": 1.5,  # Максимальная задержка между страницами (сек)
}

# ============================================================================
# СЕЛЕКТОРЫ 2ГИС
# ============================================================================
# Стратегия: Используем CSS selector list (запятая разделяет альтернативы)
# Браузер ищет первый найденный элемент из списка
# Это гарантирует работу при изменении HTML структуры 2GIS
# ============================================================================

SELECTORS = {
    # ========================================================================
    # КАРТОЧКА КОМПАНИИ В СПИСКЕ РЕЗУЛЬТАТОВ
    # ========================================================================
    # Основной селектор (data-qa атрибут)
    # Альтернативные селекторы (классы и role атрибуты)
    "company_card": (
        "div[data-qa='search-result-item'], "           # Основной селектор
        "div._1kf6gff, "                                 # Класс карточки
        "div[class*='search-result'], "                  # Содержит 'search-result'
        "li[data-qa], "                                  # Li элемент с data-qa
        "div[role='listitem'], "                         # Role атрибут
        "div[class*='_card']"                            # Содержит '_card'
    ),
    
    # ========================================================================
    # НАЗВАНИЕ КОМПАНИИ
    # ========================================================================
    "company_name": (
        "a[data-qa='search-result-title'], "             # Основной селектор
        "a[class*='title'], "                            # Ссылка с 'title'
        "a[class*='name'], "                             # Ссылка с 'name'
        "span[class*='name'], "                          # Span с 'name'
        "a[data-qa*='title'], "                          # data-qa содержит 'title'
        "div[class*='_title']"                           # Содержит '_title'
    ),
    
    # ========================================================================
    # АДРЕС КОМПАНИИ
    # ========================================================================
    # Массив селекторов для поиска адреса
    # Включает поиск по классам, data-qa и текстовому содержимому
    "company_address": [
        # CSS селекторы (основной приоритет)
        "span[data-qa='search-result-address']",        # Основной селектор
        "span[data-qa='card-address']",                 # Альтернативный data-qa
        "span[class*='address']",                       # Span с 'address'
        "div[class*='address']",                        # Div с 'address'
        "span[data-qa*='address']",                     # data-qa содержит 'address'
        "div[class*='_address']",                       # Содержит '_address'
        "span[class*='_address']",                      # Span содержит '_address'
        # XPath селекторы для поиска по текстовому содержимому
        "//*[not(self::script)][contains(text(), 'улица')]",               # Содержит "улица"
        "//*[not(self::script)][contains(text(), 'проспект')]",            # Содержит "проспект"
        "//*[not(self::script)][contains(text(), 'шоссе')]",               # Содержит "шоссе"
        "//*[not(self::script)][contains(text(), 'переулок')]",            # Содержит "переулок"
        "//*[not(self::script)][contains(text(), 'г. ')]",                 # Содержит "г. " (город)
    ],
    
    # ========================================================================
    # РЕЙТИНГ КОМПАНИИ
    # ========================================================================
    "company_rating": (
        "[itemprop='ratingValue'], "                     # Schema.org микроразметка
        "span[data-qa='rating-value'], "                 # Основной селектор
        "span[class*='rating'], "                        # Span с 'rating'
        "div[class*='rating'], "                         # Div с 'rating'
        "span[data-qa*='rating'], "                      # data-qa содержит 'rating'
        "div[class*='_rating'], "                        # Содержит '_rating'
        "span[class*='_rating'], "                       # Span содержит '_rating'
        "meta[itemprop='ratingValue']"                   # Meta тег с рейтингом
    ),
    
    # ========================================================================
    # КОЛИЧЕСТВО ОТЗЫВОВ
    # ========================================================================
    "company_reviews": (
        "span[data-qa='reviews-count'], "                # Основной селектор
        "span[class*='reviews'], "                       # Span с 'reviews'
        "span[class*='review'], "                        # Span с 'review'
        "span[class*='count'], "                         # Span с 'count'
        "div[class*='review'], "                         # Div с 'review'
        "span[data-qa*='review'], "                      # data-qa содержит 'review'
        "div[class*='_review'], "                        # Содержит '_review'
        "span[class*='_review'], "                       # Span содержит '_review'
        "a[href*='reviews'], "                           # Ссылка на отзывы
        "[itemprop='reviewCount']"                       # Schema.org микроразметка
    ),
    
    # ========================================================================
    # КАТЕГОРИЯ КОМПАНИИ
    # ========================================================================
    "company_category": (
        "nav[aria-label='Breadcrumb'] span:last-child, " # Хлебные крошки (приоритет)
        "nav[class*='breadcrumb'] span:last-child, "     # Альтернативные хлебные крошки
        "span[data-qa='search-result-category'], "       # Основной селектор
        "span[data-qa='card-category'], "                # Альтернативный data-qa
        "span[class*='category'], "                      # Span с 'category'
        "div[class*='category'], "                       # Div с 'category'
        "span[class*='type'], "                          # Span с 'type'
        "span[data-qa*='category'], "                    # data-qa содержит 'category'
        "div[class*='_category'], "                      # Содержит '_category'
        "span[class*='_category'], "                     # Span содержит '_category'
        "span[class*='breadcrumb']:last-child"           # Последний элемент breadcrumb
    ),
    
    # ========================================================================
    # ДЕТАЛЬНАЯ ИНФОРМАЦИЯ (НА СТРАНИЦЕ КОМПАНИИ)
    # ========================================================================
    
    # ТЕЛЕФОН
    "detail_phone": (
        "a[href^='tel:'], "                              # Ссылка tel:
        "a[class*='phone'], "                            # Ссылка с 'phone'
        "span[class*='phone'], "                         # Span с 'phone'
        "div[class*='phone'], "                          # Div с 'phone'
        "a[data-qa*='phone'], "                          # data-qa содержит 'phone'
        "div[class*='_phone']"                           # Содержит '_phone'
    ),
    
    # ВЕБ-САЙТ
    "detail_website": (
        "a[data-qa='website-link'], "                    # Основной селектор
        "a[class*='website'], "                          # Ссылка с 'website'
        "a[class*='link'], "                             # Ссылка с 'link'
        "a[href*='http'], "                              # Ссылка с http
        "a[data-qa*='website'], "                        # data-qa содержит 'website'
        "a[class*='_website']"                           # Содержит '_website'
    ),
    
    # ЧАСЫ РАБОТЫ
    # Массив селекторов для поиска часов работы
    # Включает поиск по классам, data-qa и текстовому содержимому
    "detail_hours": [
        # CSS селекторы (основной приоритет)
        "div[data-qa='schedule-item']",                 # Основной селектор
        "div[data-qa='card-schedule']",                 # Альтернативный data-qa
        "div[class*='schedule']",                       # Div с 'schedule'
        "div[class*='hours']",                          # Div с 'hours'
        "div[class*='time']",                           # Div с 'time'
        "div[data-qa*='schedule']",                     # data-qa содержит 'schedule'
        "div[class*='_schedule']",                      # Содержит '_schedule'
        "div[class*='_hours']",                         # Содержит '_hours'
        "div[class*='_time']",                          # Содержит '_time'
        # XPath селекторы для поиска по текстовому содержимому
        "//*[not(self::script)][contains(text(), 'Ежедневно')]",          # Содержит "Ежедневно"
        "//*[not(self::script)][contains(text(), 'Сегодня')]",            # Содержит "Сегодня"
        "//*[not(self::script)][contains(text(), 'Круглосуточно')]",      # Содержит "Круглосуточно"
        "//*[not(self::script)][contains(text(), 'До ')]",                # Содержит "До " (время закрытия)
    ],
    
    # ОПИСАНИЕ
    "detail_description": (
        "div[data-qa='about-text'], "                    # Основной селектор
        "div[class*='about'], "                          # Div с 'about'
        "div[class*='description'], "                    # Div с 'description'
        "div[class*='text'], "                           # Div с 'text'
        "div[data-qa*='about'], "                        # data-qa содержит 'about'
        "div[class*='_about'], "                         # Содержит '_about'
        "div[class*='_description']"                     # Содержит '_description'
    ),
    
    # ========================================================================
    # НАВИГАЦИЯ
    # ========================================================================
    
    # КНОПКА "ДАЛЕЕ"
    "next_page": (
        "a[data-qa='pagination-next'], "                 # Основной селектор
        "button[class*='next'], "                        # Кнопка с 'next'
        "a[class*='next'], "                             # Ссылка с 'next'
        "a[data-qa*='next'], "                           # data-qa содержит 'next'
        "button[data-qa*='next'], "                      # Кнопка data-qa содержит 'next'
        "div[class*='_next']"                            # Содержит '_next'
    ),
    
    # КНОПКА "ЗАГРУЗИТЬ ЕЩЕ"
    "load_more": (
        "button[data-qa='load-more'], "                  # Основной селектор
        "button[class*='load-more'], "                   # Кнопка с 'load-more'
        "button[class*='more'], "                        # Кнопка с 'more'
        "button[class*='load'], "                        # Кнопка с 'load'
        "button[data-qa*='load'], "                      # data-qa содержит 'load'
        "a[class*='load-more'], "                        # Ссылка с 'load-more'
        "div[class*='_load-more']"                       # Содержит '_load-more'
    ),
    
    # ========================================================================
    # КОНТЕЙНЕР СПИСКА РЕЗУЛЬТАТОВ (ДЛЯ ПРОКРУТКИ)
    # ========================================================================
    "results_container": (
        "div[data-qa='search-results'], "                # Основной селектор
        "div[class*='search-results'], "                 # Div с 'search-results'
        "div[class*='results-list'], "                   # Div с 'results-list'
        "div[role='list'], "                             # Role атрибут
        "div[class*='_results'], "                       # Содержит '_results'
        "ul[class*='results']"                           # UL с 'results'
    ),
    
    # ========================================================================
    # ДОПОЛНИТЕЛЬНЫЕ СЕЛЕКТОРЫ ДЛЯ ДЕТАЛЬНОЙ СТРАНИЦЫ
    # ========================================================================
    
    "detail_address": (
        "span[data-qa*='address'], "                     # data-qa содержит 'address'
        "span[class*='address'], "                       # Span с 'address'
        "div[class*='address'], "                        # Div с 'address'
        "span[data-qa='card-address'], "                 # Основной data-qa
        "div[class*='_address'], "                       # Содержит '_address'
        "span[class*='_address']"                        # Span содержит '_address'
    ),
    
    "detail_category": (
        "nav[aria-label='Breadcrumb'] span:last-child, " # Хлебные крошки (приоритет)
        "nav[class*='breadcrumb'] span:last-child, "     # Альтернативные хлебные крошки
        "span[data-qa*='category'], "                    # data-qa содержит 'category'
        "span[class*='category'], "                      # Span с 'category'
        "div[class*='category'], "                       # Div с 'category'
        "span[data-qa='card-category'], "                # Основной data-qa
        "div[class*='_category'], "                      # Содержит '_category'
        "span[class*='_category'], "                     # Span содержит '_category'
        "span[class*='breadcrumb']:last-child"           # Последний элемент breadcrumb
    ),
    
    "detail_rating": (
        "[itemprop='ratingValue'], "                     # Schema.org микроразметка
        "span[class*='rating'], "                        # Span с 'rating'
        "div[class*='rating'], "                         # Div с 'rating'
        "span[data-qa*='rating'], "                      # data-qa содержит 'rating'
        "div[class*='_rating'], "                        # Содержит '_rating'
        "span[class*='_rating'], "                       # Span содержит '_rating'
        "meta[itemprop='ratingValue']"                   # Meta тег с рейтингом
    ),
    
    "detail_reviews": (
        "span[class*='reviews'], "                       # Span с 'reviews'
        "span[class*='review'], "                        # Span с 'review'
        "div[class*='review'], "                         # Div с 'review'
        "span[data-qa*='review'], "                      # data-qa содержит 'review'
        "div[class*='_review'], "                        # Содержит '_review'
        "span[class*='_review'], "                       # Span содержит '_review'
        "a[href*='reviews'], "                           # Ссылка на отзывы
        "[itemprop='reviewCount']"                       # Schema.org микроразметка
    ),
}

# ============================================================================
# ПАРСИНГ КОНФИГУРАЦИЯ
# ============================================================================
PARSING_CONFIG = {
    "max_companies": 0,  # Максимальное количество компаний для сбора (0 = без ограничений)
    "max_pages": 0,  # Максимальное количество страниц (0 = без ограничений)
    "retry_attempts": 3,  # Количество попыток при ошибке
    "open_detail_page": True,  # Открывать ли детальную страницу компании
    "adaptive_pagination": True,  # Использовать адаптивную пагинацию (по кнопке "Далее")
}

# ============================================================================
# ВЫВОД ДАННЫХ
# ============================================================================
OUTPUT_CONFIG = {
    "output_dir": "output_2gis",
    "csv_filename": "companies_2gis.csv",
    "log_filename": "parser_2gis.log",
}

# ============================================================================
# ПОЛЯ ДАННЫХ
# ============================================================================
FIELDS = [
    "name",
    "address",
    "category",
    "rating",
    "reviews_count",
    "phones",
    "website",
    "hours",
    "url",
    "parsed_date",
]
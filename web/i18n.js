"use strict";
// UI-only translation. User titles, descriptions and group names are never translated.
window.I18n = (() => {
  const rows = [
    ['Вход подтверждён. Сессия готова для публикаций.', 'Login confirmed. The session is ready to publish.', 'Logowanie potwierdzone. Sesja jest gotowa do publikowania.'],
    ['Профиль браузера ещё не создан.', 'The browser profile has not been created yet.', 'Profil przeglądarki nie został jeszcze utworzony.'],
    ['Сессия Facebook отсутствует или истекла. Выполни вход заново.', 'Your Facebook session is missing or expired. Please sign in again.', 'Brak sesji Facebooka lub sesja wygasła. Zaloguj się ponownie.'],
    ['Facebook просит подтвердить вход. Открой окно входа и пройди проверку.', 'Facebook needs you to confirm your login. Open the login window and complete verification.', 'Facebook wymaga potwierdzenia logowania. Otwórz okno logowania i przejdź weryfikację.'],
    ['Не удалось проверить вход:', 'Could not verify login:', 'Nie udało się sprawdzić logowania:'],
    ['Аватар', 'Avatar', 'Awatar'],
    ['Интервал после завершения объявления, минут', 'Interval after completing a listing, minutes', 'Odstęp po zakończeniu ogłoszenia, minuty'],
    ['Объявления отправляются в порядке выбора. Один пост — исходная группа и до 9 дополнительных. Большой список разделится на пакеты Facebook.', 'Listings are sent in selection order. One post includes the original group and up to 9 additional groups. A larger list is split into Facebook batches.', 'Ogłoszenia są wysyłane w kolejności wyboru. Jeden post obejmuje grupę główną i do 9 dodatkowych. Większa lista jest dzielona na pakiety Facebooka.'],
    ['выбери объявления', 'select listings', 'wybierz ogłoszenia'],
    ['Добавить заголовок', 'Add heading', 'Dodaj nagłówek'],
    ['Удалить заголовок', 'Remove heading', 'Usuń nagłówek'],
    ['Введи свой заголовок', 'Enter your heading', 'Wpisz własny nagłówek'],
    ['Заголовок необязателен. Если поле пустое, публикация будет без заголовка. Цена — отдельной строкой внизу.', 'The heading is optional. An empty field means no heading. The price appears on a separate line at the bottom.', 'Nagłówek jest opcjonalny. Puste pole oznacza brak nagłówka. Cena jest w osobnym wierszu na dole.'],
    ['Зажми левую кнопку мыши на галочке группы и веди вниз или вверх. Повторное протягивание снимает отметки. У края экрана список прокручивается.', 'Hold the left mouse button on a group checkbox and drag down or up. Drag from a checked box to uncheck groups. The list scrolls near the screen edge.', 'Przytrzymaj lewy przycisk myszy na polu grupy i przeciągnij w dół lub w górę. Przeciągnij od zaznaczonego pola, aby odznaczyć grupy. Lista przewija się przy krawędzi ekranu.'],
    ['Создай объявление с описанием. Цена и фотографии необязательны.', 'Create a listing with a description. Price and photos are optional.', 'Utwórz ogłoszenie z opisem. Cena i zdjęcia są opcjonalne.'],
    ['Каталог пуст. Нажми «Добавить объявление».', 'The catalog is empty. Click Add listing.', 'Katalog jest pusty. Kliknij Dodaj ogłoszenie.'],
    ["Удалить отмеченные группы только у текущего аккаунта? На других аккаунтах они останутся.", "Remove marked groups only for the current account? They will remain on other accounts.", "Usunąć zaznaczone grupy tylko z bieżącego konta? Pozostaną na innych kontach."],
    ["Группы удалены только у текущего аккаунта. Другие аккаунты не затронуты.", "Groups removed only for the current account. Other accounts are unchanged.", "Grupy usunięte tylko z bieżącego konta. Inne konta pozostały bez zmian."],
    ["Готово, проверить вход", "Done, verify login", "Gotowe, sprawdź logowanie"],
    ["Смени аккаунт в открытом окне Chrome. Когда закончишь, нажми кнопку ниже. Окно не закроется до подтверждения; время ожидания — 10 минут.", "Switch accounts in the open Chrome window. When finished, press the button below. The window stays open until confirmation; the timeout is 10 minutes.", "Zmień konto w otwartym oknie Chrome. Po zakończeniu naciśnij przycisk poniżej. Okno pozostanie otwarte do potwierdzenia; limit czasu wynosi 10 minut."],
    ["Окно входа открыто. Смени аккаунт в Chrome, затем нажми «Готово, проверить вход» на сайте бота.", "Login window open. Switch accounts in Chrome, then press Done, verify login on the bot website.", "Okno logowania otwarte. Zmień konto w Chrome, a następnie naciśnij Gotowe, sprawdź logowanie na stronie bota."],
    ["Проверяю вход в выбранный аккаунт…", "Verifying the selected account…", "Sprawdzanie wybranego konta…"],
    ["Товары", "Products", "Produkty"], ["Твои товары", "Your products", "Twoje produkty"],
    ["Группы Facebook", "Facebook groups", "Grupy Facebook"], ["Группы", "Groups", "Grupy"],
    ["Новая публикация", "New publication", "Nowa publikacja"], ["Публикации", "Publications", "Publikacje"],
    ["Публикация", "Publish", "Publikacja"], ["История", "History", "Historia"], ["Профиль", "Profile", "Profil"],
    ["Подключение…", "Connecting…", "Łączenie…"], ["Пауза", "Pause", "Pauza"], ["Запустить", "Start", "Uruchom"],
    ["На паузе", "Paused", "Wstrzymano"], ["Работает", "Running", "Działa"],
    ["останавливаю черновик…", "stopping the draft…", "zatrzymywanie szkicu…"], ["публикуется", "publishing", "publikowanie"],
    ["Первое фото — главное. Проданные товары остаются здесь, но не участвуют в публикациях.", "The first photo is the cover. Sold products stay here but cannot be published.", "Pierwsze zdjęcie jest główne. Sprzedane produkty pozostają tutaj, ale nie można ich publikować."],
    ["Импорт ZIP", "Import ZIP", "Import ZIP"], ["Импортировать", "Import", "Importuj"],
    ["Каталог пуст. Импортируй свой ZIP или объявления Marketplace.", "Your catalog is empty. Import a ZIP or your Marketplace listings.", "Katalog jest pusty. Importuj ZIP lub swoje ogłoszenia Marketplace."],
    ["Добавь фотографии", "Add photos", "Dodaj zdjęcia"], ["SOLD · Продан", "SOLD", "SPRZEDANO"],
    ["Отменить SOLD", "Undo SOLD", "Cofnij SPRZEDANO"], ["Отметить SOLD", "Mark SOLD", "Oznacz SPRZEDANO"],
    ["Редактировать", "Edit", "Edytuj"], ["Закрыть", "Close", "Zamknij"],
    ["Цена, zł", "Price, zł", "Cena, zł"], ["Описание", "Description", "Opis"], ["Сохранить", "Save", "Zapisz"],
    ["Фотографии", "Photos", "Zdjęcia"], ["Главное фото", "Cover photo", "Zdjęcie główne"],
    ["Перетаскивай фото. Первое — главное. На телефоне можно использовать стрелки.", "Drag photos to reorder. The first is the cover. On your phone, use the arrows.", "Przeciągnij zdjęcia, aby zmienić kolejność. Pierwsze jest główne. Na telefonie użyj strzałek."],
    ["Добавить выбранные фото", "Add selected photos", "Dodaj wybrane zdjęcia"],
    ["Добавить фотографии", "Add photos", "Dodaj zdjęcia"], ["Переместить влево", "Move left", "Przesuń w lewo"], ["Переместить вправо", "Move right", "Przesuń w prawo"],
    ["Проверка показывает доступ, очередь модерации и явные отказы. Ожидание одобрения не означает отказ.", "Checks show access, pending moderation and explicit rejections. Pending approval is not a rejection.", "Sprawdzanie pokazuje dostęp, moderację i wyraźne odrzucenia. Oczekiwanie na zatwierdzenie nie oznacza odrzucenia."],
    ["Загрузить мои группы", "Load my groups", "Wczytaj moje grupy"], ["Проверить отмеченные", "Check marked", "Sprawdź zaznaczone"],
    ["Вступить в открытые и проверить", "Join public groups and check", "Dołącz do publicznych i sprawdź"],
    ["Найти группу в списке", "Find a group in the list", "Znajdź grupę na liście"], ["Отметить видимые", "Mark visible", "Zaznacz widoczne"],
    ["Выбрать подходящие для публикации", "Select eligible groups", "Wybierz odpowiednie grupy"],
    ["Удалить отмеченные", "Remove marked", "Usuń zaznaczone"], ["Найти новые группы или добавить ссылку", "Find new groups or add a link", "Znajdź nowe grupy lub dodaj link"],
    ["Вернуть удалённые", "Restore removed", "Przywróć usunięte"],
    ["Аккаунты", "Accounts", "Konta"], ["Услуги", "Services", "Usługi"], ["Контент", "Content", "Treści"],
    ["Контент / ИИ", "Content / AI", "Treści / AI"], ["Твои услуги", "Your services", "Twoje usługi"], ["Твой контент", "Your content", "Twoje treści"],
    ["Название", "Name", "Nazwa"], ["Новое объявление", "New listing", "Nowe ogłoszenie"],
    ["Добавить объявление", "Add listing", "Dodaj ogłoszenie"], ["Удалить из каталога", "Remove from catalog", "Usuń z katalogu"],
    ["Удалить объявление из каталога? Ожидающие отправки будут отменены. Уже опубликованные посты в Facebook останутся.", "Remove this listing from the catalog? Waiting jobs will be cancelled. Existing Facebook posts will remain.", "Usunąć ogłoszenie z katalogu? Oczekujące wysyłki zostaną anulowane. Istniejące posty na Facebooku pozostaną."],
    ["Сохранённые аккаунты", "Saved accounts", "Zapisane konta"],
    ["Каждый аккаунт имеет отдельный браузер, товары, группы, очередь и паузу. Несколько аккаунтов могут работать одновременно.", "Each account has its own browser, products, groups, queue and pause. Several accounts can run simultaneously.", "Każde konto ma oddzielną przeglądarkę, produkty, grupy, kolejkę i pauzę. Kilka kont może działać jednocześnie."],
    ["Вход не выполнен", "Not logged in", "Nie zalogowano"], ["Выбрать аккаунт", "Select account", "Wybierz konto"],
    ["Запустить аккаунт", "Start account", "Uruchom konto"], ["Пауза аккаунта", "Pause account", "Wstrzymaj konto"],
    ["Настройки выбранного аккаунта", "Selected account settings", "Ustawienia wybranego konta"],
    ["Название профиля", "Profile label", "Nazwa profilu"], ["Режим интерфейса", "Interface mode", "Tryb interfejsu"],
    ["Сохранить профиль", "Save profile", "Zapisz profil"], ["Добавить аккаунт", "Add account", "Dodaj konto"],
    ["Например: магазин или услуги", "For example: store or services", "Na przykład: sklep lub usługi"],
    ["Создать отдельный профиль", "Create separate profile", "Utwórz oddzielny profil"],
    ["Войти в выбранный аккаунт", "Log in to selected account", "Zaloguj się na wybrane konto"],
    ["После создания нажми «Войти в выбранный аккаунт». Пароль вводится только на Facebook и не хранится в приложении.", "After creating it, click Log in to selected account. Your password is entered only on Facebook and is not stored in this app.", "Po utworzeniu kliknij Zaloguj się na wybrane konto. Hasło wpisujesz tylko na Facebooku i nie jest przechowywane w aplikacji."],
    ["Для другого Facebook создай отдельный профиль. Не выходи из старого аккаунта ради переключения: выбери сохранённый профиль выше.", "Create a separate profile for another Facebook account. To switch, select a saved profile above instead of logging out.", "Dla innego konta Facebook utwórz oddzielny profil. Aby przełączyć konto, wybierz zapisany profil powyżej zamiast się wylogowywać."],
    ["Войди в Facebook в открытом окне выбранного профиля, затем нажми «Готово, проверить вход».", "Log in to Facebook in the selected profile window, then click Done, verify login.", "Zaloguj się na Facebooku w oknie wybranego profilu, a następnie kliknij Gotowe, sprawdź logowanie."],
    ["Как пользоваться", "How to use", "Jak korzystać"],
    ["Создай объявление или импортируй товар. Фото добавляются после первого сохранения. Заголовок и предпросмотр находятся в редакторе.", "Create a listing or import a product. Add photos after saving it for the first time. The heading and preview are in the editor.", "Utwórz ogłoszenie lub zaimportuj produkt. Zdjęcia dodasz po pierwszym zapisaniu. Nagłówek i podgląd są w edytorze."],
    ["Загрузи свои группы, отметь нужные и проверь доступ. Вступление и одобрение администратора — разные статусы. Проверку можно отменить.", "Load your groups, mark the ones you need and check access. Joining and admin approval are different statuses. You can cancel a check.", "Wczytaj grupy, zaznacz wybrane i sprawdź dostęp. Dołączenie i zgoda administratora to różne statusy. Sprawdzanie można anulować."],
    ["Выбери объявления и проверенные группы, создай план и нажми «Запустить». Время начала и интервал задаются отдельно от Турбо.", "Choose listings and checked groups, create a plan and click Start. The start time and interval are separate from Turbo.", "Wybierz ogłoszenia i sprawdzone grupy, utwórz plan i kliknij Uruchom. Czas rozpoczęcia i odstęp są niezależne od Turbo."],
    ["Здесь результаты по группам. Очистка истории не удаляет публикации в Facebook.", "Group results are shown here. Clearing history does not delete Facebook posts.", "Tutaj są wyniki dla grup. Wyczyszczenie historii nie usuwa postów na Facebooku."],
    ["Добавляй и выбирай аккаунты. Переключение панели не останавливает остальные аккаунты; у каждого своя кнопка паузы.", "Add and select accounts. Switching the panel does not stop other accounts; each has its own pause button.", "Dodawaj i wybieraj konta. Przełączenie panelu nie zatrzymuje pozostałych kont; każde ma własny przycisk pauzy."],
    ["Аккаунты: добавь профиль и войди в Facebook.", "Accounts: add a profile and log in to Facebook.", "Konta: dodaj profil i zaloguj się na Facebooku."],
    ["Каталог: добавь описание и при необходимости фотографии.", "Catalog: add a description and photos if needed.", "Katalog: dodaj opis i zdjęcia w razie potrzeby."],
    ["Группы: загрузи и проверь места публикации.", "Groups: load and check publishing destinations.", "Grupy: wczytaj i sprawdź miejsca publikacji."],
    ["Публикация: создай план, затем запусти нужный аккаунт.", "Publishing: create a plan, then start the intended account.", "Publikacja: utwórz plan, a następnie uruchom odpowiednie konto."],
    ["Турбо сокращает паузы, но не защищает от ограничений Facebook. При ограничении действия аккаунта останавливаются.", "Turbo reduces pauses but does not protect against Facebook limits. Account actions stop when restricted.", "Turbo skraca przerwy, ale nie chroni przed ograniczeniami Facebooka. Działania konta zatrzymują się po nałożeniu ograniczenia."],
    ["Пропущена: ошибка", "Skipped: error", "Pominięto: błąd"],
    ["Нужны ответы", "Answers needed", "Wymagane odpowiedzi"],
    ["Вступление не подтверждено", "Join unconfirmed", "Dołączenie niepotwierdzone"],
    ["Турбо: вкл.", "Turbo: on", "Turbo: wł."], ["Турбо: выкл.", "Turbo: off", "Turbo: wył."],
    ["Убирает искусственные паузы. Расписание, загрузка фото и ограничения Facebook сохраняются.", "Removes artificial pauses. Schedules, photo loading and Facebook limits are preserved.", "Usuwa sztuczne przerwy. Harmonogram, ładowanie zdjęć i ograniczenia Facebooka pozostają."],
    ["Турбо включён. Расписание и проверки безопасности сохранены.", "Turbo enabled. Schedules and safety checks are preserved.", "Turbo włączone. Harmonogram i kontrole bezpieczeństwa pozostają."],
    ["Турбо выключен.", "Turbo disabled.", "Turbo wyłączone."],
    ["Турбо: без искусственной паузы перед отправкой. Пауза вверху останавливает подготовку текущего объявления.", "Turbo: no artificial pause before submitting. Pause stops preparation of the current post.", "Turbo: bez sztucznej przerwy przed wysłaniem. Pauza zatrzymuje przygotowanie bieżącego ogłoszenia."],
    ["Заголовок объявления", "Post heading", "Nagłówek ogłoszenia"],
    ["Отменить проверку", "Cancel check", "Anuluj sprawdzanie"],
    ["Массовое изменение описаний", "Bulk description editing", "Masowa edycja opisów"],
    ["Только товары текущего аккаунта, включая проданные. Публикации в Facebook не изменятся.", "Current account products only, including sold items. Facebook posts will not change.", "Tylko produkty bieżącego konta, w tym sprzedane. Posty na Facebooku nie zmienią się."],
    ["Убрать рекламу профиля", "Remove profile promotion", "Usuń reklamę profilu"],
    ["Или найди и замени свой текст во всех описаниях. Пустая замена удаляет найденный текст.", "Or find and replace text in all descriptions. An empty replacement removes the matching text.", "Lub znajdź i zamień tekst we wszystkich opisach. Pusta zamiana usuwa znaleziony tekst."],
    ["Найти текст", "Find text", "Znajdź tekst"], ["Заменить на", "Replace with", "Zamień na"],
    ["Показать замены", "Preview replacements", "Podgląd zmian"],
    ["Отменить последнее массовое изменение", "Undo last bulk edit", "Cofnij ostatnią edycję zbiorczą"],
    ["Будет изменено товаров:", "Products to update:", "Produkty do zmiany:"],
    ["До", "Before", "Przed"], ["После", "After", "Po"],
    ["Применить изменения", "Apply changes", "Zastosuj zmiany"],
    ["Применить изменения ко всем товарам из предпросмотра?", "Apply changes to all products in the preview?", "Zastosować zmiany do wszystkich produktów z podglądu?"],
    ["Отменить последнее массовое изменение описаний?", "Undo the last bulk description edit?", "Cofnąć ostatnią masową edycję opisów?"],
    ["Описания обновлены:", "Descriptions updated:", "Zaktualizowano opisy:"],
    ["Описания восстановлены:", "Descriptions restored:", "Przywrócono opisy:"],
    ["Останавливаю проверку групп…", "Stopping group check…", "Zatrzymywanie sprawdzania grup…"],
    ["Проверка отменена. Проверено групп:", "Check cancelled. Groups checked:", "Sprawdzanie anulowane. Sprawdzone grupy:"],
    ["Предпросмотр объявления", "Post preview", "Podgląd posta"],
    ["Предпросмотр готов. Жирный заголовок применяется, если редактор Facebook его поддерживает.", "Preview ready. A bold heading is applied if the Facebook editor supports it.", "Podgląd gotowy. Pogrubiony nagłówek jest stosowany, jeśli edytor Facebooka go obsługuje."],
    ["Восстановление доступно 5 минут после удаления", "Restore is available for 5 minutes after removal", "Przywracanie jest dostępne przez 5 minut po usunięciu"],
    ["Вернуть группы, удалённые за последние 5 минут? Отменённые публикации не возобновятся.", "Restore groups removed in the last 5 minutes? Cancelled publications will not resume.", "Przywrócić grupy usunięte w ostatnich 5 minutach? Anulowane publikacje nie zostaną wznowione."],
    ["Вернуть все удалённые группы текущего аккаунта? Отменённые публикации не возобновятся.", "Restore all removed groups for the current account? Cancelled publications will not resume.", "Przywrócić wszystkie usunięte grupy bieżącego konta? Anulowane publikacje nie zostaną wznowione."],
    ["Восстановлено групп:", "Groups restored:", "Przywrócono grup:"],
    ["Отменённые публикации не возобновляются.", "Cancelled publications are not resumed.", "Anulowane publikacje nie zostaną wznowione."],
    ["Город и тема", "City and topic", "Miasto i temat"], ["Найти в Facebook", "Search Facebook", "Szukaj na Facebooku"],
    ["Добавить вручную", "Add manually", "Dodaj ręcznie"], ["Название", "Title", "Nazwa"], ["Добавить", "Add", "Dodaj"], ["Групп пока нет.", "No groups yet.", "Brak grup."],
    ["Можно", "Ready", "Gotowe"], ["Только «Продать»", "Sell only", "Tylko sprzedaż"], ["Не проверена", "Unchecked", "Niesprawdzona"],
    ["Нет поля", "No composer", "Brak edytora"], ["Закрытая группа", "Private group", "Grupa prywatna"],
    ["Вступление", "Join pending", "Wniosek"], ["На проверке", "Pending", "Oczekuje"], ["Лимит", "Limit", "Limit"],
    ["Не участник", "Not a member", "Poza grupą"], ["Исключили", "Removed", "Usunięto"], ["Блок", "Blocked", "Blokada"],
    ["Отклонено", "Rejected", "Odrzucono"], ["моя группа", "joined", "moja grupa"],
    ["Ожидают решения администратора. Это не отказ.", "Awaiting an admin decision. Not rejected.", "Oczekują na decyzję administratora. To nie odrzucenie."],
    ["Facebook явно показал отклонённые публикации", "Facebook explicitly reported rejected posts", "Facebook wyraźnie wskazał odrzucone posty"],
    ["Товары отправляются в порядке выбора. Один пост — исходная группа и до 9 дополнительных. Большой список разделится на пакеты Facebook.", "Products are published in selection order. One post targets a primary group and up to 9 additional groups. Larger lists are split into batches.", "Produkty są publikowane w kolejności wyboru. Jeden post trafia do grupy głównej i maksymalnie 9 dodatkowych. Większe listy są dzielone na pakiety."],
    ["1. Товары", "1. Products", "1. Produkty"], ["2. Проверенные группы", "2. Checked groups", "2. Sprawdzone grupy"], ["3. Расписание", "3. Schedule", "3. Harmonogram"],
    ["Порядок:", "Order:", "Kolejność:"], ["выбери товары", "select products", "wybierz produkty"],
    ["Перейди в «Группы»: загрузи и проверь свои группы.", "Go to Groups: load and check your groups.", "Przejdź do Grup: wczytaj i sprawdź swoje grupy."],
    ["Интервал после завершения товара, минут", "Interval after a product finishes, minutes", "Odstęp po zakończeniu produktu, minuty"],
    ["Начать в указанное время (Польша), или оставь пустым для отправки сейчас", "Start at this time (Poland), or leave empty to start now", "Rozpocznij o tej godzinie (Polska) lub pozostaw puste, aby zacząć teraz"],
    ["Перед отправкой бот ждёт 5 секунд. Пауза вверху останавливает подготовку текущего объявления.", "The bot waits 5 seconds before submitting. Pause stops preparation of the current post.", "Bot czeka 5 sekund przed wysłaniem. Pauza zatrzymuje przygotowanie bieżącego ogłoszenia."],
    ["Добавить план", "Add plan", "Dodaj plan"], ["Очистить историю", "Clear history", "Wyczyść historię"],
    ["Последние 30 товаров. Открой запись, чтобы увидеть результаты по группам.", "Last 30 products. Open a record to see group results.", "Ostatnie 30 produktów. Otwórz wpis, aby zobaczyć wyniki w grupach."],
    ["Отменить оставшиеся отправки плана", "Cancel remaining plan jobs", "Anuluj pozostałe wysyłki planu"], ["Публикаций пока нет.", "No publications yet.", "Brak publikacji."],
    ["В процессе", "In progress", "W toku"], ["подтверждено", "confirmed", "potwierdzono"], ["без подтверждения:", "unconfirmed:", "bez potwierdzenia:"],
    ["Ожидает", "Pending", "Oczekuje"], ["Публикуется", "Publishing", "Publikowanie"], ["Отправлено", "Sent", "Wysłano"],
    ["Опубликовано", "Published", "Opublikowano"], ["На проверке у администратора", "Awaiting admin approval", "Oczekuje na zatwierdzenie"],
    ["Результат не подтверждён", "Unconfirmed result", "Niepotwierdzony wynik"], ["Не отправлено", "Not sent", "Nie wysłano"],
    ["Пропущено", "Skipped", "Pominięto"], ["Отменено", "Cancelled", "Anulowano"],
    ["Твой Facebook-аккаунт", "Your Facebook account", "Twoje konto Facebook"], ["Войди в Facebook на компьютере", "Log in to Facebook on your computer", "Zaloguj się do Facebooka na komputerze"],
    ["Объявлений сегодня", "Posts today", "Posty dzisiaj"], ["Выбрано групп", "Selected groups", "Wybrane grupy"], ["Ближайшая отправка", "Next scheduled post", "Następna wysyłka"],
    ["Войти / сменить аккаунт", "Log in / switch account", "Zaloguj / zmień konto"], ["Обновить профиль", "Refresh profile", "Odśwież profil"],
    ["Вход открывается на компьютере. Для смены аккаунта выйди из Facebook в этом окне и войди в другой аккаунт.", "Login opens on your computer. To switch accounts, log out of Facebook in that window and log in with another account.", "Logowanie otwiera się na komputerze. Aby zmienić konto, wyloguj się z Facebooka w tym oknie i zaloguj na inne konto."],
    ["Товаров продано", "Products sold", "Sprzedane produkty"], ["Сумма продаж · по цене при отметке SOLD", "Sales total · price when marked SOLD", "Suma sprzedaży · cena przy oznaczeniu SPRZEDANO"],
    ["Импорт из Marketplace", "Import from Marketplace", "Import z Marketplace"], ["Найти мои объявления", "Find my listings", "Znajdź moje ogłoszenia"],
    ["Ссылка на своё объявление Marketplace", "Link to your Marketplace listing", "Link do swojego ogłoszenia Marketplace"],
    ["Предпросмотр", "Preview", "Podgląd"], ["Объявления Marketplace", "Marketplace listings", "Ogłoszenia Marketplace"],
    ["Ничего на Facebook не изменяется. Сначала предпросмотр, затем подтверждение импорта.", "Nothing on Facebook is changed. Preview first, then confirm the import.", "Nic na Facebooku nie zostanie zmienione. Najpierw podgląd, potem potwierdzenie importu."],
    ["Порядок фото из Facebook. Первое будет главным.", "Photo order from Facebook. The first will be the cover.", "Kolejność zdjęć z Facebooka. Pierwsze będzie główne."],
    ["Добавить в товары", "Add to products", "Dodaj do produktów"], ["Обновить существующий товар", "Update existing product", "Aktualizuj istniejący produkt"],
    ["Facebook отдал небольшие фотографии. Проверь качество перед импортом.", "Facebook returned small images. Check quality before importing.", "Facebook zwrócił małe zdjęcia. Sprawdź jakość przed importem."],
    ["Товар уже есть в каталоге. Обновление заменит описание, цену и порядок фото. SOLD сохранится.", "This product already exists. Updating replaces its description, price and photo order. SOLD stays unchanged.", "Produkt już istnieje. Aktualizacja zastąpi opis, cenę i kolejność zdjęć. Status SPRZEDANO pozostanie bez zmian."],
    ["Подтвердить замену описания, цены и всех фотографий этого товара?", "Replace this product's description, price and all photos?", "Zastąpić opis, cenę i wszystkie zdjęcia tego produktu?"],
    ["Удалённый товар не восстанавливается импортом.", "Import cannot restore a deleted product.", "Import nie przywraca usuniętego produktu."],
    ["Продажа сохранена. Товар исключён из публикаций.", "Sale saved. Product excluded from publications.", "Sprzedaż zapisana. Produkt wykluczony z publikacji."],
    ["Продажа отменена. Товар снова доступен для публикации; старые отправки не возобновляются.", "Sale undone. Product can be published again; old jobs are not resumed.", "Sprzedaż cofnięta. Produkt można ponownie publikować; stare zadania nie zostaną wznowione."],
    ["Очистить историю завершённых публикаций? Текущие задания и статистика сохранятся.", "Clear completed publication history? Active jobs and statistics are preserved.", "Wyczyścić historię zakończonych publikacji? Bieżące zadania i statystyki pozostaną."],
    ["История очищена. Текущие задания сохранены.", "History cleared. Active jobs preserved.", "Historia wyczyszczona. Bieżące zadania zachowane."],
    ["Отметь группы для проверки", "Mark groups to check", "Zaznacz grupy do sprawdzenia"], ["Выбери ZIP", "Choose a ZIP", "Wybierz ZIP"],
    ["Каталог импортирован", "Catalog imported", "Katalog zaimportowany"], ["Товар сохранён", "Product saved", "Produkt zapisany"],
    ["Выбери фотографии", "Choose photos", "Wybierz zdjęcia"], ["Фотографии добавлены", "Photos added", "Zdjęcia dodane"],
    ["Ошибка подключения", "Connection error", "Błąd połączenia"], ["Разделы", "Sections", "Sekcje"],
    ["Открой ссылку подключения из окна запуска бота", "Open the pairing link from the bot startup window", "Otwórz link połączenia z okna uruchomienia bota"],
    ["Читаю Marketplace. Объявления на Facebook не изменяются…", "Reading Marketplace. Facebook listings are not changed…", "Odczyt Marketplace. Ogłoszenia na Facebooku nie są zmieniane…"],
    ["Предпросмотр готов. Проверь описание, цену и порядок фото перед импортом.", "Preview ready. Check description, price and photo order before importing.", "Podgląd gotowy. Sprawdź opis, cenę i kolejność zdjęć przed importem."],
    ["Товар импортирован из Facebook Marketplace", "Product imported from Facebook Marketplace", "Produkt zaimportowany z Facebook Marketplace"],
    ["Подходящие группы выбраны. Перейди в «Публикация».", "Eligible groups selected. Go to Publish.", "Odpowiednie grupy wybrane. Przejdź do Publikacji."],
    ["Поиск показывает активные объявления.", "Search shows active listings.", "Wyszukiwanie pokazuje aktywne ogłoszenia."],
    ["Marketplace: найдено активных объявлений", "Marketplace: active listings found", "Marketplace: znaleziono aktywnych ogłoszeń"],
    ["Выбери товары, группы и интервал от 0 до 10080 минут", "Choose products, groups and an interval from 0 to 10080 minutes", "Wybierz produkty, grupy i odstęp od 0 do 10080 minut"],
    ["Проданный или удалённый товар нельзя публиковать", "Sold or deleted products cannot be published", "Nie można publikować sprzedanych lub usuniętych produktów"],
    ["Для каждого товара нужны описание и хотя бы одно фото", "Each product needs a description and at least one photo", "Każdy produkt wymaga opisu i co najmniej jednego zdjęcia"],
    ["Эти товары уже ожидают отправки в выбранные группы", "These products are already queued for the selected groups", "Te produkty już oczekują na wysłanie do wybranych grup"],
  ];
  const locales = {ru:'ru-RU', en:'en-GB', pl:'pl-PL', 'zh-CN':'zh-CN', hi:'hi-IN', es:'es-ES', ar:'ar', fr:'fr-FR', bn:'bn-BD', pt:'pt-PT', ja:'ja-JP'};
  const catalogs = {en:Object.fromEntries(rows.map(row => [row[0],row[1]])), pl:Object.fromEntries(rows.map(row => [row[0],row[2]])), ...(window.MARKET_LOCALES || {})};
  const templates = {
    en:{'Язык':'Language','Фото {n}':'Photo {n}','{n} фото':'{n} photos','{n} из {total}':'{n} of {total}','Проверено:':'Checked:'},
    pl:{'Язык':'Język','Фото {n}':'Zdjęcie {n}','{n} фото':'{n} zdjęć','{n} из {total}':'{n} z {total}','Проверено:':'Sprawdzono:'}
  };
  for (const [code, values] of Object.entries(templates)) Object.assign(catalogs[code], values);
  const patterns = new Map();
  const escapePattern = key => key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  function replacements(code) {
    if (!patterns.has(code)) patterns.set(code, new RegExp(Object.keys(catalogs[code]).sort((a,b) => b.length-a.length).map(escapePattern).join('|'), 'g'));
    return patterns.get(code);
  }
  let language = "ru";
  try { language = localStorage.getItem("market-language") || "ru"; } catch (_) {}
  if (!Object.hasOwn(locales, language)) language = "ru";
  const originals = new WeakMap();
  const attributes = new WeakMap();
  function t(value) {
    let text = String(value ?? "");
    if (language === "ru") return text;
    const code = catalogs[language] ? language : 'en', catalog = catalogs[code];
    const normalized = text.replace(/\s+/g, ' ').trim();
    if (Object.hasOwn(catalog, normalized)) return catalog[normalized];
    const format = (key, values) => (catalog[key] || templates.en[key]).replace(/\{(\w+)\}/g, (_, name) => values[name]);
    text = text.replace(/(\d+)\s+из\s+(\d+)/g, (_, n, total) => format('{n} из {total}', {n,total}));
    text = text.replace(/Фото (\d+)/g, (_, n) => format('Фото {n}', {n}));
    text = text.replace(/(\d+) фото/g, (_, n) => format('{n} фото', {n}));
    // One pass prevents translated phrases from being translated a second time.
    return text.replace(replacements(code), key => catalog[key]);
  }
  function apply(root = document.body) {
    document.documentElement.lang = language;
    document.documentElement.dir = language === 'ar' ? 'rtl' : 'ltr';
    for (const element of root.querySelectorAll('[data-user-content],input:not([type]),input[type="text"],textarea')) element.dir = 'auto';
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.parentElement || node.parentElement.closest('[data-user-content],script,style,textarea,option')) continue;
      const previous = originals.get(node);
      const source = previous && node.nodeValue === previous.translated ? previous.source : node.nodeValue;
      const translated = t(source);
      originals.set(node, {source, translated});
      node.nodeValue = translated;
    }
    for (const element of root.querySelectorAll('[placeholder],[aria-label],[title],[alt]')) {
      if (element.closest('[data-user-content]')) continue;
      for (const attribute of ['placeholder','aria-label','title','alt']) {
        if (!element.hasAttribute(attribute)) continue;
        const saved = attributes.get(element) || {};
        const current = element.getAttribute(attribute);
        const previous = saved[attribute];
        const source = previous && current === previous.translated ? previous.source : current;
        const translated = t(source);
        saved[attribute] = {source, translated};
        attributes.set(element, saved);
        element.setAttribute(attribute, translated);
      }
    }
    const selector = document.getElementById('language');
    if (selector) selector.value = language;
  }
  return {t, apply, languages:Object.keys(locales), get language() { return language; }, get locale() { return locales[language]; },
    set(value) { if (!Object.hasOwn(locales,value)) return; language=value; try {localStorage.setItem('market-language',value);} catch (_) {} }};
})();

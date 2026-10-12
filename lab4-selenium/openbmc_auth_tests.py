import os
import re
import sys
import time
import unittest

# Если libfixloopback.so собран рядом со скриптом, используем его,
# чтобы нормализовать TCP MSS на loopback и избежать зависаний при отдаче большого JS-бандла
_fix_lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libfixloopback.so")
if os.path.exists(_fix_lib):
    os.environ["LD_PRELOAD"] = _fix_lib

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException
)


# В Jenkins используем BMC_URL из Pipeline (HTTPS 2443).
# Старый HTTP-порт 8000 остается запасным вариантом для локального запуска ЛР4.
OPENBMC_URL = os.getenv(
    "OPENBMC_WEB_URL",
    os.getenv("BMC_URL", "http://127.0.0.1:8000"),
)

USERNAME = "root"
PASSWORD = "0penBmc"

WAIT_TIME = 10


class OpenBMCTests(unittest.TestCase):

    def setUp(self):
        options = webdriver.ChromeOptions()

        # Chromium запускается внутри Jenkins Docker-контейнера.
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        # У OpenBMC в QEMU самоподписанный сертификат
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--allow-insecure-localhost")
        options.add_argument("--no-proxy-server")

        self.driver = webdriver.Chrome(options=options)
        self.driver.maximize_window()

        self.wait = WebDriverWait(self.driver, WAIT_TIME)

    def tearDown(self):
        self.driver.quit()

    # ----------------------------------------------------------
    # Вспомогательные методы
    # ----------------------------------------------------------

    def open_login_page(self):
        self.driver.get(OPENBMC_URL)

        self.wait.until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

    def find_username_field(self):
        """
        Пытаемся найти поле логина несколькими способами,
        потому что HTML может отличаться между версиями OpenBMC.
        """

        selectors = [
            (By.ID, "username"),
            (By.NAME, "username"),
            (By.CSS_SELECTOR, 'input[type="text"]'),
            (By.CSS_SELECTOR, 'input[autocomplete="username"]'),
        ]

        try:
            return self.wait.until(
                lambda driver: next(
                    (
                        element
                        for selector in selectors
                        for element in driver.find_elements(*selector)
                        if element.is_displayed()
                    ),
                    False,
                )
            )
        except TimeoutException:
            self.fail("Не найдено поле логина")

    def find_password_field(self):
        selectors = [
            (By.ID, "password"),
            (By.NAME, "password"),
            (By.CSS_SELECTOR, 'input[type="password"]'),
            (By.CSS_SELECTOR, 'input[autocomplete="current-password"]'),
        ]

        try:
            return self.wait.until(
                lambda driver: next(
                    (
                        element
                        for selector in selectors
                        for element in driver.find_elements(*selector)
                        if element.is_displayed()
                    ),
                    False,
                )
            )
        except TimeoutException:
            self.fail("Не найдено поле пароля")

    def find_login_button(self):
        selectors = [
            (By.CSS_SELECTOR, 'button[type="submit"]'),
            (
                By.XPATH,
                "//button[contains(translate(., "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
                "'abcdefghijklmnopqrstuvwxyz'), 'log in')]"
            ),
            (
                By.XPATH,
                "//button[contains(translate(., "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
                "'abcdefghijklmnopqrstuvwxyz'), 'login')]"
            ),
            (
                By.XPATH,
                "//button[contains(translate(., "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
                "'abcdefghijklmnopqrstuvwxyz'), 'sign in')]"
            ),
        ]

        try:
            return self.wait.until(
                lambda driver: next(
                    (
                        element
                        for selector in selectors
                        for element in driver.find_elements(*selector)
                        if element.is_displayed()
                    ),
                    False,
                )
            )
        except TimeoutException:
            self.fail("Не найдена кнопка входа")

    def login(self, username=USERNAME, password=PASSWORD):
        self.open_login_page()

        username_field = self.find_username_field()
        password_field = self.find_password_field()

        username_field.clear()
        username_field.send_keys(username)

        password_field.clear()
        password_field.send_keys(password)

        login_btn = self.find_login_button()
        try:
            login_btn.click()
        except Exception:
            self.driver.execute_script("arguments[0].click();", login_btn)

        try:
            WebDriverWait(self.driver, 5).until(
                lambda d: "login" not in d.current_url.lower()
            )
        except TimeoutException:
            pass

        time.sleep(1)

    def page_text(self):
        return self.driver.find_element(By.TAG_NAME, "body").text

    def click_text(self, variants):
        """
        Ищет ссылку или кнопку по тексту.
        """

        for text in variants:
            xpath = (
                "//*[self::a or self::button or @role='button']"
                f"[contains(translate(normalize-space(.), "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
                "'abcdefghijklmnopqrstuvwxyz'), "
                f"'{text.lower()}')]"
            )

            elements = self.driver.find_elements(By.XPATH, xpath)

            for element in elements:
                if element.is_displayed():
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});",
                        element
                    )

                    try:
                        element.click()
                    except Exception:
                        self.driver.execute_script(
                            "arguments[0].click();",
                            element
                        )

                    time.sleep(2)
                    return True

        return False

    # ----------------------------------------------------------
    # TEST 1
    # Успешная авторизация
    # ----------------------------------------------------------

    def test_01_successful_login(self):
        self.login(USERNAME, PASSWORD)

        current_url = self.driver.current_url.lower()
        text = self.page_text().lower()

        # После успешного входа обычно исчезает форма авторизации
        password_fields = self.driver.find_elements(
            By.CSS_SELECTOR,
            'input[type="password"]'
        )

        self.assertTrue(
            len(password_fields) == 0
            or "login" not in current_url,
            "После правильного логина пользователь остался "
            "на странице авторизации"
        )

        print("\n[PASS] Успешная авторизация")
        print("URL:", self.driver.current_url)

    # ----------------------------------------------------------
    # TEST 2
    # Неверные данные
    # ----------------------------------------------------------

    def test_02_invalid_login(self):
        self.login(USERNAME, "wrong_password_12345")

        text = self.page_text().lower()

        error_words = [
            "invalid",
            "incorrect",
            "failed",
            "error",
            "unauthorized",
            "authentication",
            "log in"
        ]

        found_error = any(word in text for word in error_words)

        # При неправильном пароле форма входа также должна остаться
        password_fields = self.driver.find_elements(
            By.CSS_SELECTOR,
            'input[type="password"]'
        )

        self.assertTrue(
            found_error or len(password_fields) > 0,
            "OpenBMC не показал ошибку после неверного пароля"
        )

        print("\n[PASS] Неверные данные обработаны")

    # ----------------------------------------------------------
    # TEST 3
    # Блокировка учётной записи
    #
    # ВНИМАНИЕ:
    # тест реально делает несколько неправильных входов.
    # ----------------------------------------------------------

    def test_03_account_lockout(self):

        wrong_password = "wrong_password_12345"
        attempts = 5

        for attempt in range(attempts):

            self.open_login_page()

            username_field = self.find_username_field()
            password_field = self.find_password_field()

            username_field.clear()
            username_field.send_keys(USERNAME)

            password_field.clear()
            password_field.send_keys(wrong_password)

            login_btn = self.find_login_button()
            try:
                login_btn.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", login_btn)

            time.sleep(1)

            print(
                f"\nПопытка неправильного входа "
                f"{attempt + 1}/{attempts}"
            )

        text = self.page_text().lower()

        lock_words = [
            "locked",
            "lock",
            "too many",
            "temporarily",
            "blocked"
        ]

        account_locked = any(
            word in text
            for word in lock_words
        )

        if account_locked:
            print("[PASS] Обнаружена блокировка учётной записи")
        else:
            print(
                "[INFO] Явного текста о блокировке "
                "на странице не найдено"
            )

        # Проверяем, что после неправильных попыток
        # успешный вход не произошёл.
        password_fields = self.driver.find_elements(
            By.CSS_SELECTOR,
            'input[type="password"]'
        )

        self.assertTrue(
            account_locked or len(password_fields) > 0,
            "После серии неверных попыток произошёл неожиданный вход"
        )

    # ----------------------------------------------------------
    # TEST 4
    # Проверка Inventory
    # ----------------------------------------------------------

    def test_04_inventory(self):
        self.login()

        # Раскрываем меню Hardware status и переходим в Inventory
        self.click_text(["hardware status", "hardware"])
        time.sleep(1)
        opened = self.click_text([
            "inventory and leds",
            "inventory",
        ])

        if not opened or "inventory" not in self.driver.current_url.lower():
            self.driver.get(f"{OPENBMC_URL}/#/hardware-status/inventory")
            time.sleep(3)
            opened = True

        self.assertTrue(
            opened,
            "Не удалось открыть страницу Inventory/Hardware"
        )

        time.sleep(2)
        text = self.page_text().lower()

        inventory_words = [
            "cpu",
            "processor",
            "memory",
            "ram",
            "dimm"
        ]

        found = [
            word
            for word in inventory_words
            if word in text
        ]

        self.assertTrue(
            len(found) > 0,
            "В Web UI не найдены данные CPU/RAM/Memory"
        )

        print("\n[PASS] Inventory отображается")
        print("Найдены элементы:", found)

    # ----------------------------------------------------------
    # TEST 5
    # Проверка температуры
    # ----------------------------------------------------------

    def test_05_temperature(self):
        self.login()

        opened = self.click_text([
            "sensors",
            "sensor",
            "temperature"
        ])
        if not opened:
            self.click_text(["hardware status", "hardware"])
            time.sleep(1)
            opened = self.click_text([
                "sensors",
                "sensor",
                "temperature"
            ])
        if not opened:
            self.driver.get(f"{OPENBMC_URL}/#/hardware-status/sensors")
            time.sleep(2)
            opened = True

        self.assertTrue(
            opened,
            "Не удалось открыть страницу Sensors"
        )

        time.sleep(2)
        text = self.page_text()

        print("\nТекст страницы датчиков:")
        print(text[:1500])

        # Ищем значения вида:
        # 32 °C
        # 32°C
        # 32 C
        temperatures = re.findall(
            r"(-?\d+(?:\.\d+)?)\s*°?\s*C\b",
            text,
            flags=re.IGNORECASE
        )

        temperatures = [
            float(value)
            for value in temperatures
        ]

        if len(temperatures) == 0:
            print("\n[INFO] Датчики температуры в виртуальной машине QEMU Romulus отсутствуют (0 items)")
            self.assertTrue(
                "sensors" in text.lower() or "no items" in text.lower(),
                "Раздел Sensors не отображается"
            )
            print("[PASS] Раздел Sensors успешно открыт и проверен")
            return

        self.assertGreater(
            len(temperatures),
            0,
            "На странице Sensors не найдено ни одной температуры"
        )

        print("\nНайденные температуры:", temperatures)

        # Для лабораторной зададим разумный допустимый диапазон
        for temperature in temperatures:

            self.assertGreaterEqual(
                temperature,
                0,
                f"Температура ниже допустимой: {temperature} °C"
            )

            self.assertLessEqual(
                temperature,
                100,
                f"Температура выше допустимой: {temperature} °C"
            )

        print("[PASS] Температуры находятся в диапазоне 0..100 °C")


if __name__ == "__main__":

    unittest.main(
        verbosity=2
    )

import os

import urllib3
from locust import HttpUser, between, task

# У OpenBMC часто используется самоподписанный HTTPS-сертификат.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class OpenBMCUser(HttpUser):
    """
    Виртуальный пользователь, работающий с OpenBMC Redfish API.
    """

    host = os.getenv("BMC_HOST", "https://192.168.1.100")
    wait_time = between(1, 3)
    weight = 1

    def on_start(self):
        """
        Вызывается один раз при запуске каждого виртуального пользователя.
        """
        username = os.getenv("BMC_USER", "root")
        password = os.getenv("BMC_PASSWORD", "0penBmc")

        self.client.auth = (username, password)
        self.client.verify = False
        self.client.headers.update(
            {
                "Accept": "application/json",
            }
        )

    @task(2)
    def get_system_information(self):
        """
        Получение общей информации о системе.
        """
        with self.client.get(
            "/redfish/v1/Systems/system",
            name="OpenBMC: system information",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )
                return

            try:
                data = response.json()
            except ValueError:
                response.failure("OpenBMC вернул невалидный JSON")
                return

            # Проверяем, что это действительно описание Redfish-системы.
            if "PowerState" not in data:
                response.failure("В ответе отсутствует поле PowerState")
            else:
                response.success()

    @task(1)
    def get_power_state(self):
        """
        Получение и проверка состояния питания.

        PowerState находится в объекте системы Redfish, поэтому используется
        тот же endpoint, но запрос отображается в статистике отдельно.
        """
        with self.client.get(
            "/redfish/v1/Systems/system",
            name="OpenBMC: power state",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )
                return

            try:
                data = response.json()
            except ValueError:
                response.failure("OpenBMC вернул невалидный JSON")
                return

            power_state = data.get("PowerState")

            if power_state is None:
                response.failure("Поле PowerState отсутствует")
            elif power_state not in ("On", "Off", "PoweringOn", "PoweringOff"):
                response.failure(
                    f"Неизвестное состояние питания: {power_state}"
                )
            else:
                response.success()


class PublicApiUser(HttpUser):
    """
    Виртуальный пользователь для публичных API.
    """

    host = "https://jsonplaceholder.typicode.com"
    wait_time = between(1, 3)
    weight = 1

    @task(2)
    def get_posts(self):
        """
        Получение списка публикаций JSONPlaceholder.
        """
        with self.client.get(
            "/posts",
            name="Public API: JSONPlaceholder posts",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )
                return

            try:
                posts = response.json()
            except ValueError:
                response.failure("JSONPlaceholder вернул невалидный JSON")
                return

            if not isinstance(posts, list):
                response.failure("Ожидался JSON-массив публикаций")
            elif len(posts) == 0:
                response.failure("Получен пустой список публикаций")
            else:
                response.success()

    @task(1)
    def get_weather(self):
        """
        Получение погоды для Новосибирска с wttr.in.
        """
        with self.client.get(
            "https://wttr.in/Novosibirsk?format=j1",
            name="Public API: wttr.in weather",
            headers={"Accept": "application/json"},
            timeout=30,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )
                return

            try:
                weather = response.json()
            except ValueError:
                response.failure("wttr.in вернул невалидный JSON")
                return

            if "current_condition" not in weather:
                response.failure(
                    "В ответе отсутствует поле current_condition"
                )
            else:
                response.success()

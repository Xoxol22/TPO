"""ЛР5: интеграционные тесты OpenBMC. Настройка через переменные окружения."""
import json
import logging
import math
import os
import re
import subprocess
import time
from urllib.parse import urlsplit

import pytest
import requests

LOG = logging.getLogger(__name__)
SYSTEM = '/redfish/v1/Systems/system'
SESSIONS = '/redfish/v1/SessionService/Sessions'


def number(value, label):
    assert isinstance(value, (int, float)) and not isinstance(value, bool), f'{label}: нет числового показания: {value!r}'
    assert math.isfinite(value), f'{label}: не конечное число'
    return float(value)


class Redfish:
    def __init__(self, url, user, password):
        self.url = url.rstrip('/')
        parsed = urlsplit(self.url)
        assert parsed.scheme == 'https' and parsed.hostname and not parsed.username, 'BMC_URL должен иметь вид https://host:port'
        self.user, self.password = user, password
        self.http = requests.Session()
        self.http.verify = os.getenv('BMC_CA_BUNDLE') or os.getenv('BMC_VERIFY_TLS', '1') != '0'
        self.timeout = float(os.getenv('HTTP_TIMEOUT', '15'))
        self.location = None
        self.login_response = None

    def request(self, method, path, **kwargs):
        # Не передаем токен на другой сервер, даже если API вернул внешнюю ссылку.
        parsed = urlsplit(path)
        assert not parsed.scheme and not parsed.netloc and path.startswith('/redfish/v1/'), f'Недопустимая ссылка API: {path}'
        try:
            response = self.http.request(method, self.url + path, timeout=self.timeout, allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            pytest.fail(f'{method} {path}: ошибка соединения ({type(exc).__name__})', pytrace=False)
        LOG.info('%s %s -> %s', method, path, response.status_code)
        return response

    def get(self, path):
        response = self.request('GET', path)
        assert response.status_code == 200, f'GET {path}: ожидался 200, получен {response.status_code}'
        try:
            data = response.json()
        except ValueError:
            pytest.fail(f'GET {path}: ответ не является JSON', pytrace=False)
        assert isinstance(data, dict), f'GET {path}: ожидался JSON-объект'
        return data

    def login(self):
        response = self.request('POST', SESSIONS, json={'UserName': self.user, 'Password': self.password})
        self.login_response = response
        token = response.headers.get('X-Auth-Token', '').strip()
        self.location = response.headers.get('Location')
        if token:
            self.http.headers['X-Auth-Token'] = token
        # Redfish возвращает 201 при создании сессии; некоторые версии — 200.
        assert response.status_code in (200, 201), f'Авторизация неуспешна: {response.status_code}'
        assert token, 'В ответе отсутствует X-Auth-Token'
        assert self.location, 'В ответе отсутствует Location для удаления сессии'

    def close(self):
        try:
            if self.location and self.http.headers.get('X-Auth-Token'):
                response = self.request('DELETE', self.location)
                assert response.status_code in (200, 204), f'Не удалось удалить сессию: {response.status_code}'
        finally:
            self.http.close()


@pytest.fixture(scope='session')
def redfish():
    url = os.getenv('BMC_URL')
    password = os.getenv('BMC_PASSWORD')
    if not url or not password:
        pytest.fail('Задайте BMC_URL и BMC_PASSWORD; см. README.md', pytrace=False)
    client = Redfish(url, os.getenv('BMC_USER', 'root'), password)
    try:
        client.login()
        yield client
    finally:
        client.close()


def members(client, path):
    seen = set()
    while path:
        assert path not in seen, 'Цикл в пагинации Redfish'
        seen.add(path)
        page = client.get(path)
        assert isinstance(page.get('Members'), list), f'{path}: отсутствует Members'
        for member in page['Members']:
            yield client.get(member['@odata.id'])
        path = page.get('Members@odata.nextLink')


def cpu_temperatures(client):
    """Thermal.Temperatures и современная коллекция Chassis.Sensors."""
    result = []
    for chassis in members(client, '/redfish/v1/Chassis'):
        thermal = chassis.get('Thermal', {}).get('@odata.id')
        if thermal:
            sensors = client.get(thermal).get('Temperatures', [])
            for sensor in sensors:
                if sensor.get('PhysicalContext') == 'CPU' or re.search(r'\bcpu\d*\b', sensor.get('Name', ''), re.I):
                    result.append(sensor)
        else:
            path = chassis.get('Sensors', {}).get('@odata.id')
            if not path:
                continue
            for sensor in members(client, path):
                if sensor.get('ReadingType') != 'Temperature':
                    continue
                if sensor.get('PhysicalContext') != 'CPU' and not re.search(r'\bcpu\d*\b', sensor.get('Name', ''), re.I):
                    continue
                assert sensor.get('ReadingUnits') == 'Cel', f"{sensor.get('Name')}: ожидались градусы Celsius"
                normalized = dict(sensor, ReadingCelsius=sensor.get('Reading'))
                for new, old in [('UpperCaution', 'UpperThresholdNonCritical'), ('UpperCritical', 'UpperThresholdCritical'), ('UpperFatal', 'UpperThresholdFatal'), ('LowerCaution', 'LowerThresholdNonCritical'), ('LowerCritical', 'LowerThresholdCritical'), ('LowerFatal', 'LowerThresholdFatal')]:
                    normalized[old] = sensor.get('Thresholds', {}).get(new, {}).get('Reading')
                result.append(normalized)
    assert result, 'В Redfish не найдены температурные датчики CPU'
    return result


def validate_temperature(sensor):
    name = sensor.get('Name', sensor.get('Id', 'CPU'))
    value = number(sensor.get('ReadingCelsius'), name)
    status = sensor.get('Status', {})
    assert status.get('State') == 'Enabled', f'{name}: датчик не активен: {status}'
    assert status.get('Health') == 'OK', f'{name}: состояние датчика: {status}'
    upper = []
    lower = []
    for severity in ('NonCritical', 'Critical', 'Fatal'):
        for prefix, target in [('Upper', upper), ('Lower', lower)]:
            field = f'{prefix}Threshold{severity}'
            if sensor.get(field) is not None:
                target.append(number(sensor[field], f'{name}.{field}'))
    # Redfish задает семантику порогов, но не универсальную норму CPU.
    assert upper, f'{name}: нет верхних порогов; норму подтвердить нельзя'
    assert value < min(upper), f'{name}: {value} °C >= верхнего порога {min(upper)} °C'
    if lower:
        assert value > max(lower), f'{name}: {value} °C <= нижнего порога {max(lower)} °C'
    LOG.info('%s: %.2f °C, верхний порог %.2f °C', name, value, min(upper))


def parse_ipmi(output):
    result = {}
    for line in output.splitlines():
        fields = [part.strip() for part in line.split('|')]
        if len(fields) < 4 or fields[2].lower() != 'degrees c':
            continue
        name, reading, units, state = fields[:4]
        assert name not in result, f'Неоднозначное имя IPMI: {name}'
        try:
            value = float(reading)
        except ValueError:
            value = None
        result[name] = (value, state.lower())
    assert result, 'IPMI не вернул температурных датчиков'
    return result


def read_ipmi(client):
    env = dict(os.environ, IPMI_PASSWORD=os.getenv('IPMI_PASSWORD', client.password), LC_ALL='C')
    command = ['ipmitool', '-I', 'lanplus', '-H', os.getenv('IPMI_HOST', urlsplit(client.url).hostname), '-p', os.getenv('IPMI_PORT', '623'), '-U', os.getenv('IPMI_USER', client.user), '-E', 'sensor', 'list']
    try:
        completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.fail(f'Не удалось выполнить ipmitool: {type(exc).__name__}', pytrace=False)
    assert completed.returncode == 0, f'ipmitool завершился с кодом {completed.returncode}; проверьте доступность IPMI и учетные данные'
    return parse_ipmi(completed.stdout)


def compare_sensors(sensors, ipmi, mapping, tolerance):
    assert math.isfinite(tolerance) and tolerance >= 0, 'Допуск должен быть конечным неотрицательным числом'
    used = set()
    for sensor in sensors:
        name = sensor['Name']
        target = mapping.get(name, name)
        assert target not in used, f'Несколько Redfish-датчиков сопоставлены с {target}'
        used.add(target)
        assert target in ipmi, f'{name}: нет датчика IPMI {target}; задайте CPU_SENSOR_MAP'
        value, state = ipmi[target]
        rf_value = number(sensor.get('ReadingCelsius'), name)
        ipmi_value = number(value, target)
        assert sensor.get('Status', {}).get('Health') == 'OK', f'{name}: Redfish Health != OK'
        assert sensor.get('Status', {}).get('State') == 'Enabled', f'{name}: Redfish State != Enabled'
        assert state == 'ok', f'{target}: состояние IPMI {state}'
        difference = abs(rf_value - ipmi_value)
        LOG.info('%s: Redfish=%.2f °C, IPMI=%.2f °C, разница=%.2f °C', name, rf_value, ipmi_value, difference)
        assert difference <= tolerance, f'{name}: расхождение {difference:.2f} °C > {tolerance} °C'
    extra = {name for name in ipmi if re.search(r'\bcpu\d*\b', name, re.I)} - used
    assert not extra, f'В IPMI есть несопоставленные датчики CPU: {sorted(extra)}'


def test_authentication(redfish):
    assert redfish.login_response.status_code in (200, 201), 'Неуспешное создание сессии'
    assert redfish.login_response.headers.get('X-Auth-Token', '').strip()


def test_system_information(redfish):
    system = redfish.get(SYSTEM)
    assert isinstance(system.get('Status'), dict), 'Отсутствует объект Status'
    assert system.get('PowerState') in ('On', 'Off', 'PoweringOn', 'PoweringOff'), 'Нет корректного PowerState'


def test_power_on(redfish):
    response = redfish.request('POST', SYSTEM + '/Actions/ComputerSystem.Reset', json={'ResetType': 'On'})
    assert response.status_code in (200, 202, 204), f'Команда включения отклонена: HTTP {response.status_code}'
    deadline = time.monotonic() + float(os.getenv('POWER_TIMEOUT', '120'))
    while True:
        state = redfish.get(SYSTEM).get('PowerState')
        if state == 'On':
            return
        assert time.monotonic() < deadline, f'Система не перешла в On, PowerState={state}'
        time.sleep(2)


def test_cpu_temperature(redfish):
    for sensor in cpu_temperatures(redfish):
        validate_temperature(sensor)


def test_cpu_redfish_matches_ipmi(redfish):
    sensors = cpu_temperatures(redfish)
    ipmi = read_ipmi(redfish)
    mapping = json.loads(os.getenv('CPU_SENSOR_MAP', '{}'))
    assert isinstance(mapping, dict), 'CPU_SENSOR_MAP должен быть JSON-объектом'
    compare_sensors(sensors, ipmi, mapping, float(os.getenv('CPU_TEMP_TOLERANCE', '2')))

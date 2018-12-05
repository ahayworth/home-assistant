"""
Support for Nest Temperature Sensors
"""
from datetime import timedelta, datetime
import logging
from pytz import utc

import voluptuous as vol

from homeassistant.const import (
    DEVICE_CLASS_TEMPERATURE, CONF_USERNAME, CONF_PASSWORD, TEMP_CELSIUS)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.util import Throttle, utcnow

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = cv.PLATFORM_SCHEMA.extend({
    vol.Required(CONF_USERNAME): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
})


async def async_setup_platform(hass, config, async_add_entities,
                               discovery_info=None):
    """Load Nest Kryptonite sensors."""
    session = async_get_clientsession(hass)

    throttle = timedelta(minutes=5)
    kryptonite_data = KryptoniteData(config, session, throttle)
    await kryptonite_data.update_now()
    all_sensors = []
    for sensor in kryptonite_data.data['kryptonite']:
        _LOGGER.debug("Found kryptonite device: %s", sensor)
        sensor_info = kryptonite_data.data['kryptonite'][sensor]
        kryptonite_sensor = KryptoniteSensor(sensor, sensor_info,
                                             kryptonite_data)
        all_sensors.append(kryptonite_sensor)

    async_add_entities(all_sensors, False)


class KryptoniteSensor(Entity):
    """Contains a Kryptonite Sensor."""

    def __init__(self, uuid, sensor_info, data):
        self._uuid = uuid
        self._device_class = DEVICE_CLASS_TEMPERATURE
        self._data = data

        wheres = {}
        structure_id = sensor_info['structure_id']
        for where in self._data.data['where'][structure_id]['wheres']:
            wheres[where['where_id']] = where['name']
        location = wheres[sensor_info['where_id']]
        self._name = 'Nest {} Temperature Sensor'.format(location)
        self._unit_of_measurement = TEMP_CELSIUS
        self._icon = 'mdi:thermometer'

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def device_class(self):
        """Return the device class."""
        return self._device_class

    @property
    def icon(self):
        """Icon to use in the frontend."""
        return self._icon

    @property
    def state(self):
        """Return the state of the device."""
        return self._data.data['kryptonite'][self._uuid]['current_temperature']

    @property
    def device_state_attributes(self):
        """Return additional attributes."""
        battery = self._data.data['kryptonite'][self._uuid]['battery_level']
        rcs_settings = self._data.data['rcs_settings']
        nest_thermostat = None
        fqdn = "kryptonite.{}".format(self._uuid)
        for thermostat_uuid in rcs_settings:
            if fqdn in rcs_settings[thermostat_uuid]['associated_rcs_sensors']:
                nest_thermostat = thermostat_uuid
                break

        return {
            'battery_level': battery,
            'active': (fqdn in
                       rcs_settings[nest_thermostat]['active_rcs_sensors']),
            'thermostat': nest_thermostat,
        }

    @property
    def unique_id(self):
        """Return the unique id of this entity."""
        return "kryptonite_{}".format(self._uuid)

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity."""
        return self._unit_of_measurement

    async def async_update(self):
        """Get the latest data."""
        await self._data.async_update()


class KryptoniteData:
    """Pull data from Nest."""

    def __init__(self, config, session, throttle):
        """Setup."""
        self._session = session
        self._config = config
        self._login_info = None
        self.data = {}
        self.async_update = Throttle(throttle)(self._async_update)

    async def _login(self):
        """Log in, return auth and transport."""
        login_url = 'https://home.nest.com/user/login'
        login_data = {
            'username': self._config[CONF_USERNAME],
            'password': self._config[CONF_PASSWORD],
        }

        response = await self._session.post(login_url, data=login_data)
        if response.status != 200:
            _LOGGER.error("Got response %s from Nest, giving up.",
                          response.status)
            return None

        user_data = await response.json()
        return {
            'access_token': user_data['access_token'],
            'transport_url': user_data['urls']['transport_url'],
            'user_id': user_data['userid'],
            'expires': user_data['expires_in'],
        }

    async def update_now(self):
        """Grab new data now."""
        fmt = '%a, %d-%b-%Y %H:%M:%S %Z'
        linfo = self._login_info
        if (not linfo
                or utc.localize(
                    datetime.strptime(linfo['expires'], fmt)) < utcnow()):

            login_info = await self._login()
            if not login_info:
                return

            self._login_info = login_info

        transport_url = self._login_info['transport_url'] \
            + '/v3/mobile/user.' + self._login_info['user_id']

        transport_headers = {
            'x-nl-user-id': self._login_info['user_id'],
            'authorization': 'Basic ' + self._login_info['access_token'],
        }

        response = await self._session.get(transport_url,
                                           headers=transport_headers)
        if response.status != 200:
            _LOGGER.error("Got response %s from Nest, giving up.",
                          response.status)
            return None

        self.data = await response.json()

    async def _async_update(self):
        """Grab new data."""
        await self.update_now()

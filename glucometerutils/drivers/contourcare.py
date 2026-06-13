# -*- coding: utf-8 -*-
#
# SPDX-FileCopyrightText: © 2019 The glucometerutils Authors
# SPDX-License-Identifier: MIT
"""Driver for Contour Care devices.

Supported features:
    - get readings (blood glucose), including comments;
    - get date and time;
    - get serial number and software version;
    - get device info (e.g. unit)

Expected device path: /dev/hidraw4 or similar HID device. Optional when using
HIDAPI.
"""

import datetime
from collections.abc import Generator
from typing import NoReturn, Optional

from glucometerutils import common
from glucometerutils.support import contourcare

_MEAL_CODES = {
    "T": common.Meal.NONE,
    "B": common.Meal.BEFORE,
    "A": common.Meal.AFTER,
    "F": common.Meal.FASTING,
}


class Device(contourcare.ContourCareHidDevice):
    """Glucometer driver for Contour Care devices."""

    def __init__(self, device: Optional[str]) -> None:
        super().__init__(device)

    def get_meter_info(self) -> common.MeterInfo:
        self._get_info_record()
        return common.MeterInfo(
            "Contour Care",
            serial_number=self.get_serial_number(),
            version_info=("Meter versions: " + self.get_version(),),
            native_unit=self.get_glucose_unit(),
        )

    def get_readings(self) -> Generator[common.AnyReading, None, None]:
        """
        Get reading dump from download data mode(all readings stored)
        This meter supports only blood samples
        """
        for parsed_record in self._get_multirecord():
            timestamp = self.parse_timestamp(parsed_record["datetime"])
            # Apparently the GlucoseReadings expect mg/dL values, so convert if necessary
            value = common.convert_glucose_unit(
                float(parsed_record["value"]),
                self.get_glucose_unit(),
                common.Unit.MG_DL,
            )
            meal = _MEAL_CODES[parsed_record["meal"][0]]
            yield common.GlucoseReading(
                timestamp,
                value,
                meal,
                measure_method=common.MeasurementMethod.BLOOD_SAMPLE,
            )

    def get_serial_number(self) -> str:
        return self._get_serial_number()

    def get_version(self):
        return self._get_version()

    def get_glucose_unit(self) -> common.Unit:
        if self._get_glucose_unit() == "0":
            return common.Unit.MG_DL
        else:
            return common.Unit.MMOL_L

    def _set_device_datetime(self, date: datetime.datetime) -> NoReturn:
        raise NotImplementedError

    def zero_log(self) -> NoReturn:
        raise NotImplementedError

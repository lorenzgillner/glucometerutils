# -*- coding: utf-8 -*-
#
# SPDX-FileCopyrightText: © 2026 The glucometerutils Authors
# SPDX-License-Identifier: MIT
"""Common routines to implement communication with Contour Care devices.

The communication is fairly similar to ContourUSB devices, but slightly different in a few places.
Since there is no official protocol document available

* glucodump code segments are developed by Anders Hammarquist
* code for the contourusb driver was developed by Arvanitis Christos
* additional edits by Lorenz Gillner
"""

import datetime
import enum
import re
from collections.abc import Generator
from typing import Optional

from glucometerutils import driver
from glucometerutils.support import hiddevice

_RECORD_FORMAT_RE = re.compile(
    r"\x02(?P<check>(?P<recno>[0-7])(?P<text>[^\x0d]*)\x0d(?P<end>[\x03\x17]))"  # haven't seen 0x03 yet
    r"(?P<checksum>[0-9A-F][0-9A-F])\x0d\x0a"
)

_HEADER_RECORD_RE = re.compile(
    r"^(?P<record_type>[A-Z])\|"
    r"(?P<escape_del>.)(?P<component_del>.)(?P<field_del>.)\|\|"
    r"(?P<gibberish>\w{6})\|"  # what is this supposed to be?
    r"(?P<product_code>\w+)"
    r"\^(?P<dig_ver>\d{2}\.\d{2})"
    r"\\(?P<anlg_ver>\d{2}\.\d{2})"
    r"\\(?P<agp_ver>\d{2}\.\d{2})"
    r"\^(?P<serial_num>\w+)\|"
    r"A=(?P<res_marking>\d)\^"
    r"C=(?P<config_bits>\d+)\^"
    r"R=(?P<ref_method>\d+)\^"
    r"S=(?P<internal>\d+)\^"
    r"U=(?P<unit>\d+)\^"
    r"V=(?P<lo_bound>\d{2})(?P<hi_bound>\d{3})\^"
    r"X=(?P<post_food_low>\d{3})(?P<pre_food_low>\d{3})"
    r"(?P<post_food_high>\d{3})(?P<pre_food_high>\d{3})\^"
    r"a=(?P<unclear>\d+)\^"
    r"J=(?P<dont_know>\d+)\|"
    r"(?P<total_recs>\d*)\|\|\|\|\|"
    r"P\|\d+\|"
    r"(?P<datetime>\d+)\|$"
)

_RESULT_RECORD_RE = re.compile(
    r"^(?P<record_type>[a-zA-Z])\|(?P<seq_num>\d+)\|\w*\^\w*\^\w*\^"
    r"(?P<test_id>\w+)\|(?P<value>\d+)\|(?P<unit>\w+\/\w+)\^"
    r"(?P<ref_method>[BPD])\|\|(?P<markers>[><BADISXCZ\/1-12]*)\|\|"
    r"(?P<datetime>\d+)"
)


class FrameError(Exception):
    pass


@enum.unique
class Mode(enum.Enum):
    """Operation modes."""

    ESTABLISH = enum.auto()
    DATA = enum.auto()
    PRECOMMAND = enum.auto()
    COMMAND = enum.auto()


class ContourCareHidDevice(driver.GlucometerDevice):
    """Base class implementing the Contour Care device."""

    blocksize = 64

    state: Optional[Mode] = None

    currecno: Optional[int] = None

    def __init__(self, usb_ids: tuple[int, int], device_path: Optional[str]) -> None:
        super().__init__(device_path)
        self._hid_session = hiddevice.HidSession(usb_ids, device_path)

    def read(self, r_size=blocksize):
        result = []

        while True:
            data = self._hid_session.read()
            dstr = data
            data_end_idx = data[3] + 4
            result.append(dstr[4:data_end_idx])
            if data[3] != self.blocksize - 4:
                break

        return b"".join(result)

    def write(self, data):
        data = b"\x00\x00\x00" + chr(len(data)).encode() + data.encode()
        pad_length = self.blocksize - len(data)
        data += pad_length * b"\x00"

        self._hid_session.write(data)

    def parse_header_record(self, text):
        header = _HEADER_RECORD_RE.search(text)

        self.field_del = header.group("field_del")
        self.escape_del = header.group("escape_del")
        self.component_del = header.group("component_del")

        self.product_code = header.group("product_code")
        self.dig_ver = header.group("dig_ver")
        self.anlg_ver = header.group("anlg_ver")
        self.agp_ver = header.group("agp_ver")

        self.serial_num = header.group("serial_num")
        self.res_marking = header.group("res_marking")
        self.config_bits = header.group("config_bits")
        self.ref_method = header.group("ref_method")
        self.internal = header.group("internal")

        # U limit
        self.unit = header.group("unit")
        self.lo_bound = header.group("lo_bound")
        self.hi_bound = header.group("hi_bound")

        # X field
        self.post_food_low = header.group("post_food_low")
        self.pre_food_low = header.group("pre_food_low")
        self.post_food_high = header.group("post_food_high")
        self.pre_food_high = header.group("pre_food_high")

        self.total = header.group("total_recs")

        # Datetime string in YYYYMMDDHHMMSS format
        self.datetime = header.group("datetime")

    def checksum(self, text):
        """
        Implemented by Anders Hammarquist for glucodump project
        More info: https://bitbucket.org/iko/glucodump/src/default/
        """
        checksum = hex(sum(ord(c) for c in text) % 256).upper().split("X")[1]
        return ("00" + checksum)[-2:]

    def checkframe(self, frame) -> Optional[str]:
        """
        Implemented by Anders Hammarquist for glucodump project
        More info: https://bitbucket.org/iko/glucodump/src/default/
        """
        match = _RECORD_FORMAT_RE.match(frame)
        if not match:
            raise FrameError("Couldn't parse frame", frame)

        recno = int(match.group("recno"))
        if self.currecno is None:
            self.currecno = recno

        if recno + 1 == self.currecno:
            return None

        if recno != self.currecno:
            raise FrameError(
                f"Bad recno, got {recno!r} expected {self.currecno!r}", frame
            )

        calculated_checksum = self.checksum(match.group("check"))
        received_checksum = match.group("checksum")
        if calculated_checksum != received_checksum:
            raise FrameError(
                f"Checksum error: received {received_checksum} expected {calculated_checksum}",
                frame,
            )

        self.currecno = (self.currecno + 1) % 8
        return match.group("text")

    def connect(self):
        """Connecting the device, nothing to be done.
        All process is hadled by hiddevice
        """
        pass

    def _get_info_record(self):
        self.currecno = None
        self.state = Mode.ESTABLISH
        try:
            while True:
                self.write("\x06")  # this one is different
                res = self.read()
                if res[0] == 0x04 and res[-1] == 0x05:
                    # we are connected and just got a header
                    header_record = res.decode()
                    stx = header_record.find("\x02")
                    if stx != -1:
                        result = _RECORD_FORMAT_RE.match(header_record[stx:-1]).group(
                            "text"
                        )
                        self.parse_header_record(result)
                    break
                else:
                    pass

        except FrameError as e:
            print("Frame error")
            raise e

        except Exception as e:
            print("Uknown error occured")
            raise e

    def disconnect(self):
        """Disconnect the device, nothing to be done."""
        pass

    # Some of the commands are also shared across devices that use this HID
    # protocol, but not many. Only provide here those that do seep to change
    # between them.
    def _get_version(self) -> str:
        """Return the software version of the device."""
        return self.dig_ver + " - " + self.anlg_ver + " - " + self.agp_ver

    def _get_serial_number(self) -> str:
        """Returns the serial number of the device."""
        return self.serial_num

    def _get_glucose_unit(self) -> str:
        """Return 0 for mg/dL, 1 for mmol/L"""
        return self.unit

    def get_datetime(self) -> datetime.datetime:
        datetime_str = self.datetime
        return datetime.datetime(
            int(datetime_str[0:4]),  # year
            int(datetime_str[4:6]),  # month
            int(datetime_str[6:8]),  # day
            int(datetime_str[8:10]),  # hour
            int(datetime_str[10:12]),  # minute
            0,
        )

    def sync(self) -> Generator[str, None, None]:
        """
        Sync with meter and yield received data frames
        FSM implemented by Anders Hammarquist's for glucodump
        More info: https://bitbucket.org/iko/glucodump/src/default/
        """
        self.state = Mode.ESTABLISH
        try:
            tometer = "\x04"
            result = None
            foo = 0
            while True:
                self.write(tometer)
                if result is not None and self.state == Mode.DATA:
                    yield result
                result = None
                data_bytes = self.read()
                data = data_bytes.decode()

                if self.state == Mode.ESTABLISH:
                    if data_bytes[-1] == 15:
                        # got a <NAK>, send <EOT>
                        tometer = chr(foo)
                        foo += 1
                        foo %= 256
                        continue
                    if data_bytes[-1] == 5:
                        # got an <ENQ>, send <ACK>
                        tometer = "\x06"
                        self.currecno = None
                        continue
                if self.state == Mode.DATA:
                    if data_bytes[-1] == 4:
                        # got an <EOT>, done
                        self.state = Mode.PRECOMMAND
                        break
                stx = data.find("\x02")
                if stx != -1:
                    # got <STX>, parse frame
                    try:
                        result = self.checkframe(data[stx:])
                        tometer = "\x06"
                        self.state = Mode.DATA
                    except FrameError:
                        tometer = "\x15"  # Couldn't parse, <NAK>
                else:
                    # Got something we don't understand, <NAK> it
                    tometer = "\x15"
        except Exception as e:
            raise e

    def parse_result_record(self, text: str) -> dict[str, str]:
        result = _RESULT_RECORD_RE.search(text)
        assert result is not None
        rec_text = result.groupdict()
        return rec_text

    def _get_multirecord(self) -> list[dict[str, str]]:
        """Queries for, and returns, "multirecords" results.

        Returns:
          (csv.reader): a CSV reader object that returns a record for each line
             in the record file.
        """
        records_arr = []
        for rec in self.sync():
            if rec[0] == "R":
                # parse using result record regular expression
                rec_text = self.parse_result_record(rec)
                # get dictionary to use in main driver module without import re

                records_arr.append(rec_text)
        # return csv.reader(records_arr)
        return records_arr  # array of groupdicts
